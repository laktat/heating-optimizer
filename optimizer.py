"""
Návrh optimálních provozních parametrů.

Pořadí kroků není náhodné, je to pořadí, ve kterém se to dělá na stavbě:

  1) OPRAVY. Vadné čidlo, zaseklý pohon a ucpané sítko se musí odstranit
     dřív, než se cokoli nastavuje. Vyvažovat soustavu s ucpaným sítkem
     znamená vyvážit i to sítko — po vyčištění je všechno špatně.

  2) HYDRAULICKÉ VYVÁŽENÍ. Každá větev dostane vyvažovací ventil
     nastavený tak, aby při plně otevřených regulačních ventilech tekl
     přesně potřebný průtok. Nejvzdálenější (indexová) větev zůstane
     naplno — ona určuje, jaký tlak musí čerpadlo dodat; ostatní se
     přiškrtí o to, co mají navíc.

  3) ČERPADLO. Z vyvážení vyjde, jakou tlakovou diferenci potřebuje
     indexová větev. Čerpadlo pak drží právě tu na konci páteře, a když
     ventily přivírají, samo zpomalí.

  4) EKVITERMNÍ KŘIVKA. Pro každou venkovní teplotu se hledá nejnižší
     teplota vody, při které ještě všechny zóny dosáhnou žádané teploty.
     Křivka se těmito body proloží s rezervou. Teprve po vyvážení to dává
     smysl — v nevyvážené soustavě určuje křivku nejhorší větev.

  5) ÚTLUM. Mimo provoz se zóny pouštějí na útlumovou teplotu.

U větví bez regulace (šatny, sklad) se cílový průtok nebere z projektu,
ale z toho, jaký průtok dá při optimalizované křivce přesně žádanou
teplotu — jinak by větev s předimenzovanými tělesy přetápěla dál.
Proto se vyvážení a křivka počítají dvakrát po sobě.
"""

from dataclasses import dataclass, field

import building
import climate
import hydraulics
import simulate
import thermal
from building import balancing_kv, balancing_turns

CURVE_MARGIN = 1.5            # rezerva křivky nad nutnou teplotou [K]
PREHEAT_H = 2.0               # ranní náběh po útlumu [h]
T_GRID = [-14.0, -11.0, -8.0, -5.0, -2.0, 1.0, 4.0, 7.0, 10.0, 13.0]


def calibrated(diag):
    """
    Model soustavy tak, jak ji diagnostika poznala: zjištěné nastavení
    z obchůzky a BMS plus odhady skrytých závad. Na něm se počítá, kolik
    budova spotřebuje dnes.
    """
    net = diag.config.copy()
    net.header_sensor_error = diag.sensor_offset
    for bid, est in diag.estimated_faults.items():
        b = net.by_id(bid)
        for k, v in est.items():
            setattr(b, k, v)
    return net


def repaired(net):
    """Stejná soustava po odstranění závad (čidla, pohony, sítka)."""
    out = net.copy()
    out.header_sensor_error = 0.0
    for b in out.branches:
        b.valve_stuck = None
        b.kv_obstruction = None
    return out


# --- vyvážení ------------------------------------------------------------------
@dataclass
class BalanceResult:
    turns: dict               # nastavení vyvažovacích ventilů [otáčky]
    kv: dict
    targets: dict             # cílové průtoky [m³/h]
    index_branch: str
    dp_index: float           # tlaková diference na konci páteře [kPa]
    pump_head: float          # dopravní výška v návrhovém bodě [kPa]
    pump_speed: float         # otáčky v návrhovém bodě
    q_total: float
    check: dict               # ověření modelem: skutečný / cílový průtok
    warnings: list = field(default_factory=list)


