"""
Webové rozhraní optimizeru.

Při startu se jednou spočítá celý řetěz — týden měření, diagnostika,
plán opatření a roční ekonomika — a výsledky se drží v paměti. Rozhraní
si je jen čte. Interaktivní simulace (/api/simulate) počítá ustálený stav
na požádání; jeden výpočet trvá jednotky milisekund, roční bilance kolem
půl sekundy.

Spuštění:  python -m web.server   →  http://127.0.0.1:8010
"""

import sys
import threading
from dataclasses import replace
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, HTTPException                      # noqa: E402
from fastapi.responses import FileResponse, JSONResponse         # noqa: E402
from fastapi.staticfiles import StaticFiles                      # noqa: E402
from pydantic import BaseModel                                   # noqa: E402

import bms_bridge                                                # noqa: E402
import building                                                  # noqa: E402
import climate                                                   # noqa: E402
import diagnostics                                               # noqa: E402
import economics                                                 # noqa: E402
import hydraulics                                                # noqa: E402
import optimizer                                                 # noqa: E402
import simulate                                                  # noqa: E402

VERSION = "0.1.0"
STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Heating Optimizer")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

_state = {}
_lock = threading.Lock()


def compute():
    """Celý řetěz od měření po návratnost. Volá se jednou při startu."""
    t0 = time.time()
    tariffs, t_src = bms_bridge.tariffs()
    records = simulate.week(building.as_found(), climate.measurement_week())
    diag = diagnostics.diagnose(records)
    plan = optimizer.plan(diag)
    econ = economics.evaluate(plan, tariffs)
    _state.update(records=records, diag=diag, plan=plan, econ=econ,
                  tariffs=tariffs, tariffs_source=t_src,
                  computed_in=time.time() - t0)
    return _state


def state():
    with _lock:
        if not _state:
            compute()
    return _state


# --- serializace ---------------------------------------------------------------
def network_info(net):
    return {
        "curve_slope": net.curve_slope, "curve_shift": net.curve_shift,
        "pump_mode": net.pump_mode, "pump_speed": net.pump.speed,
        "dp_target": net.dp_target, "setback_enabled": net.setback_enabled,
        "header_sensor_error": net.header_sensor_error,
        "q_design_total": net.q_design_total,
        "pump": {"h0": net.pump.h0, "r": net.pump.r},
        "branches": [{
            "id": b.id, "name": b.name, "short": b.short, "kind": b.kind,
            "node": b.node, "control": b.control, "p_nom": b.p_nom,
            "q_design": b.q_design, "bv_dn": b.bv_dn, "bv_turns": b.bv_turns,
            "kvs": b.kvs, "valve_stuck": b.valve_stuck,
            "kv_obstruction": b.kv_obstruction,
            "sp_occ": b.zone.sp_occ, "sp_setback": b.zone.sp_setback,
            # tlak, který větev potřebuje na návrhový průtok při plně otevřených ventilech
            "dp_need": hydraulics.loss(hydraulics.branch_kv(
                replace(b, valve_stuck=None), 1.0), b.q_design),
        } for b in net.branches],
    }


def steady_info(res):
    return {
        "t_out": res.t_out, "t_supply": res.t_supply, "t_return": res.t_return,
        "q_total": res.q_total, "heat_kw": res.heat_kw, "gas_kw": res.gas_kw,
        "boiler_eta": res.boiler_eta, "pump_kw": res.pump_kw,
        "pump_speed": res.pump_speed, "pump_head": res.pump_head,
        "node_dp": res.node_dp,
        "branches": [{**b.__dict__, "delta_t": b.delta_t} for b in res.branches],
    }


def _net(name):
    p = state()["plan"]
    if name == "baseline":
        return p.baseline
    if name == "optimal":
        return p.optimal
    for mid, _, net in p.steps:
        if mid == name:
            return net
    raise HTTPException(404, f"neznámý stav {name}")


# --- API ----------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/meta")
def meta():
    s = state()
    return {"version": VERSION, "computed_in": s["computed_in"],
            "tariffs": s["tariffs"], "tariffs_source": s["tariffs_source"],
            "period": s["diag"].summary["period"]}


