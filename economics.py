"""
Roční bilance a finanční úspory v Kč.

Spotřeba za rok se počítá metodou teplotních tříd (bin method): hodiny
typického roku se roztřídí podle venkovní teploty a podle toho, které
zóny jsou zrovna obsazené. Každá třída se spočítá jedním ustáleným
stavem a vynásobí počtem hodin. Proti hodinové simulaci je to stokrát
rychlejší a pro roční energii stejně přesné — setrvačnost budovy se
v součtu přes rok vyruší.

Útlum je výjimka: budova po vypnutí nevychladne hned. Za každou zónu se
proto spočítá, o kolik průměrně klesne teplota během typické přestávky
(noc, víkend), když se netopí — exponenciální chladnutí s časovou
konstantou zóny, oříznuté útlumovou teplotou. Tím se v ustáleném stavu
počítá „efektivní" útlumová teplota. Energie na ranní zátop je v tom
zahrnutá: co se v noci neztratilo, se ráno nemusí dohánět.

Ceny opatření jsou orientační (ceník servisních firem 2026 bez DPH);
u zakázky se přepíšou podle nabídky.
"""

import math
from collections import Counter
from dataclasses import dataclass, field

import building
import climate
import simulate

#: Orientační náklady opatření [Kč bez DPH] a co v nich je.
MEASURES = {
    "sensors": {
        "name": "Oprava čidel",
        "what": "Přesazení čidla rozdělovače do jímky s pastou, výměna převodníku "
                "čidla zpátečky šaten, kontrola proti kalibrovanému teploměru.",
        "cost": 8_500, "life": 10,
    },
    "actuator": {
        "name": "Výměna pohonu zónového ventilu",
        "what": "Nový pohon 0–10 V na ventil administrativy, kontrola kuželky, "
                "zkouška zdvihu v celém rozsahu.",
        "cost": 13_800, "life": 12,
    },
    "balancing": {
        "name": "Vyčištění sítka a hydraulické vyvážení",
        "what": "Vyčištění filtru ve větvi skladu, měření průtoků na 6 vyvažovacích "
                "ventilech, nastavení podle výpočtu, čerpadlo do návrhového bodu, "
                "protokol o vyvážení.",
        "cost": 37_200, "life": 15,
    },
    "pump": {
        "name": "Regulace čerpadla na Δp",
        "what": "Čidlo tlakové diference na konci páteře u skladu, kabeláž, "
                "parametrizace frekvenčního měniče čerpadla, úprava v BMS. "
                "Čerpadlo pak samo zpomaluje, když zóny přivírají.",
        "cost": 38_000, "life": 15,
    },
    "curve": {
        "name": "Nová ekvitermní křivka",
        "what": "Nastavení sklonu a posunu v regulátoru kotelny, týden sledování.",
        "cost": 4_500, "life": 10,
    },
    "setback": {
        "name": "Útlum mimo provoz",
        "what": "Časové programy zón v BMS podle směn, optimalizovaný ranní náběh.",
        "cost": 6_000, "life": 10,
    },
}

#: Opatření, která dávají smysl jen spolu. Vyčištění sítka a vyvážení samo
#: spotřebu zvýší (do studeného skladu konečně teče voda), vydělá se na
#: něm teprve snížením křivky, které umožní.
PACKAGES = [
    ("Opravy závad", ("sensors", "actuator")),
    ("Hydraulika a teplota vody", ("balancing", "pump", "curve")),
    ("Provozní režim", ("setback",)),
]

DISCOUNT = 0.04               # diskontní sazba pro čistou současnou hodnotu
ESCALATION = 0.03             # očekávaný roční růst cen energií
HORIZON = 10                  # hodnocené období [roky]