def balance(net, targets=None):
    """
    Nastavení vyvažovacích ventilů metodou indexové větve.

    Vrací výsledek a novou soustavu s nastavenými ventily a regulací čerpadla.
    """
    targets = targets or {b.id: b.q_design for b in net.branches}
    n_nodes = len(net.segment_kv)

    def k_rest(b):
        """Součet 1/Kv² všeho ve větvi kromě vyvažovacího ventilu."""
        k = 1.0 / b.kv_pipe ** 2
        if b.kvs is not None:
            k += 1.0 / hydraulics.control_valve_kv(b.kvs, 1.0) ** 2
        if b.kv_obstruction is not None:
            k += 1.0 / b.kv_obstruction ** 2
        return k

    dp_req = {b.id: 100.0 * targets[b.id] ** 2 * (k_rest(b) + 1.0 / balancing_kv(b.bv_dn, 4.0) ** 2)
              for b in net.branches}
    seg_loss = []
    for k in range(1, n_nodes + 1):
        q = sum(targets[b.id] for b in net.branches if b.node >= k)
        seg_loss.append(hydraulics.loss(net.segment_kv[k - 1], q))

    def downstream(node):
        """Ztráta páteře od uzlu node až na konec."""
        return sum(seg_loss[node:])

    need = {b.id: dp_req[b.id] - downstream(b.node) for b in net.branches}
    index = max(need, key=need.get)
    dp_last = need[index]
    node_dp = [dp_last + downstream(k) for k in range(n_nodes + 1)]

    out = net.copy()
    turns, kvs, warnings = {}, {}, []
    for b in out.branches:
        q = targets[b.id]
        dp = node_dp[b.node]
        inv = dp / (100.0 * q ** 2) - k_rest(b)
        kv = inv ** -0.5 if inv > 0 else building.BALANCING_KV_MAX[b.bv_dn]
        t = round(balancing_turns(b.bv_dn, kv), 1)
        if t <= building.BALANCING_TURNS_MIN + 1e-9:
            warnings.append(f"{b.short}: vyvažovací ventil DN{b.bv_dn} nestačí přiškrtit, "
                            "je potřeba menší dimenze nebo clona.")
        b.bv_turns = t
        turns[b.id], kvs[b.id] = t, balancing_kv(b.bv_dn, t)

    q_total = sum(targets.values())
    head = node_dp[0] + hydraulics.loss(out.plant_kv, q_total)
    p = out.pump
    speed = ((head + p.r * q_total ** 2) / p.h0) ** 0.5
    if speed > 1.0:
        warnings.append(f"Čerpadlo nestačí: návrhový bod potřebuje {head:.0f} kPa "
                        f"při {q_total:.1f} m³/h.")
    out.pump_mode = "dp"
    out.dp_target = dp_last

    st_ = hydraulics.solve(out, speed=min(speed, 1.0))
    check = {b.id: st_.flows[b.id] / targets[b.id] for b in out.branches}
    res = BalanceResult(turns=turns, kv=kvs, targets=targets, index_branch=index,
                        dp_index=dp_last, pump_head=head, pump_speed=min(speed, 1.0),
                        q_total=q_total, check=check, warnings=warnings)
    return res, out


# --- ekvitermní křivka ---------------------------------------------------------------
def _satisfied(res, net):
    for r in res.branches:
        b = net.by_id(r.id)
        if b.kvs is not None:
            if r.valve > 0.95 or r.t_room < r.setpoint - 0.15:
                return False
        elif r.t_room < r.setpoint - 0.15:
            return False
    return True


def required_supply(net, t_out, lo=25.0, hi=None):
    """
    Nejnižší teplota rozdělovače, při které všechny zóny drží žádanou teplotu.
    Když to nejde ani na maximum kotelny, vrací maximum.
    """
    hi = net.sp_max if hi is None else hi
    if not _satisfied(simulate.steady(net, t_out, True, t_supply=hi), net):
        return hi
    for _ in range(14):
        mid = (lo + hi) / 2
        if _satisfied(simulate.steady(net, t_out, True, t_supply=mid), net):
            hi = mid
        else:
            lo = mid
    return hi


def _hours_weights():
    """Kolik hodin topné sezóny připadá na každý bod mřížky T_GRID."""
    w = [0.0] * len(T_GRID)
    for h in climate.typical_year():
        if h.heating:
            i = min(range(len(T_GRID)), key=lambda k: abs(T_GRID[k] - h.t_out))
            w[i] += 1
    return w


_WEIGHTS = None


def fit_curve(points):
    """
    Křivka 20 + sklon·(20 − t_e)^0,8 + posun, která leží nad všemi nutnými
    body (s rezervou) a přitom má v topné sezóně co nejnižší teplotu vody.
    """
    global _WEIGHTS
    if _WEIGHTS is None:
        _WEIGHTS = _hours_weights()
    best = None
    for i in range(0, 91):
        slope = 0.5 + i * 0.05
        shift = max(min(req + CURVE_MARGIN, building.design().sp_max)
                    - 20.0 - slope * max(20.0 - t, 0.0) ** 0.8
                    for t, req in points)
        cost = sum(w * (20.0 + slope * max(20.0 - t, 0.0) ** 0.8 + shift)
                   for w, (t, _) in zip(_WEIGHTS, points))
        if best is None or cost < best[0]:
            best = (cost, slope, shift)
    return round(best[1], 2), round(best[2], 1)


def optimize_curve(net):
    points = [(t, required_supply(net, t)) for t in T_GRID]
    slope, shift = fit_curve(points)
    return slope, shift, points


def thermal_targets(net, slope, shift):
    """
    Cílové průtoky: regulované větve projektový průtok, neregulované takový,
    který při dané křivce a výpočtové teplotě dá právě žádanou teplotu.
    """
    targets = {}
    for b in net.branches:
        if b.kvs is not None:
            targets[b.id] = b.q_design
            continue
        ts = net.curve(building.T_OUT_DESIGN, slope, shift) - CURVE_MARGIN
        p_req = thermal.zone_loss(b.zone, building.T_OUT_DESIGN, b.zone.sp_occ, True)
        q = thermal.required_flow(b, ts, b.zone.sp_occ, p_req)
        targets[b.id] = min(q * 1.05, b.q_design * 1.5) if q else b.q_design
    return targets


