"""
Simulace celé soustavy: hydraulika + tělesa + prostory + regulace.

Dva režimy:

    steady()   ustálený stav pro jednu venkovní teplotu — interaktivní
               simulace v rozhraní, roční bilance metodou teplotních tříd
               a hledání optimálních parametrů
    week()     dynamický průběh týdne po čtvrthodinách s regulátory
               a setrvačností prostorů; z něj vznikají „naměřená" data

Hydraulika a teplo jsou provázané: zónový ventil přivře, protože je
v místnosti teplo — tím se změní tlaky v páteři a ostatním větvím
přiteče víc. Proto se hydraulika a regulace řeší střídavě, dokud se
otevření ventilů neustálí.
"""

import random
from dataclasses import dataclass, field

from scipy.optimize import brentq

import hydraulics
import thermal
from building import AS_FOUND_SENSOR_FAULTS, WATER_KW

BOILER_KW = 800.0


def pipe_loss(net, ts, tr):
    """
    Ztráta rozvodů v nevytápěných prostorech [kW]. Roste s teplotou vody —
    i proto se vyplácí topit nejnižší teplotou, která ještě stačí.
    """
    return max(net.pipe_ua * ((ts + tr) / 2 - net.pipe_ambient), 0.0)


def effective_setpoint(branch, occupied, setback):
    z = branch.zone
    if occupied or not setback:
        return z.sp_occ
    return z.sp_setback


def _valve_for_flow(branch, q_req, dp):
    """Otevření regulačního ventilu, při kterém větev pustí průtok q_req."""
    if q_req <= 1e-9:
        return 0.0
    if dp <= 1e-6:
        return 1.0
    kv_need = q_req / (dp / 100.0) ** 0.5
    inv = 1.0 / kv_need ** 2 - 1.0 / branch.kv_pipe ** 2 - 1.0 / branch.kv_balancing ** 2
    if branch.kv_obstruction is not None:
        inv -= 1.0 / branch.kv_obstruction ** 2
    if inv <= 0:
        return 1.0
    kv_cv = inv ** -0.5
    if kv_cv >= branch.kvs:
        return 1.0
    f = lambda u: hydraulics.control_valve_kv(branch.kvs, u) - kv_cv
    if f(0.0) >= 0:
        return 0.0
    return brentq(f, 0.0, 1.0, xtol=1e-5)


def _pump_speed(net, valves):
    """Otáčky, při kterých čerpadlo udrží žádanou Δp na posledním uzlu."""
    if net.pump_mode != "dp" or net.dp_target is None:
        return net.pump.speed
    last = len(net.segment_kv)

    def f(s):
        return hydraulics.solve(net, valves, speed=s).node_dp[last] - net.dp_target

    if f(1.0) <= 0:
        return 1.0
    if f(0.25) >= 0:
        return 0.25
    return brentq(f, 0.25, 1.0, xtol=1e-4)


@dataclass
class BranchResult:
    id: str
    flow: float
    flow_design: float
    ratio: float
    dp: float
    valve: float | None       # skutečné otevření ventilu
    valve_cmd: float | None   # co ventilu posílá regulátor
    t_supply: float
    t_return: float
    t_room: float
    setpoint: float
    power: float

    @property
    def delta_t(self):
        return self.t_supply - self.t_return


@dataclass
class SteadyResult:
    t_out: float
    t_supply: float
    t_return: float
    q_total: float
    heat_kw: float
    gas_kw: float
    boiler_eta: float
    pump_kw: float
    pump_speed: float
    pump_head: float
    node_dp: list
    branches: list = field(default_factory=list)

    def by_id(self, bid):
        return next(b for b in self.branches if b.id == bid)


