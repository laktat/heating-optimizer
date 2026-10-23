"""
Tepelná strana — kolik tepla otopné těleso předá a kam se ustálí místnost.

Výkon tělesa závisí na tom, o kolik je voda teplejší než vzduch kolem:

    P = P_nom · (ΔT_log / ΔT_log,nom)^n

ΔT_log je logaritmický teplotní rozdíl mezi vodou a vzduchem, n je
exponent tělesa (deskový radiátor ~1,3, výměník VZT ~1,0). Současně musí
platit bilance na straně vody P = ṁ·c·(t_přívod − t_zpátečka). Z těch
dvou rovnic se pro daný průtok dopočítá zpátečka i výkon.

Důležitý důsledek, na kterém stojí diagnostika: výkon roste s průtokem
jen pomalu. Větev s dvojnásobným průtokem dá jen o 10–15 % víc tepla, zato se jí skoro neochladí voda — zpátečka je teplá a kondenzační
kotel přestane kondenzovat. Nevyváženost se proto pozná hlavně podle
malého ochlazení vody, ne podle výkonu.
"""

import math

from scipy.optimize import brentq

from building import T_RETURN_NOM, T_SUPPLY_NOM, WATER_KW


def lmtd(ts, tr, ti):
    """Logaritmický teplotní rozdíl voda–vzduch [K]."""
    a, b = ts - ti, tr - ti
    if a <= 0 or b <= 0:
        return 0.0
    if abs(a - b) < 1e-6:
        return a
    return (a - b) / math.log(a / b)


def _lmtd_nom(branch):
    return lmtd(T_SUPPLY_NOM, T_RETURN_NOM, branch.zone.sp_occ)


def terminal(branch, ts, ti, q):
    """
    Výkon tělesa [kW] a teplota zpátečky [°C] při daném průtoku q [m³/h].

    Řeší rovnost výkonu tělesa a výkonu odevzdaného vodou.
    """
    if q <= 1e-6 or ts <= ti + 1e-3:
        return 0.0, max(min(ts, ti), ti)
    m = WATER_KW * q
    l0 = _lmtd_nom(branch)

    def f(tr):
        return m * (ts - tr) - branch.p_nom * (lmtd(ts, tr, ti) / l0) ** branch.n_exp

    lo, hi = ti + 1e-6, ts - 1e-9
    tr = brentq(f, lo, hi, xtol=1e-6) if f(lo) > 0 > f(hi) else lo
    return m * (ts - tr), tr


def terminal_max(branch, ts, ti):
    """Výkon tělesa při nekonečném průtoku — horní mez, kterou nic nepřekoná."""
    if ts <= ti:
        return 0.0
    return branch.p_nom * ((ts - ti) / _lmtd_nom(branch)) ** branch.n_exp


def required_flow(branch, ts, ti, p_req):
    """Jaký průtok [m³/h] je potřeba, aby těleso dalo výkon p_req. None = nejde."""
    if p_req <= 1e-3:
        return 0.0
    if p_req >= 0.999 * terminal_max(branch, ts, ti):
        return None
    l0 = _lmtd_nom(branch)

    # zpátečka, při které má těleso přesně požadovaný výkon
    def g(tr):
        return branch.p_nom * (lmtd(ts, tr, ti) / l0) ** branch.n_exp - p_req

    lo, hi = ti + 1e-6, ts - 1e-9
    if g(lo) >= 0:
        # Výkon je tak malý, že se voda ochladí prakticky až na teplotu
        # vzduchu. ΔT_log se k nule blíží jen logaritmicky, proto ho brentq
        # nezachytí; průtok je tu ale určený jen ochlazením vody.
        return p_req / (WATER_KW * (ts - ti))
    tr = brentq(g, lo, hi, xtol=1e-6)
    return p_req / (WATER_KW * (ts - tr))


def zone_loss(zone, t_out, ti, occupied):
    """Kolik tepla [kW] musí zóna dostat, aby držela teplotu ti."""
    gains = zone.gains_occ if occupied else zone.gains_unocc
    return zone.ua * (ti - t_out) - gains


def free_float(branch, ts, t_out, q, occupied):
    """
    Kam se ustálí teplota prostoru při daném průtoku, když ji nic nereguluje.

    Vrací (teplota prostoru, výkon tělesa, zpátečka).
    """
    z = branch.zone
    gains = z.gains_occ if occupied else z.gains_unocc
    t_free = t_out + gains / z.ua          # teplota jen ze zisků, bez topení

    def h(ti):
        return terminal(branch, ts, ti, q)[0] - zone_loss(z, t_out, ti, occupied)

    if q <= 1e-6 or ts <= t_free:
        return t_free, 0.0, max(min(ts, t_free), t_free)
    ti = brentq(h, t_free - 1e-6, ts, xtol=1e-5)
    p, tr = terminal(branch, ts, ti, q)
    return ti, p, tr


# --- kotel --------------------------------------------------------------------
# Účinnost kondenzačního kotle (vztaženo k výhřevnosti) podle zpátečky.
# Pod rosným bodem spalin (~55 °C) kondenzuje a účinnost prudce roste.
BOILER_ETA = [(25.0, 1.06), (35.0, 1.04), (45.0, 1.00), (52.0, 0.97),
              (57.0, 0.93), (65.0, 0.915), (80.0, 0.90)]


def boiler_efficiency(t_return):
    pts = BOILER_ETA
    if t_return <= pts[0][0]:
        return pts[0][1]
    for (t0, e0), (t1, e1) in zip(pts, pts[1:]):
        if t_return <= t1:
            return e0 + (e1 - e0) * (t_return - t0) / (t1 - t0)
    return pts[-1][1]