@app.get("/api/overview")
def overview():
    s = state()
    d, e = s["diag"], s["econ"]
    return {
        "baseline": e["baseline"], "optimal": e["optimal"], "total": e["total"],
        "packages": e["packages"], "measures": [
            {k: m[k] for k in ("id", "name", "invest", "saving_kc", "payback_y",
                               "saving_gas_kwh", "saving_el_kwh")}
            for m in e["measures"]],
        "findings": [f.as_dict() for f in d.findings],
        "summary": d.summary,
    }


@app.get("/api/diagnosis")
def diagnosis():
    return state()["diag"].as_dict()


@app.get("/api/plan")
def plan_info():
    s = state()
    p = s["plan"]
    b = p.balance
    curves = []
    for t in range(-16, 21):
        curves.append({"t_out": t,
                       "now": p.baseline.supply_temp(t),
                       "now_curve": p.baseline.curve(t),
                       "new": p.optimal.supply_temp(t),
                       "design": building.design().curve(t)})
    return {
        "parameters": p.parameters,
        "balance": {"turns": b.turns, "targets": b.targets, "index": b.index_branch,
                    "dp_index": b.dp_index, "pump_head": b.pump_head,
                    "pump_speed": b.pump_speed, "q_total": b.q_total,
                    "check": b.check, "warnings": b.warnings},
        "curve_points": [{"t_out": t, "required": r} for t, r in p.curve_points],
        "curves": curves,
        "margin": optimizer.CURVE_MARGIN,
        "steps": [{"id": mid, "label": label} for mid, label, _ in p.steps],
        "baseline": network_info(p.baseline), "optimal": network_info(p.optimal),
    }


@app.get("/api/economics")
def economics_info():
    return state()["econ"]


@app.get("/api/timeseries")
def timeseries(keys: str = ""):
    recs = state()["records"]
    wanted = [k for k in keys.split(",") if k] or sorted(recs[0].keys())
    return {"ts": [r["ts"] for r in recs],
            "series": {k: [r.get(k) for r in recs] for k in wanted}}


@app.get("/api/network/{name}")
def network(name: str, t_out: float = -5.0, occupied: bool = True):
    net = _net(name)
    return {"network": network_info(net),
            "steady": steady_info(simulate.steady(net, t_out, occupied))}


class SimRequest(BaseModel):
    base: str = "baseline"
    t_out: float = -5.0
    occupied: bool = True
    curve_slope: float | None = None
    curve_shift: float | None = None
    pump_mode: str | None = None
    pump_speed: float | None = None
    dp_target: float | None = None
    turns: dict | None = None
    repair: bool = False
    setback: bool | None = None
    annual: bool = False


@app.post("/api/simulate")
def simulate_custom(req: SimRequest):
    net = _net(req.base).copy()
    if req.repair:
        net = optimizer.repaired(net)
    if req.curve_slope is not None:
        net.curve_slope = req.curve_slope
    if req.curve_shift is not None:
        net.curve_shift = req.curve_shift
    if req.pump_mode in ("fixed", "dp"):
        net.pump_mode = req.pump_mode
    if req.pump_speed is not None:
        net.pump.speed = min(max(req.pump_speed, 0.3), 1.0)
    if req.dp_target is not None:
        net.dp_target = req.dp_target
    if net.pump_mode == "dp" and net.dp_target is None:
        net.dp_target = state()["plan"].balance.dp_index
    if req.setback is not None:
        net.setback_enabled = req.setback
    for bid, t in (req.turns or {}).items():
        b = next((x for x in net.branches if x.id == bid), None)
        if b:
            b.bv_turns = min(max(float(t), building.BALANCING_TURNS_MIN),
                             building.BALANCING_TURNS_MAX)
    out = {"network": network_info(net),
           "steady": steady_info(simulate.steady(net, req.t_out, req.occupied))}
    if req.annual:
        a = economics.annual(net, state()["tariffs"])
        out["annual"] = a.as_dict()
        out["baseline_cost"] = state()["econ"]["baseline"]["cost"]
    return out


@app.get("/api/bms")
def bms():
    return bms_bridge.status()


@app.post("/api/recompute")
def recompute():
    with _lock:
        _state.clear()
        compute()
    return {"ok": True, "computed_in": _state["computed_in"]}


@app.exception_handler(ValueError)
def value_error(_, exc):
    return JSONResponse({"detail": str(exc)}, status_code=422)


def main():
    import argparse

    import uvicorn
    ap = argparse.ArgumentParser(description="Webové rozhraní optimizeru vytápění")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8010)
    args = ap.parse_args()
    state()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