# --- celek -------------------------------------------------------------------------
@dataclass
class Plan:
    baseline: object          # dnešní stav (kalibrovaný model)
    optimal: object           # soustava se všemi opatřeními
    steps: list               # [(id opatření, popis, soustava po kroku)]
    balance: BalanceResult
    curve_points: list        # [(t_e, nutná teplota)]
    parameters: list          # tabulka dnes / navrženo


def plan(diag):
    base = calibrated(diag)
    fixed = repaired(base)

    # 1. průchod: projektové průtoky → křivka
    bal, net = balance(fixed)
    net.curve_shift, net.curve_slope = 0.0, 3.4
    slope, shift, _ = optimize_curve(net)
    # 2. průchod: neregulované větve na tepelně správný průtok → křivka znovu
    bal, net = balance(fixed, thermal_targets(fixed, slope, shift))
    net.curve_slope, net.curve_shift = slope, shift
    slope, shift, points = optimize_curve(net)

    # kroky v pořadí, v jakém se dělají na stavbě
    steps = []
    cur = base.copy()
    cur.header_sensor_error = 0.0
    steps.append(("sensors", "Oprava čidel", cur))
    cur = cur.copy()
    for b in cur.branches:
        b.valve_stuck = None
    steps.append(("actuator", "Výměna pohonu ventilu", cur))
    # Vyčištění sítka samo o sobě spotřebu zvýší — do studeného skladu
    # konečně poteče voda. Je to ale podmínka vyvážení, takže se počítá
    # s ním jako jeden krok. Po vyvážení se čerpadlo nastaví do návrhového
    # bodu (pevné otáčky) — tak to dělá každý, kdo soustavu vyvažuje.
    cur = cur.copy()
    for b in cur.branches:
        b.kv_obstruction = None
        b.bv_turns = bal.turns[b.id]
    cur.pump.speed = bal.pump_speed
    steps.append(("balancing", "Vyčištění sítka a hydraulické vyvážení", cur))
    cur = cur.copy()
    cur.pump_mode, cur.dp_target = "dp", bal.dp_index
    steps.append(("pump", "Regulace čerpadla na Δp", cur))
    cur = cur.copy()
    cur.curve_slope, cur.curve_shift = slope, shift
    steps.append(("curve", "Nová ekvitermní křivka", cur))
    cur = cur.copy()
    cur.setback_enabled = True
    cur.preheat_h = PREHEAT_H
    steps.append(("setback", "Útlum mimo provoz", cur))

    optimal = cur
    params = parameter_table(base, optimal, bal)
    return Plan(baseline=base, optimal=optimal, steps=steps, balance=bal,
                curve_points=points, parameters=params)


def parameter_table(base, opt, bal):
    rows = []

    def row(group, name, now, new, unit=""):
        rows.append({"group": group, "name": name, "now": now, "new": new, "unit": unit})

    row("Kotelna", "Sklon ekvitermní křivky", base.curve_slope, opt.curve_slope)
    row("Kotelna", "Posun ekvitermní křivky", base.curve_shift, opt.curve_shift, "K")
    for t in (-12.0, 0.0, 10.0):
        row("Kotelna", f"Teplota vody při {t:+.0f} °C", base.supply_temp(t), opt.supply_temp(t), "°C")
    row("Kotelna", "Chyba čidla rozdělovače", base.header_sensor_error, 0.0, "K")
    row("Útlum", "Ranní náběh před začátkem provozu", base.preheat_h, opt.preheat_h, "h")
    row("Čerpadlo", "Režim regulace",
        "pevné otáčky" if base.pump_mode == "fixed" else "Δp",
        f"Δp na konci páteře")
    row("Čerpadlo", "Otáčky v návrhovém bodě", base.pump.speed * 100, bal.pump_speed * 100, "%")
    row("Čerpadlo", "Žádaná Δp na konci páteře", None, bal.dp_index, "kPa")
    for b0, b1 in zip(base.branches, opt.branches):
        row("Vyvažovací ventily", f"{b1.short} (DN{b1.bv_dn})", b0.bv_turns, b1.bv_turns, "ot.")
    for b in opt.branches:
        z = b.zone
        row("Útlum", b.short,
            "vypnuto" if not base.setback_enabled else f"{z.sp_setback:.0f} °C",
            f"{z.sp_setback:.0f} °C mimo {_sched(z.schedule)}")
    return rows


def _sched(s):
    days = "po–pá" if s.days == (0, 1, 2, 3, 4) else ",".join(str(d) for d in s.days)
    if s.start <= 0 and s.end >= 24:
        return f"{days} (nepřetržitě)"
    return f"{days} {s.start:.0f}–{s.end:.0f} h"
