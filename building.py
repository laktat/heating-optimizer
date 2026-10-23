"""
Soupis otopné soustavy závodu — jediné místo, kde je popsané, jaké větve
z kotelny vedou, co vytápějí a jak jsou nadimenzované.

Navazuje na bms-monitor: kotelna se dvěma kotli 400 kW a okruh topné vody
jsou tytéž, tady se jen rozepisuje, kam z rozdělovače voda teče. Vedle
ohřívačů tří VZT jednotek jsou na rozdělovači i tři větve ústředního topení.

Rozdělovač ── úsek 1 ── uzel 1 ── úsek 2 ── uzel 2 ── … ── uzel 6
                         │                    │              │
                    Administrativa         Šatny          Sklad
                    (nejblíž kotelně)                 (nejdál, poslední)

Každá větev visí na páteřním rozvodu v jiném místě. Čím je dál od kotelny,
tím víc tlaku po cestě ztratí potrubí — proto se větve musí vyvážit, jinak
ty blízké berou vodu těm vzdáleným.

Soubor popisuje dva stavy:

    DESIGN   jak to navrhl projektant (jmenovité výkony, průtoky, tlakové ztráty)
    AS_FOUND jak to technik na místě našel (nevyvážené ventily, zvednutá
             křivka, porouchaný pohon, vadná čidla)

AS_FOUND slouží jen simulátoru, který z něj vyrábí „naměřená" data.
Diagnostika ho nevidí — pracuje jen s měřením a s projektem, stejně jako
technik, který přijede na neznámou stavbu.
"""

from dataclasses import dataclass, field, replace

CP_WATER = 4.186          # [kJ/(kg·K)]
RHO_WATER = 1000.0        # [kg/m³]
#: tepelný výkon vody [kW] na 1 m³/h a 1 K
WATER_KW = RHO_WATER * CP_WATER / 3600.0      # ≈ 1,163

T_SUPPLY_NOM = 75.0       # jmenovitý teplotní spád soustavy 75/55 °C
T_RETURN_NOM = 55.0
T_OUT_DESIGN = -12.0      # výpočtová venkovní teplota (Praha, ČSN EN 12831)


# --- vyvažovací ventil ---------------------------------------------------------
# Kv při plném otevření podle dimenze, zhruba podle běžných škrticích
# vyvažovacích ventilů s ručním kolem 0–4 otáčky.
BALANCING_KV_MAX = {20: 5.7, 25: 8.7, 32: 14.2, 40: 19.2, 50: 33.0, 65: 85.0}
BALANCING_TURNS_MAX = 4.0
BALANCING_TURNS_MIN = 0.5


def balancing_kv(dn, turns):
    """Kv vyvažovacího ventilu [m³/h] podle dimenze a nastavení [otáčky]."""
    t = min(max(turns, BALANCING_TURNS_MIN), BALANCING_TURNS_MAX)
    return BALANCING_KV_MAX[dn] * (t / BALANCING_TURNS_MAX) ** 1.8


def balancing_turns(dn, kv):
    """Inverze: kolik otáček nastavit, aby ventil měl dané Kv."""
    ratio = max(kv, 1e-6) / BALANCING_KV_MAX[dn]
    t = BALANCING_TURNS_MAX * ratio ** (1.0 / 1.8)
    return min(max(t, BALANCING_TURNS_MIN), BALANCING_TURNS_MAX)


SHORT_NAMES = {"adm": "Administrativa", "sat": "Šatny", "vzt3": "VZT 3",
               "vzt2": "Lakovna", "vzt1": "Hala A", "skl": "Sklad"}


# --- popis větve a zóny ----------------------------------------------------------
@dataclass
class Schedule:
    """Kdy je zóna obsazená — dny v týdnu (0 = pondělí) a hodiny od–do."""
    days: tuple = (0, 1, 2, 3, 4)
    start: float = 6.0
    end: float = 18.0

    def occupied(self, weekday, hour):
        return weekday in self.days and self.start <= hour < self.end

    def occupied_share(self):
        """Podíl obsazených hodin v týdnu."""
        return len(self.days) * (self.end - self.start) / (7 * 24)


