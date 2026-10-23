"""
Napojení na bms-monitor — odkud optimizer bere data z kotelny a ceny energií.

bms-monitor sbírá data z kotelny do SQLite archivu (tabulka samples:
ts, device, key, value). Odtud se dá vyčíst skutečná ekvitermní křivka
(žádaná teplota rozdělovače proti venkovní), teploty rozdělovače
a sběrače a odebíraný výkon. Ceny energií se berou z plant.TARIFFS,
aby se v obou projektech počítalo se stejnými sazbami.

Větve ústředního topení bms-monitor neměří — pro ně slouží simulátor
měření v simulate.week(). Když bms-monitor k dispozici není, optimizer
běží dál s výchozími hodnotami z building.py.

Umístění bms-monitoru: proměnná prostředí BMS_MONITOR_DIR, výchozí ~/bms-monitor.
"""

import importlib.util
import os
import sqlite3
import statistics as st
from pathlib import Path

import building

KOTELNA_KEYS = ("t_outdoor", "sp_calc", "t_header_flow", "t_header_return",
                "heat_power", "load_power", "flow")


def bms_dir():
    return Path(os.environ.get("BMS_MONITOR_DIR", Path.home() / "bms-monitor"))


def tariffs():
    """Ceny energií z bms-monitor/plant.py, s doplněním emisních faktorů."""
    out = dict(building.TARIFFS)
    path = bms_dir() / "plant.py"
    if not path.exists():
        return out, "výchozí (building.py)"
    try:
        spec = importlib.util.spec_from_file_location("bms_plant", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        t = mod.TARIFFS
        out["electricity"] = float(t["electricity"]["price"])
        out["gas"] = float(t["gas"]["price"])
        return out, str(path)
    except Exception as exc:          # cizí soubor nesmí shodit optimizer
        return out, f"výchozí — plant.py nejde načíst ({exc.__class__.__name__})"


def _series(con, key):
    rows = con.execute(
        "SELECT ts, value FROM samples WHERE device = 'kotelna' AND key = ? ORDER BY ts",
        (key,)).fetchall()
    return rows


def _align(a, b):
    """Spáruje dvě řídké řady podle času — archiv ukládá jen změny."""
    out, j, last = [], 0, None
    for ts, v in a:
        while j < len(b) and b[j][0] <= ts:
            last = b[j][1]
            j += 1
        if last is not None:
            out.append((v, last))
    return out


def status():
    db = bms_dir() / "data.sqlite"
    t, t_src = tariffs()
    info = {"dir": str(bms_dir()), "db": str(db), "available": db.exists(),
            "tariffs": t, "tariffs_source": t_src}
    if not db.exists():
        return info
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        period = con.execute("SELECT min(ts), max(ts), count(*) FROM samples "
                             "WHERE device = 'kotelna'").fetchone()
        info["period"] = period[:2]
        info["samples"] = period[2]
        stats = {}
        series = {}
        for key in KOTELNA_KEYS:
            rows = _series(con, key)
            series[key] = rows
            vals = [v for _, v in rows]
            if vals:
                stats[key] = {"n": len(vals), "min": min(vals), "max": max(vals),
                              "mean": st.mean(vals)}
        info["stats"] = stats
        pairs = _align(series.get("sp_calc", []), series.get("t_outdoor", []))
        info["curve_points"] = [{"t_out": o, "sp": s} for s, o in pairs][-400:]
        info["curve_fit"] = _fit(pairs)
        con.close()
    except sqlite3.Error as exc:
        info["available"] = False
        info["error"] = str(exc)
    return info


def _fit(pairs):
    """Posun křivky při sklonu z bms-monitoru (3,4) — kolik dat, tolik přesnosti."""
    net = building.design()
    pts = [(o, s) for s, o in pairs if net.sp_min + 0.1 < s < net.sp_max - 0.1]
    if len(pts) < 5:
        return None
    from scipy.optimize import curve_fit

    def f(x, slope, shift):
        return [20.0 + slope * max(20.0 - v, 0.0) ** 0.8 + shift for v in x]

    try:
        (slope, shift), _ = curve_fit(f, [p[0] for p in pts], [p[1] for p in pts], p0=(3.4, 0.0))
    except RuntimeError:
        return None
    return {"slope": float(slope), "shift": float(shift), "n": len(pts)}
