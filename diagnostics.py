"""
Diagnostika — proč budova spotřebovává víc, než musí.

Vstupem jsou jen data, která by se dala opravdu naměřit (teploty, povely
ventilů, kalorimetr na rozdělovači, otáčky čerpadla), a projekt. Skryté
příčiny ze simulátoru (building.as_found) diagnostika nevidí.

Postup je řetěz, kde každý krok stojí na předchozím — stejně jako když
technik hledá závadu na místě:

  1) ČIDLA. Dokud není jasné, kterým čidlům se dá věřit, nemá smysl nic
     počítat. Zamrzlé čidlo se pozná podle toho, že se nehýbe, i když se
     okolo všechno mění. Posunuté čidlo podle toho, že nesouhlasí s ostatními,
     která měří totéž (přívod na rozdělovači, na kalorimetru, ve větvích).

  2) VIRTUÁLNÍ PRŮTOKOMĚRY. Průtoky větví se většinou neměří. Dají se ale
     dopočítat z tepelné bilance prostoru: zóna ztrácí UA·(t_i − t_e), má
     zisky a mění svou teplotu, takže víme, kolik tepla do ní větev dodala.
     Výkon lomeno ochlazení vody dává průtok. Větev s vadným čidlem
     zpátečky se dopočítá z kalorimetru: co neprotekne ostatními, teče jí.

  3) MODEL PROTI MĚŘENÍ. Hydraulický model se zjištěným nastavením ventilů
     a otáček řekne, kolik by kudy mělo téct. Kde měření nesedí s modelem,
     je ve větvi něco, o čem projekt neví: zaseklý ventil teče, i když má
     povel zavřít; ucpané sítko pustí míň, než by mělo.

  4) PROVOZ. Až nakonec se hodnotí nastavení — křivka, čerpadlo, útlum —
     protože na vadných čidlech by i správná křivka vypadala špatně.
"""

import statistics as st
from dataclasses import dataclass, field

from scipy.optimize import curve_fit

import building
import hydraulics
from building import WATER_KW

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


@dataclass
class Finding:
    id: str
    category: str             # čidla | hydraulika | regulace | čerpadlo | komfort
    severity: str             # critical | warning | info
    title: str
    detail: str
    recommendation: str
    branch: str | None = None
    measure: str | None = None    # na které opatření v ekonomice navazuje
    evidence: list = field(default_factory=list)  # [(popis, hodnota, jednotka)]

    def as_dict(self):
        return {**self.__dict__,
                "evidence": [{"label": a, "value": b, "unit": c} for a, b, c in self.evidence]}


def _series(records, key):
    return [r[key] for r in records if key in r]


