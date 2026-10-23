"""
Venkovní teplota — typický rok pro roční bilanci a jeden mrazivý týden,
na kterém simulátor vyrábí „naměřená" data.

Typický rok vychází z dlouhodobých měsíčních průměrů pro Prahu. Mezi nimi
se interpoluje, přidá se denní chod (minimum ráno, maximum odpoledne)
a počasí — několikadenní vlny, které teplotu odtlačí od průměru. Šum je
s pevným semínkem, takže každý výpočet úspor dopadne stejně.

Topí se podle české praxe jen ve dnech, kdy průměrná denní teplota klesne
pod 13 °C (vyhláška 194/2007 Sb.).
"""

import math
import random
from dataclasses import dataclass

#: dlouhodobé měsíční průměry Praha-Ruzyně [°C]
MONTHLY_MEAN = [-1.0, 0.3, 4.0, 9.0, 13.8, 17.0, 18.9, 18.5, 14.2, 9.0, 4.0, 0.4]
#: rozkmit denního chodu (max − min) po měsících [K]
DAILY_RANGE = [5.0, 6.0, 8.0, 10.0, 11.0, 11.0, 11.0, 11.0, 10.0, 8.0, 5.0, 4.5]
HEATING_LIMIT = 13.0
#: 1. 1. 2026 byl čtvrtek — v kódu je pondělí 0
YEAR_START_WEEKDAY = 3


@dataclass
class Hour:
    t_out: float
    weekday: int
    hour: int
    heating: bool             # topná sezóna — průměr dne pod 13 °C


def _monthly(values, day):
    """Hladká interpolace měsíčních hodnot na den v roce (cyklicky)."""
    mid = [15.0 + 30.42 * m for m in range(12)]
    x = day % 365
    for i in range(12):
        a, b = mid[i], mid[(i + 1) % 12] + (365 if i == 11 else 0)
        xx = x + (365 if x < mid[0] else 0)
        if a <= xx < b:
            w = (1 - math.cos(math.pi * (xx - a) / (b - a))) / 2
            return values[i] + (values[(i + 1) % 12] - values[i]) * w
    return values[0]


def typical_year(seed=2026):
    rng = random.Random(seed)
    hours = []
    weather = 0.0                           # odchylka počasí od průměru [K]
    for day in range(365):
        mean = _monthly(MONTHLY_MEAN, day)
        rng_k = _monthly(DAILY_RANGE, day)
        # zimní vlny jsou výraznější (inverze, mrazivé tlakové výše)
        sigma = 4.2 if mean < 5 else 2.8
        weather = weather * math.exp(-1 / 3.0) + rng.gauss(0, sigma) * (1 - math.exp(-2 / 3.0)) ** 0.5
        day_mean = mean + weather
        heating = day_mean < HEATING_LIMIT
        weekday = (YEAR_START_WEEKDAY + day) % 7
        for h in range(24):
            # minimum kolem 5. hodiny, maximum kolem 15. hodiny
            t = day_mean + rng_k / 2 * math.sin(2 * math.pi * (h - 9) / 24)
            hours.append(Hour(t + rng.gauss(0, 0.4), weekday, h, heating))
    return hours


def degree_days(hours, t_in=19.0):
    """Denostupně za topné období (ČSN 38 3350: vnitřní 19 °C, mez 13 °C)."""
    days = {}
    for i, h in enumerate(hours):
        days.setdefault(i // 24, []).append(h)
    total, n = 0.0, 0
    for d in days.values():
        if d[0].heating:
            total += t_in - sum(x.t_out for x in d) / 24
            n += 1
    return total, n


def measurement_week(step_h=0.25, seed=12):
    """
    Mrazivý lednový týden (pondělí 12. 1. 2026 – neděle 18. 1.) po čtvrthodinách.

    Začíná mírně pod nulou, ve čtvrtek přijde mráz až ke −12 °C a o víkendu
    se oteplí — dost rozmanitosti, aby se v datech ukázaly všechny závady.
    """
    rng = random.Random(seed)
    day_means = [-3.0, -5.5, -8.5, -10.5, -7.0, -2.5, 0.5]
    out = []
    n = int(round(7 * 24 / step_h))
    for i in range(n):
        t_h = i * step_h
        day = int(t_h // 24)
        hour = t_h - day * 24
        # plynulý přechod mezi dny, ať teplota o půlnoci neskáče
        nxt = day_means[min(day + 1, 6)]
        mean = day_means[day] + (nxt - day_means[day]) * (hour / 24)
        t = mean + 2.2 * math.sin(2 * math.pi * (hour - 9) / 24) + rng.gauss(0, 0.25)
        out.append((t_h, day, hour, t))
    return out