def steady(net, t_out, occupied=True, setpoints=None, t_supply=None, max_iter=60):
    """
    Ustálený stav soustavy při venkovní teplotě t_out.

    occupied = True/False pro všechny zóny, nebo {id: bool} zvlášť.
    setpoints = {id: žádaná teplota} přepíše žádané teploty z rozvrhu.
    t_supply = vnucená teplota rozdělovače místo ekvitermní křivky.
    """
    occ = occupied if isinstance(occupied, dict) else {b.id: occupied for b in net.branches}
    sp = {b.id: effective_setpoint(b, occ[b.id], net.setback_enabled) for b in net.branches}
    if setpoints:
        sp.update(setpoints)
    ts = net.supply_temp(t_out) if t_supply is None else t_supply

    regulated = [b for b in net.branches if b.kvs is not None]
    valves = {b.id: 1.0 for b in regulated}
    cmd = dict(valves)

    hyd = None
    for _ in range(max_iter):
        speed = _pump_speed(net, valves)
        hyd = hydraulics.solve(net, valves, speed=speed)
        change = 0.0
        for b in regulated:
            p_req = thermal.zone_loss(b.zone, t_out, sp[b.id], occ[b.id])
            q_req = thermal.required_flow(b, ts, sp[b.id], p_req)
            u_new = 1.0 if q_req is None else _valve_for_flow(b, q_req, hyd.branch_dp[b.id])
            cmd[b.id] = u_new
            if b.valve_stuck is not None:
                # regulátor posílá povel, ale pohon stojí — v místnosti je
                # tepleji, než má, takže povel padá k nule
                u_new = b.valve_stuck
            u = 0.5 * valves[b.id] + 0.5 * u_new
            change = max(change, abs(u - valves[b.id]))
            valves[b.id] = u
        if change < 2e-4:
            break

    speed = _pump_speed(net, valves)
    hyd = hydraulics.solve(net, valves, speed=speed)
    results = []
    for b in net.branches:
        q = hyd.flows[b.id]
        ti, p, tr = thermal.free_float(b, ts, t_out, q, occ[b.id])
        if b.valve_stuck is not None:
            # porouchaný pohon: regulátor vidí přetopeno a zavírá
            cmd[b.id] = 0.0 if ti > sp[b.id] + 0.2 else 1.0
        results.append(BranchResult(
            id=b.id, flow=q, flow_design=b.q_design, ratio=q / b.q_design,
            dp=hyd.branch_dp[b.id],
            valve=valves.get(b.id), valve_cmd=cmd.get(b.id),
            t_supply=ts, t_return=tr if q > 1e-6 else ts, t_room=ti,
            setpoint=sp[b.id], power=p,
        ))

    q_tot = sum(r.flow for r in results)
    tr_mix = sum(r.flow * r.t_return for r in results) / q_tot if q_tot > 1e-6 else ts
    heat = sum(r.power for r in results) + pipe_loss(net, ts, tr_mix)
    eta = thermal.boiler_efficiency(tr_mix)
    return SteadyResult(
        t_out=t_out, t_supply=ts, t_return=tr_mix, q_total=q_tot,
        heat_kw=heat, gas_kw=heat / eta, boiler_eta=eta,
        pump_kw=hyd.pump_power, pump_speed=hyd.pump_speed, pump_head=hyd.pump_head,
        node_dp=hyd.node_dp, branches=results,
    )


# --- dynamický týden ----------------------------------------------------------
class ValvePI:
    """Zónový regulátor teploty prostoru — výstup je povel na ventil 0–1."""

    def __init__(self, kp=0.35, ti_h=1.5, out=0.6):
        self.kp, self.ti_h, self.i = kp, ti_h, out

    def step(self, error, dt_h):
        self.i += self.kp / self.ti_h * error * dt_h
        self.i = min(max(self.i, -0.2), 1.2)
        out = self.kp * error + self.i
        out = min(max(out, 0.0), 1.0)
        self.i = min(max(self.i, -self.kp * error), 1.0 - self.kp * error)
        return out


def _sensor(name, true_value, faults, rng, sigma):
    f = faults.get(name)
    if f and f["type"] == "stuck":
        return f["value"]
    v = true_value + rng.gauss(0.0, sigma)
    if f and f["type"] == "offset":
        v += f["value"]
    return v


