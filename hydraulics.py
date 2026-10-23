"""
Hydraulika otopné soustavy — kolik vody teče kterou větví.

Soustava je žebřík: čerpadlo tlačí vodu páteřním rozvodem a z něj
odbočují větve. Každý úsek páteře si vezme část tlaku, takže vzdálená
větev má k dispozici menší tlakovou diferenci než blízká. Větev sama je
sériové spojení potrubí s tělesy, vyvažovacího a regulačního ventilu.

Všechno se počítá přes Kv (průtok v m³/h při ztrátě 1 bar):

    Q = Kv · √(Δp / 100)            Δp v kPa
    1/Kv² = Σ 1/Kvᵢ²                 sériové řazení

Řešení: zvolí se tlaková diference na posledním uzlu a „pochoduje" se
zpátky ke kotelně — u každého uzlu se přičte průtok odbočky a tlaková
ztráta úseku. Na konci musí tlak sedět s křivkou čerpadla. To je jedna
neznámá a jedna monotónní rovnice, takže stačí metoda půlení intervalu
(brentq) a výpočet je robustní i pro hodně rozladěnou soustavu.
"""

from dataclasses import dataclass

from scipy.optimize import brentq

#: poměr regulace ventilu (Kvs / Kv nejmenší regulovatelné)
RANGEABILITY = 50.0


def control_valve_kv(kvs, u):
    """
    Kv regulačního ventilu s modifikovanou ekviprocentní charakteristikou.

    Čistá ekviprocentní křivka Kvs·R^(u−1) by při zavření neklesla na nulu,
    proto se odečte lineární člen, který ji v nule „dovře" — tak vypadá
    charakteristika skutečných kuželek.
    """
    u = min(max(u, 0.0), 1.0)
    return max(kvs * (RANGEABILITY ** (u - 1.0) - (1.0 - u) / RANGEABILITY), 1e-4)


def branch_kv(branch, u=1.0):
    """Ekvivalentní Kv celé větve při otevření regulačního ventilu u (0–1)."""
    inv = 1.0 / branch.kv_pipe ** 2 + 1.0 / branch.kv_balancing ** 2
    if branch.kv_obstruction is not None:
        inv += 1.0 / branch.kv_obstruction ** 2
    if branch.kvs is not None:
        if branch.valve_stuck is not None:
            u = branch.valve_stuck          # pohon nereaguje, ventil stojí
        inv += 1.0 / control_valve_kv(branch.kvs, u) ** 2
    return inv ** -0.5


def flow(kv, dp_kpa):
    return kv * (max(dp_kpa, 0.0) / 100.0) ** 0.5


def loss(kv, q):
    """Tlaková ztráta [kPa] prvku s daným Kv při průtoku q."""
    return 100.0 * (q / kv) ** 2


@dataclass
class HydraulicState:
    flows: dict               # průtok větví [m³/h]
    node_dp: list             # tlaková diference na uzlech páteře [kPa], index 0 = rozdělovač
    branch_dp: dict           # tlaková diference, kterou má větev k dispozici [kPa]
    q_total: float            # celkový průtok čerpadlem [m³/h]
    pump_head: float          # dopravní výška čerpadla [kPa]
    pump_speed: float
    pump_power: float         # elektrický příkon čerpadla [kW]
    pump_eta: float

    def ratio(self, branch):
        """Skutečný / návrhový průtok — 1,0 je vyvážená větev."""
        return self.flows[branch.id] / branch.q_design


def _march(net, kvs, dp_last):
    """Z tlaku na posledním uzlu dopočítá tlaky všech uzlů a celkový průtok."""
    n_nodes = len(net.segment_kv)
    dp = [0.0] * (n_nodes + 1)
    dp[n_nodes] = dp_last
    q_down = 0.0
    for k in range(n_nodes, 0, -1):
        q_down += sum(flow(kv, dp[k]) for b, kv in kvs if b.node == k)
        dp[k - 1] = dp[k] + loss(net.segment_kv[k - 1], q_down)
    return dp, q_down


def solve(net, valves=None, speed=None):
    """
    Ustálené průtoky v soustavě.

    valves = {id větve: otevření 0–1} pro větve s regulačním ventilem;
    chybějící větve se berou jako plně otevřené.
    """
    valves = valves or {}
    pump = net.pump
    s = pump.speed if speed is None else speed
    kvs = [(b, branch_kv(b, valves.get(b.id, 1.0))) for b in net.branches]

    def residual(dp_last):
        dp, q = _march(net, kvs, dp_last)
        return pump.head(q, s) - dp[0] - loss(net.plant_kv, q)

    top = pump.h0 * s * s
    if s <= 0.01 or residual(top) > 0:
        dp_last = top if s > 0.01 else 0.0
    else:
        dp_last = brentq(residual, 0.0, top, xtol=1e-6)
    dp, q = _march(net, kvs, dp_last)

    flows = {b.id: flow(kv, dp[b.node]) for b, kv in kvs}
    head = max(pump.head(q, s), 0.0)
    return HydraulicState(
        flows=flows, node_dp=dp,
        branch_dp={b.id: dp[b.node] for b in net.branches},
        q_total=q, pump_head=head, pump_speed=s,
        pump_power=pump.power_kw(q, s) if s > 0.01 else 0.0,
        pump_eta=pump.efficiency(q, s),
    )