@dataclass
class Zone:
    """Vytápěný prostor — co ztrácí, co získává, jak je setrvačný."""
    ua: float                 # měrná tepelná ztráta prostupem a větráním [kW/K]
    gains_occ: float          # vnitřní zisky při provozu (stroje, lidé, světla) [kW]
    gains_unocc: float        # zisky mimo provoz [kW]
    capacity: float           # tepelná kapacita [kWh/K]
    sp_occ: float             # žádaná teplota při provozu [°C]
    sp_setback: float         # útlumová teplota [°C]
    schedule: Schedule = field(default_factory=Schedule)

    @property
    def tau_h(self):
        """Časová konstanta prostoru [h] — jak rychle vychladne."""
        return self.capacity / self.ua


@dataclass
class Branch:
    id: str
    name: str
    kind: str                 # "radiators" | "ahu_coil" | "unit_heaters"
    node: int                 # na kterém uzlu páteře větev visí (1 = nejblíž)
    p_nom: float              # jmenovitý výkon otopných těles při 75/55/t_i [kW]
    n_exp: float              # exponent otopného tělesa (radiátor 1,3; výměník ~1,0)
    zone: Zone
    control: str              # "zone" = regulační ventil na teplotu prostoru, "none" = bez regulace
    dp_design: float          # tlaková ztráta potrubí a těles při návrhovém průtoku [kPa]
    kvs: float | None         # Kvs regulačního ventilu [m³/h], None když ventil není
    bv_dn: int                # dimenze vyvažovacího ventilu
    bv_turns: float = BALANCING_TURNS_MAX   # nastavení vyvažovacího ventilu
    valve_stuck: float | None = None        # zaseklý pohon: skutečné otevření 0–1
    #: dodatečný odpor, o kterém projekt neví — ucpané sítko filtru,
    #: přivřená uzavírací armatura [Kv, m³/h]; None = nic takového
    kv_obstruction: float | None = None

    @property
    def short(self):
        return SHORT_NAMES.get(self.id, self.name)

    @property
    def q_design(self):
        """Návrhový průtok [m³/h] při jmenovitém spádu 20 K."""
        return self.p_nom / (WATER_KW * (T_SUPPLY_NOM - T_RETURN_NOM))

    @property
    def kv_pipe(self):
        """Ekvivalentní Kv potrubí a těles, odvozené z návrhové tlakové ztráty."""
        return self.q_design / (self.dp_design / 100.0) ** 0.5

    @property
    def kv_balancing(self):
        return balancing_kv(self.bv_dn, self.bv_turns)


@dataclass
class Pump:
    """
    Oběhové čerpadlo topné vody za hydraulickým oddělovačem.

    Křivka H = H0·s² − r·Q² (zákony podobnosti, s = otáčky / jmenovité).
    Účinnost je nejvyšší v optimálním bodě, který se s otáčkami posouvá.
    """
    h0: float = 185.0         # tlak při nulovém průtoku [kPa]
    r: float = 0.040          # strmost křivky [kPa/(m³/h)²]
    q_bep: float = 38.0       # průtok v optimálním bodě při plných otáčkách [m³/h]
    eta_max: float = 0.62     # celková účinnost čerpadla s motorem v optimu
    speed: float = 1.0        # aktuální otáčky (poměr)
    rated_kw: float = 2.2

    def head(self, q, speed=None):
        s = self.speed if speed is None else speed
        return self.h0 * s * s - self.r * q * q

    def efficiency(self, q, speed=None):
        s = self.speed if speed is None else speed
        rel = q / max(self.q_bep * s, 1e-6)
        return max(self.eta_max * (1.0 - 0.55 * (rel - 1.0) ** 2), 0.15)

    def power_kw(self, q, speed=None):
        """Elektrický příkon [kW] z hydraulického výkonu Q·H a účinnosti."""
        h = max(self.head(q, speed), 0.0)
        return q / 3600.0 * h / self.efficiency(q, speed)