# --- útlum --------------------------------------------------------------------
def _unoccupied_periods(schedule):
    """Průměrná délka přestávky [h] a podíl neobsazených hodin v týdnu."""
    week = [schedule.occupied(d, h + 0.5) for d in range(7) for h in range(24)]
    if all(week):
        return 0.0
    runs, cur = [], 0
    for occ in week * 2:                  # dvakrát, ať se přestávka přes neděli nerozsekne
        if not occ:
            cur += 1
        elif cur:
            runs.append(cur)
            cur = 0
    runs = runs[len(runs) // 2:] or runs
    return sum(runs) / len(runs)


def setback_drop(zone, t_out, length_h):
    """
    O kolik [K] je zóna během přestávky v průměru chladnější než žádaná,
    když se nechá volně chladnout až k útlumové teplotě.
    """
    if length_h <= 0:
        return 0.0
    cap = zone.sp_occ - zone.sp_setback
    t_free = t_out + zone.gains_unocc / zone.ua
    span = max(zone.sp_occ - t_free, 0.0)
    tau = zone.tau_h
    n = 48
    total = 0.0
    for i in range(n):
        t = (i + 0.5) * length_h / n
        total += min(cap, span * (1 - math.exp(-t / tau)))
    return total / n


# --- roční bilance ---------------------------------------------------------------
@dataclass
class Annual:
    heat_kwh: float
    gas_kwh: float
    pump_kwh: float
    gas_cost: float
    el_cost: float
    co2_t: float
    heating_hours: int
    comfort: dict             # id zóny -> {under_kh, over_kh} (Kh v obsazených hodinách)
    by_temp: list = field(default_factory=list)   # rozpad podle venkovní teploty

    @property
    def cost(self):
        return self.gas_cost + self.el_cost

    def as_dict(self):
        return {**self.__dict__, "cost": self.cost}


_YEAR = None


def _year():
    global _YEAR
    if _YEAR is None:
        _YEAR = climate.typical_year()
    return _YEAR


def annual(net, tariffs=None):
    t = tariffs or building.TARIFFS
    # ranní náběh zkracuje přestávku — poslední hodiny před směnou se už topí
    lengths = {b.id: max(_unoccupied_periods(b.zone.schedule) - net.preheat_h, 0.0)
               for b in net.branches}
    bins = Counter()
    for h in _year():
        if not h.heating:
            continue
        occ = tuple(b.zone.schedule.occupied(h.weekday, h.hour + 0.5) for b in net.branches)
        bins[(round(h.t_out), occ)] += 1

    heat = gas = pump = 0.0
    comfort = {b.id: {"under_kh": 0.0, "over_kh": 0.0} for b in net.branches}
    by_temp = Counter()
    for (t_out, occ), hours in sorted(bins.items()):
        occ_map = {b.id: o for b, o in zip(net.branches, occ)}
        sp = {}
        for b, o in zip(net.branches, occ):
            if not o and net.setback_enabled:
                sp[b.id] = b.zone.sp_occ - setback_drop(b.zone, t_out, lengths[b.id])
            else:
                sp[b.id] = b.zone.sp_occ
        r = simulate.steady(net, float(t_out), occ_map, setpoints=sp)
        heat += r.heat_kw * hours
        gas += r.gas_kw * hours
        pump += r.pump_kw * hours
        by_temp[t_out] += r.gas_kw * hours
        for br, o in zip(r.branches, occ):
            if not o:
                continue
            z = net.by_id(br.id).zone
            comfort[br.id]["under_kh"] += max(z.sp_occ - 0.5 - br.t_room, 0.0) * hours
            comfort[br.id]["over_kh"] += max(br.t_room - z.sp_occ - 1.0, 0.0) * hours

    return Annual(
        heat_kwh=heat, gas_kwh=gas, pump_kwh=pump,
        gas_cost=gas * t["gas"], el_cost=pump * t["electricity"],
        co2_t=(gas * t["co2_gas"] + pump * t["co2_el"]) / 1000.0,
        heating_hours=sum(bins.values()), comfort=comfort,
        by_temp=[{"t_out": k, "gas_kwh": v} for k, v in sorted(by_temp.items())],
    )


# --- návratnost ------------------------------------------------------------------
def npv(invest, saving, years=HORIZON, rate=DISCOUNT, growth=ESCALATION):
    return -invest + sum(saving * (1 + growth) ** (y - 1) / (1 + rate) ** y
                         for y in range(1, years + 1))


def irr(invest, saving, years=HORIZON, growth=ESCALATION):
    if saving <= 0:
        return None
    lo, hi = -0.99, 10.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if npv(invest, saving, years, mid, growth) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def evaluate(plan, tariffs=None):
    """
    Spočítá dnešní stav a postupně každé opatření v pořadí plánu.

    Úspora opatření je rozdíl proti stavu po předchozím kroku — opatření se
    navzájem ovlivňují (po vyvážení už snížená křivka ušetří jinak než
    předtím), takže se nesmí počítat každé zvlášť a sčítat.
    """
    t = tariffs or building.TARIFFS
    base = annual(plan.baseline, t)
    rows = []
    prev = base
    for mid, label, net in plan.steps:
        cur = annual(net, t)
        m = MEASURES[mid]
        saving = prev.cost - cur.cost
        rows.append({
            "id": mid, "name": m["name"], "what": m["what"], "invest": m["cost"],
            "saving_kc": saving,
            "saving_gas_kwh": prev.gas_kwh - cur.gas_kwh,
            "saving_el_kwh": prev.pump_kwh - cur.pump_kwh,
            "saving_co2_t": prev.co2_t - cur.co2_t,
            "payback_y": m["cost"] / saving if saving > 1 else None,
            "npv": npv(m["cost"], saving, min(HORIZON, m["life"])),
            "cost_after": cur.cost,
            "comfort_after": cur.comfort,
        })
        prev = cur
    final = prev
    invest = sum(r["invest"] for r in rows)
    saving = base.cost - final.cost
    total = {
        "invest": invest, "saving_kc": saving,
        "saving_pct": saving / base.cost * 100 if base.cost else 0.0,
        "saving_gas_kwh": base.gas_kwh - final.gas_kwh,
        "saving_el_kwh": base.pump_kwh - final.pump_kwh,
        "saving_co2_t": base.co2_t - final.co2_t,
        "payback_y": invest / saving if saving > 1 else None,
        "npv": npv(invest, saving), "irr": irr(invest, saving),
        "cashflow": _cashflow(invest, saving),
    }
    packages = []
    for name, ids in PACKAGES:
        sel = [r for r in rows if r["id"] in ids]
        inv = sum(r["invest"] for r in sel)
        sav = sum(r["saving_kc"] for r in sel)
        packages.append({"name": name, "ids": list(ids), "invest": inv, "saving_kc": sav,
                         "payback_y": inv / sav if sav > 1 else None,
                         "npv": npv(inv, sav)})
    return {"baseline": base.as_dict(), "optimal": final.as_dict(),
            "measures": rows, "packages": packages, "total": total,
            "assumptions": {"discount": DISCOUNT, "escalation": ESCALATION,
                            "horizon": HORIZON, "tariffs": t,
                            "heating_hours": base.heating_hours}}


def _cashflow(invest, saving):
    out, acc = [{"year": 0, "cum": -invest}], -invest
    for y in range(1, HORIZON + 1):
        acc += saving * (1 + ESCALATION) ** (y - 1)
        out.append({"year": y, "cum": acc})
    return out