def _smooth(values, n=4):
    out = []
    for i in range(len(values)):
        w = values[max(0, i - n // 2): i + n // 2 + 1]
        out.append(sum(w) / len(w))
    return out


def _step_h(records):
    from datetime import datetime
    a = datetime.fromisoformat(records[0]["ts"])
    b = datetime.fromisoformat(records[1]["ts"])
    return (b - a).total_seconds() / 3600.0


def _occupied(records, branch):
    from datetime import datetime
    out = []
    for r in records:
        t = datetime.fromisoformat(r["ts"])
        out.append(branch.zone.schedule.occupied(t.weekday(), t.hour + t.minute / 60))
    return out


# --- 1) čidla ---------------------------------------------------------------
def check_sensors(records, net):
    findings, bad = [], set()
    supply_ref = _series(records, "meter.t_supply")
    ref_spread = st.pstdev(supply_ref)

    temp_keys = sorted({k for r in records[:1] for k in r
                        if k.endswith((".t_supply", ".t_return", ".t_room")) and not k.startswith("meter.")})
    for key in temp_keys:
        vals = _series(records, key)
        if st.pstdev(vals) < 0.02 and ref_spread > 1.0:
            bad.add(key)
            bid = key.split(".")[0]
            findings.append(Finding(
                id=f"sensor-stuck-{key}", category="čidla", severity="warning",
                title=f"Zamrzlé čidlo {key}",
                branch=bid if bid != "header" else None, measure="sensors",
                detail=(f"Čidlo ukazuje celý týden {vals[0]:.1f} °C bez jediné změny, "
                        f"přestože přívod se pohyboval v rozsahu {min(supply_ref):.0f}–"
                        f"{max(supply_ref):.0f} °C. Skutečné čidlo takhle nestojí — "
                        "typicky je vadný převodník nebo přerušený vodič a regulátor "
                        "drží poslední hodnotu."),
                recommendation="Vyměnit čidlo nebo převodník. Do té doby se větev "
                               "vyhodnocuje dopočtem z kalorimetru.",
                evidence=[("odchylka za týden", st.pstdev(vals), "K"),
                          ("hodnota", vals[0], "°C")]))

    # Přívod rozdělovače měří tři nezávislá místa: čidlo regulace, kalorimetr
    # a čidla na začátku každé větve. Většina má pravdu.
    header = _series(records, "header.t_supply")
    branch_supply = [_series(records, f"{b.id}.t_supply") for b in net.branches
                     if f"{b.id}.t_supply" not in bad]
    diff_meter = st.mean(h - m for h, m in zip(header, supply_ref))
    diff_branches = st.mean(
        h - st.median(col[i] for col in branch_supply) for i, h in enumerate(header))
    branch_vs_meter = st.mean(
        st.median(col[i] for col in branch_supply) - m for i, m in enumerate(supply_ref))
    if abs(diff_meter) > 1.0 and abs(diff_branches) > 0.8 and abs(branch_vs_meter) < 1.0:
        bad.add("header.t_supply")
        findings.append(Finding(
            id="sensor-offset-header", category="čidla", severity="critical",
            title="Čidlo teploty rozdělovače měří špatně",
            measure="sensors",
            detail=(f"Čidlo, podle kterého kotelna reguluje, ukazuje o {abs(diff_meter):.1f} K "
                    f"{'méně' if diff_meter < 0 else 'více'} než kalorimetr a o "
                    f"{abs(diff_branches):.1f} K {'méně' if diff_branches < 0 else 'více'} "
                    "než čidla na přívodech větví. Kalorimetr a větve se mezi sebou shodují, "
                    "takže chyba je v čidle regulace. Kotelna proto "
                    f"{'přetápí' if diff_meter < 0 else 'nedotápí'} — vyrábí vodu o "
                    f"{abs(diff_meter):.1f} K {'teplejší' if diff_meter < 0 else 'chladnější'}, "
                    "než ukazuje ekvitermní křivka."),
            recommendation="Zkontrolovat usazení čidla v jímce (teplovodivá pasta, hloubka), "
                           "případně čidlo vyměnit.",
            evidence=[("proti kalorimetru", diff_meter, "K"),
                      ("proti větvím (medián)", diff_branches, "K"),
                      ("větve proti kalorimetru", branch_vs_meter, "K")]))
    return findings, bad, diff_meter if "header.t_supply" in bad else 0.0


# --- 2) virtuální průtokoměry --------------------------------------------------
def estimate_flows(records, net, bad):
    """
    Průtok každé větve [m³/h] v každém vzorku z tepelné bilance prostoru.

    Vrací {id: [průtok nebo None]} a {id: [výkon]}.
    """
    dt = _step_h(records)
    t_out = _smooth(_series(records, "t_out"))
    flows, powers = {}, {}
    for b in net.branches:
        z = b.zone
        ti = _smooth(_series(records, f"{b.id}.t_room"), 6)
        occ = _occupied(records, b)
        ts = _series(records, f"{b.id}.t_supply")
        tr = _series(records, f"{b.id}.t_return")
        p_list, q_list = [], []
        for i in range(len(records)):
            lo, hi = max(i - 1, 0), min(i + 1, len(records) - 1)
            dti = (ti[hi] - ti[lo]) / ((hi - lo) * dt) if hi > lo else 0.0
            gains = z.gains_occ if occ[i] else z.gains_unocc
            p = max(z.ua * (ti[i] - t_out[i]) - gains + z.capacity * dti, 0.0)
            p_list.append(p)
            usable = f"{b.id}.t_return" not in bad and f"{b.id}.t_supply" not in bad
            dT = ts[i] - tr[i]
            q_list.append(p / (WATER_KW * dT) if usable and dT > 2.0 else (0.0 if usable else None))
        flows[b.id], powers[b.id] = q_list, p_list

    # Větve bez spolehlivé zpátečky: dopočet z kalorimetru.
    meter = _series(records, "meter.flow")
    missing = [b.id for b in net.branches if any(q is None for q in flows[b.id])]
    if len(missing) == 1:
        bid = missing[0]
        others = [k for k in flows if k != bid]
        flows[bid] = [max(meter[i] - sum(flows[k][i] for k in others), 0.0)
                      for i in range(len(records))]
    return flows, powers, missing


def _model_flows(records, net):
    """Kolik by podle modelu mělo kudy téct při povelech ventilů a otáčkách z BMS."""
    out = {b.id: [] for b in net.branches}
    for r in records:
        valves = {b.id: r.get(f"{b.id}.valve_cmd", 100.0) / 100.0
                  for b in net.branches if b.kvs is not None}
        st_ = hydraulics.solve(net, valves, speed=r["pump.speed"] / 100.0)
        for b in net.branches:
            out[b.id].append(st_.flows[b.id])
    return out


def observed_config(records, survey=None):
    """
    Co se o soustavě dá zjistit obchůzkou a z BMS — bez skrytých závad.

    Nastavení vyvažovacích ventilů se odečte na místě a předá v survey
    ({"bv_turns": {id: otáčky}}); bez něj se bere stav, jaký technik
    na tomhle závodě našel — všechny naplno. Otáčky čerpadla a ekvitermní
    křivka se vyčtou z BMS.
    """
    net = building.design()
    turns = (survey or {}).get("bv_turns", {})
    for b in net.branches:
        b.bv_turns = turns.get(b.id, building.BALANCING_TURNS_MAX)
    net.pump.speed = st.mean(_series(records, "pump.speed")) / 100.0
    slope, shift = fit_curve(records)
    net.curve_slope, net.curve_shift = slope, shift
    sp = {b.id: set(_series(records, f"{b.id}.setpoint")) for b in net.branches}
    net.setback_enabled = any(len(v) > 1 for v in sp.values())
    return net


def fit_curve(records):
    """Ekvitermní křivka, na kterou kotelna skutečně jede (z žádané a venkovní)."""
    lo, hi = building.design().sp_min, building.design().sp_max
    pts = [(r["t_out"], r["header.sp_calc"]) for r in records
           if lo + 0.05 < r["header.sp_calc"] < hi - 0.05]
    t, sp = [p[0] for p in pts], [p[1] for p in pts]

    def f(x, slope, shift):
        return [20.0 + slope * max(20.0 - v, 0.0) ** 0.8 + shift for v in x]

    (slope, shift), _ = curve_fit(f, t, sp, p0=(3.0, 0.0))
    return float(slope), float(shift)


# --- 3) a 4) celé vyhodnocení ----------------------------------------------------
@dataclass
class Diagnosis:
    findings: list
    branches: list            # tabulka větví pro rozhraní
    config: object            # zjištěná konfigurace (building.Network)
    sensor_offset: float      # odhadnutá chyba čidla rozdělovače [K]
    estimated_faults: dict    # co se o skrytých závadách zjistilo — pro kalibraci modelu
    summary: dict

    def as_dict(self):
        return {
            "findings": [f.as_dict() for f in self.findings],
            "branches": self.branches,
            "sensor_offset": self.sensor_offset,
            "estimated_faults": self.estimated_faults,
            "summary": self.summary,
            "config": {
                "curve_slope": self.config.curve_slope,
                "curve_shift": self.config.curve_shift,
                "pump_speed": self.config.pump.speed,
                "setback_enabled": self.config.setback_enabled,
            },
        }


def diagnose(records, survey=None):
    findings = []
    cfg = observed_config(records, survey)
    net = cfg

    sensor_findings, bad, offset = check_sensors(records, net)
    findings += sensor_findings

    flows, powers, derived = estimate_flows(records, net, bad)
    model = _model_flows(records, net)
    meter = _series(records, "meter.flow")
    estimated = {}
    rows = []

    for b in net.branches:
        q = flows[b.id]
        qm = model[b.id]
        q_mean = st.mean(q)
        qm_mean = st.mean(qm)
        ti = _series(records, f"{b.id}.t_room")
        sp = _series(records, f"{b.id}.setpoint")
        occ = _occupied(records, b)
        ts = _series(records, f"{b.id}.t_supply")
        tr = _series(records, f"{b.id}.t_return")
        cmd = _series(records, f"{b.id}.valve_cmd")
        occ_idx = [i for i, o in enumerate(occ) if o]
        over = st.mean(max(ti[i] - sp[i], 0.0) for i in occ_idx) if occ_idx else 0.0
        under_h = sum(1 for i in occ_idx if ti[i] < sp[i] - 0.5) * _step_h(records)
        over_h = sum(1 for i in occ_idx if ti[i] > sp[i] + 1.0) * _step_h(records)
        ratio = q_mean / b.q_design
        rows.append({
            "id": b.id, "name": b.name, "short": b.short, "kind": b.kind, "control": b.control,
            "node": b.node,
            "flow_est": q_mean, "flow_model": qm_mean, "flow_design": b.q_design,
            "ratio": ratio, "model_ratio": q_mean / qm_mean if qm_mean > 1e-3 else None,
            "t_room": st.mean(ti), "setpoint": st.mean(sp[i] for i in occ_idx) if occ_idx else sp[0],
            "overheat": over, "over_hours": over_h, "under_hours": under_h,
            "t_return": st.mean(tr), "delta_t": st.mean(a - c for a, c in zip(ts, tr)),
            "power": st.mean(powers[b.id]),
            "valve_cmd": st.mean(cmd) if cmd else None,
            "derived": b.id in derived,
        })

    # --- rekonstrukce zpátečky větve s vadným čidlem (směšovací bilance)
    for bid in derived:
        others = [b for b in net.branches if b.id != bid]
        m_tr = _series(records, "meter.t_return")
        rec_tr = []
        for i in range(len(records)):
            qb = flows[bid][i]
            if qb < 0.2:
                continue
            s = sum(flows[o.id][i] * records[i][f"{o.id}.t_return"] for o in others)
            rec_tr.append((meter[i] * m_tr[i] - s) / qb)
        if rec_tr:
            row = next(r for r in rows if r["id"] == bid)
            row["t_return"] = st.mean(rec_tr)
            row["delta_t"] = st.mean(_series(records, f"{bid}.t_supply")) - row["t_return"]
            for f in findings:
                if f.id == f"sensor-stuck-{bid}.t_return":
                    f.evidence.append(("zpátečka dopočtená ze směšování", st.mean(rec_tr), "°C"))
                    f.detail += (f" Ze směšovací bilance na sběrači vychází skutečná zpátečka "
                                 f"kolem {st.mean(rec_tr):.1f} °C.")

    for b in net.branches:
        row = next(r for r in rows if r["id"] == b.id)
        q, qm = flows[b.id], model[b.id]
        q_mean, qm_mean, ratio = row["flow_est"], row["flow_model"], row["ratio"]
        over, over_h, under_h = row["overheat"], row["over_hours"], row["under_hours"]
        cmd = _series(records, f"{b.id}.valve_cmd")

        # --- zaseklý ventil: povel zavřít, ale voda teče dál
        if b.kvs is not None and cmd:
            closed = [i for i, c in enumerate(cmd) if c < 5.0]
            if len(closed) > 0.3 * len(cmd):
                q_closed = st.mean(q[i] for i in closed)
                if q_closed > 0.2 * b.q_design:
                    # dopočet skutečného otevření z průtoku a tlaku z modelu
                    dp = st.mean(hydraulics.solve(net, {b.id: 1.0}).branch_dp[b.id] for _ in [0])
                    from simulate import _valve_for_flow
                    u_est = _valve_for_flow(b, q_closed, dp)
                    estimated[b.id] = {"valve_stuck": round(u_est, 2)}
                    findings.append(Finding(
                        id=f"valve-stuck-{b.id}", category="regulace", severity="critical",
                        title=f"Zaseklý zónový ventil — {b.name}",
                        branch=b.id, measure="actuator",
                        detail=(f"Regulátor posílá ventilu po {len(closed) / len(cmd) * 100:.0f} % "
                                f"času povel pod 5 %, a přesto větví teče {q_closed:.1f} m³/h "
                                f"({q_closed / b.q_design * 100:.0f} % návrhu). V prostoru je "
                                f"průměrně o {over:.1f} K víc, než je žádaná. Ventil na povel "
                                f"nereaguje — podle průtoku stojí kolem {u_est * 100:.0f} % otevření."),
                        recommendation="Vyměnit pohon ventilu, zkontrolovat, jestli kuželka "
                                       "není zapečená. Po výměně ověřit zdvih celým rozsahem.",
                        evidence=[("povel ventilu (průměr)", st.mean(cmd), "%"),
                                  ("průtok při povelu zavřít", q_closed, "m³/h"),
                                  ("odhad skutečného otevření", u_est * 100, "%"),
                                  ("přetápění při provozu", over, "K")]))
                    continue

            # povel otevřít naplno, ale teče mnohem míň, než by při něm mělo
            opened = [i for i, c in enumerate(cmd) if c > 90.0]
            if len(opened) > 0.3 * len(cmd):
                q_open = st.mean(q[i] for i in opened)
                qm_open = st.mean(qm[i] for i in opened)
                if qm_open > 0.1 and q_open < 0.6 * qm_open:
                    findings.append(Finding(
                        id=f"valve-blocked-{b.id}", category="regulace", severity="critical",
                        title=f"Ventil nedotevírá — {b.name}",
                        branch=b.id, measure="actuator",
                        detail=(f"Regulátor drží povel nad 90 % po {len(opened) / len(cmd) * 100:.0f} % "
                                f"času, prostor je {under_h:.0f} h pod žádanou, ale větví teče jen "
                                f"{q_open:.1f} m³/h místo {qm_open:.1f} m³/h, které by plně otevřený "
                                "ventil pustil. Pohon nedojede, nebo je ventil zanesený."),
                        recommendation="Zkontrolovat zdvih pohonu a kuželku ventilu.",
                        evidence=[("povel ventilu (průměr)", st.mean(cmd), "%"),
                                  ("průtok při povelu otevřít", q_open, "m³/h"),
                                  ("průtok podle modelu", qm_open, "m³/h")]))
                    continue

        # --- dodatečný odpor: teče výrazně míň, než říká model
        if b.kvs is None and qm_mean > 0.1 and q_mean / qm_mean < 0.8:
            # Tlak na uzlu závisí na tom, kolik větev bere — s překážkou bere míň
            # a tlak je vyšší. Odhad Kv překážky se proto zpřesňuje iterací.
            trial = net.copy()
            tb = trial.by_id(b.id)
            known = 1 / b.kv_pipe ** 2 + 1 / b.kv_balancing ** 2
            kv_obs = None
            for _ in range(12):
                dp = st.mean(hydraulics.solve(
                    trial, {k.id: r.get(f"{k.id}.valve_cmd", 100.0) / 100.0
                            for k in trial.branches if k.kvs is not None},
                    speed=r["pump.speed"] / 100.0).branch_dp[b.id] for r in records[::24])
                inv = dp / (100.0 * q_mean ** 2) - known
                if inv <= 0:
                    break
                kv_obs = inv ** -0.5
                tb.kv_obstruction = kv_obs
            if kv_obs:
                estimated[b.id] = {"kv_obstruction": round(kv_obs, 2)}
            findings.append(Finding(
                id=f"obstruction-{b.id}", category="hydraulika", severity="critical",
                title=f"Ve větvi je odpor navíc — {b.name}",
                branch=b.id, measure="balancing",
                detail=(f"Podle tepelné bilance prostoru teče větví {q_mean:.1f} m³/h, ale "
                        f"hydraulický model při zjištěném nastavení ventilů a otáčkách čerpadla "
                        f"čeká {qm_mean:.1f} m³/h. Chybí {(1 - q_mean / qm_mean) * 100:.0f} % — "
                        "to samotná vzdálenost od kotelny nevysvětlí, ta je v modelu započtená. "
                        "Ve větvi je překážka, o které projekt neví: typicky zanesené sítko "
                        "filtru nebo přivřená uzavírací armatura. Prostor má "
                        f"{under_h:.0f} h v provozu pod žádanou teplotou."),
                recommendation="Vyčistit sítko filtru, projít uzavírací armatury ve větvi. "
                               "Teprve potom větev vyvažovat — jinak se vyváží i ucpané sítko.",
                evidence=[("průtok podle bilance", q_mean, "m³/h"),
                          ("průtok podle modelu", qm_mean, "m³/h"),
                          ("návrhový průtok", b.q_design, "m³/h"),
                          ("odhad Kv překážky", kv_obs or 0.0, "m³/h"),
                          ("hodin pod žádanou", under_h, "h")]))
            continue

        # --- nevyvážená větev bez regulace: teče jí víc, než má, a přetápí
        if b.kvs is None and ratio > 1.3:
            findings.append(Finding(
                id=f"overflow-{b.id}", category="hydraulika", severity="warning",
                title=f"Nevyvážená větev, teče jí moc vody — {b.name}",
                branch=b.id, measure="balancing",
                detail=(f"Větví teče {q_mean:.1f} m³/h, tedy {ratio * 100:.0f} % návrhu. "
                        f"Leží blízko kotelny (uzel {b.node}), má k dispozici skoro celý tlak "
                        "čerpadla a vyvažovací ventil je naplno. Voda se ve větvi skoro neochladí "
                        f"(ochlazení {row['delta_t']:.1f} K místo návrhových 20 K) a prostor "
                        f"je průměrně o {over:.1f} K přetopený. Teplou zpátečkou zároveň brání "
                        "kotli kondenzovat." +
                        (" Zpátečka této větve je dopočtená z kalorimetru, vlastní čidlo je vadné."
                         if b.id in derived else "")),
                recommendation="Přiškrtit vyvažovací ventil na hodnotu z optimalizace.",
                evidence=[("průtok", q_mean, "m³/h"), ("návrh", b.q_design, "m³/h"),
                          ("poměr k návrhu", ratio * 100, "%"),
                          ("přetápění", over, "K"),
                          ("hodin přetopeno", over_h, "h")]))


    # --- čerpadlo
    speed = _series(records, "pump.speed")
    load = _series(records, "meter.power")
    if st.pstdev(speed) < 0.5 and min(load) < 0.7 * max(load):
        q_tot = st.mean(meter)
        findings.append(Finding(
            id="pump-fixed", category="čerpadlo", severity="warning",
            title="Čerpadlo jede na pevné otáčky bez ohledu na odběr",
            measure="pump",
            detail=(f"Otáčky čerpadla stojí celý týden na {st.mean(speed):.0f} %, přestože "
                    f"odběr tepla se pohyboval mezi {min(load):.0f} a {max(load):.0f} kW a "
                    "zónové ventily přivíraly. Čerpadlo tlačí vodu proti přivřeným ventilům: "
                    f"celkový průtok {q_tot:.1f} m³/h, v blízkých větvích tlaková diference "
                    f"kolem {hydraulics.solve(net).node_dp[1]:.0f} kPa, kterou musí ventily "
                    "zmařit."),
            recommendation="Přepnout čerpadlo na regulaci konstantní tlakové diference "
                           "s čidlem na konci páteře (u skladu).",
            evidence=[("otáčky", st.mean(speed), "%"),
                      ("příkon", st.mean(_series(records, "pump.power")), "kW"),
                      ("rozsah odběru", f"{min(load):.0f}–{max(load):.0f}", "kW")]))

    # --- ekvitermní křivka a regulační ventily
    # Kdyby byla voda tak akorát, aspoň v nejchladnějších hodinách by se
    # některý zónový ventil otevřel skoro naplno. Rozhoduje 95. percentil
    # povelu, ne průměr — průměr stáhne dolů noc a odpoledne.
    def p95(v):
        v = sorted(v)
        return v[int(0.95 * (len(v) - 1))]

    zone_cmds = {b.id: st.mean(_series(records, f"{b.id}.valve_cmd"))
                 for b in net.branches if b.kvs is not None and b.id not in estimated}
    zone_p95 = {k: p95(_series(records, f"{k}.valve_cmd")) for k in zone_cmds}
    design_cfg = building.design()
    shift_vs = cfg.curve_shift - design_cfg.curve_shift
    if zone_cmds and max(zone_p95.values()) < 75.0:
        findings.append(Finding(
            id="curve-high", category="regulace", severity="warning",
            title="Topná voda je teplejší, než budova potřebuje",
            measure="curve",
            detail=(f"Kotelna jede na křivku se sklonem {cfg.curve_slope:.2f} a posunem "
                    f"{cfg.curve_shift:+.1f} K (projekt {design_cfg.curve_slope:.2f} / "
                    f"{design_cfg.curve_shift:+.1f} K). Regulované zóny přitom za celý mrazivý "
                    "týden otevřely ventily v průměru jen na "
                    + ", ".join(f"{net.by_id(k).short} {v:.0f} %"
                                for k, v in zone_cmds.items())
                    + ". Teplou vodu musí škrtit a neregulované větve přetápí. Posun křivky "
                    "nahoru je typická reakce na studený sklad — jenže sklad je studený kvůli "
                    "hydraulice, ne kvůli teplotě vody."),
            recommendation="Nejdřív odstranit hydraulické závady a vyvážit, potom snížit "
                           "křivku na hodnoty z optimalizace.",
            evidence=[("zjištěný sklon", cfg.curve_slope, ""),
                      ("zjištěný posun", cfg.curve_shift, "K"),
                      ("posun proti projektu", shift_vs, "K"),
                      ("nejvíc otevřená zóna (95. percentil)", max(zone_p95.values()), "%")]))

    # --- útlum
    if not cfg.setback_enabled:
        with_sched = [b for b in net.branches if b.zone.schedule.occupied_share() < 0.9]
        findings.append(Finding(
            id="setback-off", category="regulace", severity="warning",
            title="Útlum mimo provoz je vypnutý",
            measure="setback",
            detail=("Žádané teploty se celý týden nemění, i v noci a o víkendu, kdy jsou "
                    "prostory prázdné: " + ", ".join(b.short for b in with_sched) +
                    ". Budova se tak vytápí na plný komfort i v době, kdy v ní nikdo není."),
            recommendation="Zapnout útlum podle provozní doby každé zóny. Ranní náběh "
                           "posunout tak, aby byla teplota dosažená na začátku směny.",
            evidence=[("zón s rozvrhem", len(with_sched), ""),
                      ("podíl neobsazených hodin (adm.)",
                       (1 - net.by_id("adm").zone.schedule.occupied_share()) * 100, "%")]))

    # --- kondenzace
    tr_hdr = _series(records, "meter.t_return")
    hot = sum(1 for t in tr_hdr if t > 45.0) / len(tr_hdr) * 100
    if st.mean(tr_hdr) > 42.0:
        findings.append(Finding(
            id="return-hot", category="hydraulika", severity="info",
            title="Kotle málo kondenzují — zpátečka je teplá",
            measure="balancing",
            detail=(f"Průměrná zpátečka na sběrači je {st.mean(tr_hdr):.1f} °C, "
                    f"{hot:.0f} % času nad 45 °C. Kondenzační kotel má nejvyšší účinnost se zpátečkou "
                    "pod 40 °C, kolem 55 °C kondenzace končí úplně. Teplou "
                    "zpátečku dělají hlavně přetékající větve bez regulace — voda jimi "
                    "proteče a skoro se neochladí."),
            recommendation="Vyvážit větve a snížit křivku — obojí zpátečku ochladí.",
            evidence=[("průměrná zpátečka", st.mean(tr_hdr), "°C"),
                      ("čas nad 45 °C", hot, "%")]))

    # --- komfort: zóny pod žádanou, které nemají jiné vysvětlení
    for row in rows:
        if row["under_hours"] > 8 and not any(f.branch == row["id"] for f in findings):
            findings.append(Finding(
                id=f"cold-{row['id']}", category="komfort", severity="warning",
                title=f"Prostor nedosahuje žádané teploty — {row['name']}",
                branch=row["id"], measure="balancing",
                detail=f"{row['under_hours']:.0f} h provozu pod žádanou teplotou.",
                recommendation="Ověřit průtok větví a výkon těles.",
                evidence=[("hodin pod žádanou", row["under_hours"], "h")]))

    findings.sort(key=lambda f: SEVERITY_ORDER[f.severity])
    summary = {
        "records": len(records),
        "period": (records[0]["ts"], records[-1]["ts"]),
        "t_out_mean": st.mean(_series(records, "t_out")),
        "t_out_min": min(_series(records, "t_out")),
        "heat_mean": st.mean(load),
        "gas_kwh": sum(_series(records, "boiler.gas_kw")) * _step_h(records),
        "heat_kwh": sum(load) * _step_h(records),
        "pump_kwh": sum(_series(records, "pump.power")) * _step_h(records),
        "t_return_mean": st.mean(tr_hdr),
        "counts": {s: sum(1 for f in findings if f.severity == s) for s in SEVERITY_ORDER},
        "derived_branches": derived,
        "bad_sensors": sorted(bad),
    }
    return Diagnosis(findings=findings, branches=rows, config=cfg, sensor_offset=offset,
                     estimated_faults=estimated, summary=summary)