@dataclass
class Network:
    branches: list
    pump: Pump
    #: Kv úseků páteřního rozvodu (přívod i zpátečka dohromady); úsek k vede do uzlu k
    segment_kv: list
    #: Kv strojovny — rozdělovač, sběrač, uzávěry, filtr [m³/h]
    plant_kv: float
    #: tepelná ztráta rozvodů vedoucích nevytápěným prostorem [kW/K]
    pipe_ua: float = 0.35
    #: teplota prostoru kolem těchto rozvodů (technické chodby, podhledy) [°C]
    pipe_ambient: float = 10.0
    #: ekvitermní křivka: žádaná = 20 + sklon·(20 − venkovní)^0,8 + posun
    curve_slope: float = 3.4
    curve_shift: float = 0.0
    sp_min: float = 40.0
    sp_max: float = 80.0
    setback_enabled: bool = True
    #: o kolik hodin dřív se po útlumu začne zatápět na komfortní teplotu
    preheat_h: float = 0.0
    #: chyba čidla teploty rozdělovače [K] = co ukazuje − skutečnost.
    #: Kotelna reguluje na měřenou hodnotu, takže čidlo, které ukazuje
    #: o 2,5 K méně (−2,5), vyrobí vodu o 2,5 K teplejší, než chce křivka.
    header_sensor_error: float = 0.0
    #: "fixed" = čerpadlo na pevných otáčkách, "dp" = drží tlakovou diferenci
    #: na nejvzdálenějším uzlu (dp_target) a otáčky si dopočítá samo
    pump_mode: str = "fixed"
    dp_target: float | None = None

    def by_id(self, bid):
        return next(b for b in self.branches if b.id == bid)

    def curve(self, t_out, slope=None, shift=None):
        """Ekvitermní křivka — stejná rovnice jako v kotelně bms-monitoru."""
        s = self.curve_slope if slope is None else slope
        k = self.curve_shift if shift is None else shift
        sp = 20.0 + s * max(20.0 - t_out, 0.0) ** 0.8 + k
        return min(max(sp, self.sp_min), self.sp_max)

    def supply_temp(self, t_out):
        """Skutečná teplota na rozdělovači — kotelna reguluje na čidlo s chybou."""
        return self.curve(t_out) - self.header_sensor_error

    @property
    def q_design_total(self):
        return sum(b.q_design for b in self.branches)

    def copy(self):
        return replace(self, branches=[replace(b, zone=replace(b.zone))
                                       for b in self.branches],
                       pump=replace(self.pump), segment_kv=list(self.segment_kv))


def _segment_kv(branches, dp_kpa):
    """Kv úseků páteře z návrhových průtoků, které jimi tečou, a z návrhové ztráty."""
    nodes = max(b.node for b in branches)
    out = []
    for k in range(1, nodes + 1):
        q = sum(b.q_design for b in branches if b.node >= k)
        out.append(q / (dp_kpa[k - 1] / 100.0) ** 0.5)
    return out


# Výrobní provoz jede ve třech směnách v pracovní dny.
SHIFTS = Schedule(days=(0, 1, 2, 3, 4), start=0.0, end=24.0)


def _design_branches():
    return [
        Branch("adm", "ÚT — Administrativa", "radiators", 1, p_nom=90.0, n_exp=1.3,
               zone=Zone(ua=2.42, gains_occ=12.0, gains_unocc=2.0, capacity=120.0,
                         sp_occ=21.0, sp_setback=17.0,
                         schedule=Schedule(start=6.0, end=18.0)),
               control="zone", dp_design=18.0, kvs=6.3, bv_dn=32),
        Branch("sat", "ÚT — Šatny a sociální zázemí", "radiators", 2, p_nom=42.0, n_exp=1.3,
               zone=Zone(ua=1.32, gains_occ=3.0, gains_unocc=0.5, capacity=50.0,
                         sp_occ=22.0, sp_setback=17.0,
                         schedule=Schedule(start=5.0, end=23.0)),
               control="none", dp_design=30.0, kvs=None, bv_dn=20),
        Branch("vzt3", "Ohřívač VZT 3 — Sklad a administrativa", "ahu_coil", 3,
               p_nom=110.0, n_exp=1.0,
               zone=Zone(ua=3.12, gains_occ=20.0, gains_unocc=4.0, capacity=90.0,
                         sp_occ=20.0, sp_setback=16.0,
                         schedule=Schedule(start=6.0, end=18.0)),
               control="zone", dp_design=25.0, kvs=10.0, bv_dn=40),
        Branch("vzt2", "Ohřívač VZT 2 — Lakovna", "ahu_coil", 4, p_nom=180.0, n_exp=1.0,
               zone=Zone(ua=5.0, gains_occ=25.0, gains_unocc=3.0, capacity=60.0,
                         sp_occ=21.0, sp_setback=16.0,
                         schedule=Schedule(start=6.0, end=22.0)),
               control="zone", dp_design=25.0, kvs=16.0, bv_dn=50),
        Branch("vzt1", "Ohřívač VZT 1 — Výrobní hala A", "ahu_coil", 5, p_nom=320.0, n_exp=1.0,
               zone=Zone(ua=10.0, gains_occ=110.0, gains_unocc=20.0, capacity=600.0,
                         sp_occ=18.0, sp_setback=15.0, schedule=SHIFTS),
               control="zone", dp_design=25.0, kvs=25.0, bv_dn=65),
        Branch("skl", "Sklad — teplovzdušné jednotky", "unit_heaters", 6, p_nom=130.0, n_exp=1.15,
               zone=Zone(ua=4.5, gains_occ=5.0, gains_unocc=2.0, capacity=250.0,
                         sp_occ=16.0, sp_setback=12.0,
                         schedule=Schedule(start=6.0, end=22.0)),
               control="none", dp_design=28.0, kvs=None, bv_dn=40),
    ]