def week(net, weather, sensor_faults=None, step_h=0.25, seed=7,
         start="2026-01-12T00:00"):
    """
    Dynamický průběh týdne. Vrací seznam záznamů tak, jak by je odebíral
    dispečink: měřené hodnoty včetně šumu a vadných čidel, žádné
    skryté veličiny (průtoky větví se neměří, jen celkový na kalorimetru).
    """
    from datetime import datetime, timedelta

    faults = AS_FOUND_SENSOR_FAULTS if sensor_faults is None else sensor_faults
    rng = random.Random(seed)
    t0 = datetime.fromisoformat(start)

    # výchozí stav — ustálený stav první hodnoty, ať simulace nezačíná skokem
    first = steady(net, weather[0][3], occupied=False)
    t_room = {r.id: r.t_room for r in first.branches}
    pis = {b.id: ValvePI(out=first.by_id(b.id).valve or 0.5)
           for b in net.branches if b.kvs is not None}
    valves = {b.id: first.by_id(b.id).valve or 1.0 for b in net.branches if b.kvs is not None}
    t_supply = first.t_supply
    records = []

    for t_h, day, hour, t_out in weather:
        occ = {b.id: b.zone.schedule.occupied(day, hour) for b in net.branches}
        # ranní náběh: regulace přejde na komfortní teplotu o preheat_h dřív,
        # aby byla na začátku směny v prostoru žádaná teplota
        heat_occ = {b.id: occ[b.id] or b.zone.schedule.occupied(
            (day + int((hour + net.preheat_h) // 24)) % 7, (hour + net.preheat_h) % 24)
            for b in net.branches}
        sp = {b.id: effective_setpoint(b, heat_occ[b.id], net.setback_enabled) for b in net.branches}

        # kotelna: reguluje na měřenou teplotu rozdělovače podle měřené venkovní
        t_out_meas = _sensor("header.t_out", t_out, faults, rng, 0.15)
        target = net.curve(t_out_meas) - net.header_sensor_error
        t_supply += (target - t_supply) * min(step_h / 0.4, 1.0)

        cmd = {}
        for b in net.branches:
            if b.kvs is None:
                continue
            room_meas = t_room[b.id] + rng.gauss(0.0, 0.1)
            cmd[b.id] = pis[b.id].step(sp[b.id] - room_meas, step_h)
            valves[b.id] = b.valve_stuck if b.valve_stuck is not None else cmd[b.id]

        speed = _pump_speed(net, valves)
        hyd = hydraulics.solve(net, valves, speed=speed)

        rec = {"ts": (t0 + timedelta(hours=t_h)).isoformat(timespec="minutes"),
               "t_out": t_out_meas}
        heat, num, q_tot = 0.0, 0.0, 0.0
        for b in net.branches:
            q = hyd.flows[b.id]
            z = b.zone
            p, tr = thermal.terminal(b, t_supply, t_room[b.id], q)
            gains = z.gains_occ if occ[b.id] else z.gains_unocc
            dti = (p + gains - z.ua * (t_room[b.id] - t_out)) / z.capacity
            t_room[b.id] += dti * step_h
            heat += p
            num += q * tr
            q_tot += q
            # čidla ve větvích: přívod je o kousek chladnější, čím dál od kotelny
            rec[f"{b.id}.t_supply"] = _sensor(f"{b.id}.t_supply",
                                              t_supply - 0.12 * b.node, faults, rng, 0.08)
            rec[f"{b.id}.t_return"] = _sensor(f"{b.id}.t_return", tr, faults, rng, 0.08)
            rec[f"{b.id}.t_room"] = _sensor(f"{b.id}.t_room", t_room[b.id], faults, rng, 0.1)
            rec[f"{b.id}.setpoint"] = sp[b.id]
            # skutečný průtok jen pro ověření virtuálních průtokoměrů —
            # podtržítko = skrytá veličina, diagnostika ji nečte
            rec[f"_{b.id}.flow"] = q
            if b.kvs is not None:
                rec[f"{b.id}.valve_cmd"] = cmd[b.id] * 100.0

        tr_mix = num / q_tot if q_tot > 1e-6 else t_supply
        heat = min(heat + pipe_loss(net, t_supply, tr_mix), BOILER_KW)
        eta = thermal.boiler_efficiency(tr_mix)
        rec.update({
            "header.t_supply": _sensor("header.t_supply", t_supply, faults, rng, 0.08)
                               + net.header_sensor_error,
            "header.t_return": _sensor("header.t_return", tr_mix, faults, rng, 0.08),
            # kalorimetr má vlastní párová čidla a průtokoměr — je spolehlivý
            "meter.flow": q_tot * (1 + rng.gauss(0, 0.005)),
            "meter.t_supply": t_supply + rng.gauss(0, 0.05),
            "meter.t_return": tr_mix + rng.gauss(0, 0.05),
            "meter.power": WATER_KW * q_tot * (t_supply - tr_mix),
            "header.sp_calc": net.curve(t_out_meas),
            "pump.speed": speed * 100.0,
            "pump.power": hyd.pump_power,
            "pump.head": hyd.pump_head,
            "boiler.gas_kw": heat / eta,
        })
        records.append(rec)
    return records