def design():
    """Soustava tak, jak ji navrhl projektant — vyvážená, s útlumem."""
    branches = _design_branches()
    # Návrhová ztráta úseků páteře [kPa]. Poslední úsek ke skladu je dlouhý
    # (přes celý areál) a nejtenčí — proto je sklad nejvzdálenější větev.
    seg_dp = [3.0, 4.0, 6.0, 8.0, 14.0, 45.0]
    net = Network(branches=branches, pump=Pump(), segment_kv=_segment_kv(branches, seg_dp),
                  plant_kv=130.0)
    # Projekt počítá s vyvážením — nastavení ventilů dopočítá optimizer
    # stejným postupem, jakým ho dělá projektant (optimizer.balance).
    return net


def as_found():
    """
    Stav, jaký technik na místě najde. Typický příběh staršího závodu:

      * soustava nebyla nikdy vyvážená — všechny vyvažovací ventily naplno,
        čerpadlo jede pořád na 100 % otáček;
      * sítko filtru ve větvi skladu je zanesené a sklad je navíc nejdál —
        teče tam málo vody, je tam zima, tak údržba zvedla křivku o 6 K;
      * pohon zónového ventilu administrativy je vadný a ventil zůstal
        stát na 70 % — kanceláře se přetápějí a lidé větrají okny;
      * čidlo teploty na rozdělovači ukazuje o 2,5 K méně (špatně usazená
        jímka), takže kotelna topí ještě víc, než ukazuje křivka;
      * čidlo zpátečky šaten zamrzlo na jedné hodnotě (vadný převodník);
      * útlum je vypnutý — někdo ho vypnul kvůli rannímu vychladnutí.
    """
    net = design()
    for b in net.branches:
        b.bv_turns = BALANCING_TURNS_MAX
    net.by_id("adm").valve_stuck = 0.7
    net.by_id("skl").kv_obstruction = 3.5
    net.pump.speed = 1.0
    net.curve_shift = 6.0
    net.header_sensor_error = -2.5
    net.setback_enabled = False
    return net


#: Další vadná čidla pro simulátor měření. Chybu čidla rozdělovače nese
#: přímo Network.header_sensor_error, protože ovlivňuje i regulaci kotelny.
AS_FOUND_SENSOR_FAULTS = {
    "sat.t_return": {"type": "stuck", "value": 48.0},
}

#: Ceny energií. Výchozí hodnoty jsou stejné jako v bms-monitor/plant.py;
#: když je bms-monitor k dispozici, bms_bridge je načte přímo odtamtud.
TARIFFS = {
    "electricity": 4.20,      # Kč/kWh včetně distribuce
    "gas": 1.60,              # Kč/kWh výhřevnosti
    "currency": "Kč",
    "co2_gas": 0.202,         # kg CO₂/kWh zemního plynu
    "co2_el": 0.450,          # kg CO₂/kWh elektřiny (energetický mix ČR)
}
