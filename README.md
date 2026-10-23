# Optimalizace vytápění — proč budova spotřebovává moc a co s tím

Navazuje na [bms-monitor](../../bms-monitor): tam se technologie závodu
**sleduje**, tady se **hledá, proč spotřebuje víc, než musí, a počítá, co
s tím**. Kotelna (2 × 400 kW, kondenzační), okruh topné vody a ohřívače tří
VZT jednotek jsou tytéž; přibyly tři větve ústředního topení a celá
hydraulika rozvodu mezi nimi.

Projekt vznikl ze stejné praxe jako bms-monitor. Na stavbách se pořád
opakuje tentýž příběh: soustava nebyla nikdy vyvážená, vzdálený sklad je
studený, a tak údržba zvedne křivku. Sklad se zahřeje, ale kanceláře
a šatny se přetápějí, zpátečka je teplá a kotle nekondenzují. Čerpadlo
jede naplno proti přivřeným ventilům. Všechno, co je potřeba k nápravě, se
dá spočítat z dat, která regulace stejně měří.

## Co to dělá

| | Modul | Co počítá |
|---|---|---|
| 1 | `hydraulics.py` | Průtoky a tlaky v páteři a větvích — Kv prvků, křivka čerpadla, ekviprocentní ventily |
| 2 | `thermal.py` | Výkon těles (ΔT_log, exponent n), zpátečka, ustálená teplota prostoru, účinnost kotle podle zpátečky |
| 3 | `simulate.py` | Ustálený stav soustavy s regulací a dynamický týden po čtvrthodinách → „naměřená" data |
| 4 | `diagnostics.py` | Vadná čidla, virtuální průtokoměry, zaseklé ventily, ucpané větve, nevyváženost, nastavení provozu |
| 5 | `optimizer.py` | Opravy → vyvážení metodou indexové větve → Δp čerpadla → ekvitermní křivka → útlum |
| 6 | `economics.py` | Roční bilance metodou teplotních tříd, úspory v Kč, návratnost, NPV, IRR, CO₂ |
| 7 | `bms_bridge.py` | Čte archiv a ceny energií z bms-monitoru |
| 8 | `web/` | Rozhraní ve stejném vzhledu jako dispečink bms-monitoru |

## Soustava

```
Kotelna ── úsek 1 ── uzel 1 ── úsek 2 ── uzel 2 ── … ── úsek 6 ── uzel 6
  │                    │                   │                       │
čerpadlo          Administrativa        Šatny          …         Sklad
(za oddělovačem)  radiátory, zón.     radiátory,               teplovzdušné
                  ventil              bez regulace             jednotky, bez regulace
                                           uzly 3–5: ohřívače VZT 3, VZT 2, VZT 1
```

Každá větev je sériově: potrubí s tělesy, regulační ventil (u zónově
řízených), vyvažovací ventil s ručním kolem 0,5–4 otáčky. Popis je
v `building.py` — stejná filozofie jako `plant.py` v bms-monitoru: přidat
větev = přidat řádek.

`building.py` popisuje dva stavy. **DESIGN** je projekt. **AS_FOUND** je to,
co technik na místě najde — a co diagnostika **nevidí**:

- vyvažovací ventily nikdy nenastavené, všechny naplno, čerpadlo na 100 %;
- zanesené sítko filtru ve větvi skladu (Kv 3,5) — proto je tam zima;
- křivka zvednutá o 6 K, protože ve skladu byla zima;
- čidlo rozdělovače ukazuje o 2,5 K méně (špatně usazená jímka) — kotelna topí o to víc;
- pohon zónového ventilu administrativy zaseklý na 70 %;
- čidlo zpátečky šaten zamrzlé na 48 °C;
- útlum vypnutý.

Simulátor z toho vyrobí týden měření (12.–18. 1. 2026, mráz až −12 °C)
s šumem a vadnými čidly. Diagnostika dostane jen to, co by se dalo
opravdu naměřit: teploty, povely ventilů, kalorimetr na rozdělovači,
otáčky a příkon čerpadla.

## Hydraulika

Soustava je žebřík. Zvolí se tlaková diference na posledním uzlu a
„pochoduje" se zpátky ke kotelně — u každého uzlu se přičte průtok odbočky
a ztráta úseku páteře. Na konci musí tlak sedět s křivkou čerpadla. Jedna
neznámá, jedna monotónní rovnice: stačí `brentq` a výpočet je robustní
i pro hodně rozladěnou soustavu (ms na jeden stav).

```
Q = Kv · √(Δp / 100)          1/Kv² = Σ 1/Kvᵢ²          H = H₀·s² − r·Q²
```

Regulační ventil má modifikovanou ekviprocentní charakteristiku (čistá
ekviprocentní by v nule nedovřela). Hydraulika a regulace se řeší
střídavě, dokud se otevření ventilů neustálí — zónový ventil přivře,
změní se tlaky v páteři a ostatním větvím přiteče víc.

## Diagnostika — řetěz, kde každý krok stojí na předchozím

**1) Čidla.** Dokud není jasné, kterým čidlům věřit, nemá smysl nic
počítat. Zamrzlé čidlo se pozná podle toho, že se nehýbe, když se všechno
okolo mění. Posunuté podle toho, že nesouhlasí s ostatními, která měří
totéž: přívod rozdělovače měří čidlo regulace, kalorimetr i čidla na
začátku každé větve — většina má pravdu.

**2) Virtuální průtokoměry.** Průtoky větví se většinou neměří. Dají se ale
dopočítat z tepelné bilance prostoru: zóna ztrácí UA·(tᵢ − tₑ), má zisky
a mění teplotu podle své kapacity, takže víme, kolik tepla do ní větev
dodala. Výkon lomeno ochlazení vody je průtok. Větev s vadným čidlem
zpátečky se dopočítá z kalorimetru (co neproteče jinudy, teče jí) a její
skutečná zpátečka ze směšovací bilance na sběrači.

**3) Model proti měření.** Hydraulický model se zjištěným nastavením
z obchůzky a s povely ventilů z BMS řekne, kolik by kudy mělo téct.
Kde se rozejdou, je ve větvi něco, o čem projekt neví:

- povel zavřít, a voda teče → **zaseklý ventil**, z průtoku se odhadne, kde stojí;
- povel otevřít, a teče málo → **ventil nedotevírá**;
- větev bez regulace bere výrazně míň, než model čeká → **překážka** (sítko,
  přivřená armatura); její Kv se dopočítá iterací;
- větev bere výrazně víc, než má → **nevyváženost**.

**4) Provoz.** Až nakonec nastavení: čerpadlo na pevných otáčkách při
proměnném odběru, křivka (95. percentil povelu zónových ventilů hluboko
pod plným otevřením), vypnutý útlum, teplá zpátečka.

Výsledek na simulovaném týdnu:

| Skryto v simulaci | Diagnostika našla |
|---|---|
| čidlo rozdělovače −2,5 K | −2,50 K |
| sítko ve skladu Kv 3,5 | Kv 3,6 |
| pohon administrativy na 70 % | ~78 % |
| křivka 3,40 / +6,0 K | 3,40 / +6,0 K |
| zamrzlé čidlo šaten | zpátečka dopočtená ze směšování 63 °C |

## Optimalizace

Pořadí je pořadí na stavbě:

1. **Opravy** — čidla, pohon, sítko. Vyvažovat s ucpaným sítkem znamená
   vyvážit i to sítko.
2. **Vyvážení metodou indexové větve.** Pro každou větev se spočítá tlak,
   který potřebuje na cílový průtok při plně otevřených ventilech. Odečtením
   ztrát páteře se najde rozhodující (indexová) větev — ta zůstane naplno,
   ostatní se přiškrtí o to, co mají navíc. Výsledek je nastavení každého
   ventilu v otáčkách a ověření modelem (± 1 %).
3. **Čerpadlo** — Δp na konci páteře, kterou indexová větev potřebuje
   (32,7 kPa); při přivírání ventilů pak samo zpomalí.
4. **Ekvitermní křivka** — pro každou venkovní teplotu nejnižší teplota
   vody, při které ještě všechny zóny dosáhnou žádané teploty, + rezerva
   1,5 K. Křivka se těmi body proloží tak, aby byla v topné sezóně
   vážené hodinami co nejnižší.
5. **Útlum** s ranním náběhem 2 h.

Neregulované větve (šatny, sklad) nemají cílový průtok z projektu, ale
průtok, který při nové křivce dá přesně žádanou teplotu — jejich tělesa
jsou předimenzovaná a s projektovým průtokem by přetápěly dál. Proto se
vyvážení a křivka počítají dvakrát po sobě.

## Úspory

Roční spotřeba se počítá **metodou teplotních tříd** na typickém roce
pro Prahu (měsíční průměry + denní chod + několikadenní vlny počasí,
pevné semínko; 3 207 denostupňů, 216 topných dní). Hodiny se roztřídí
podle venkovní teploty a obsazenosti zón, každá třída je jeden ustálený
stav. Útlum se počítá přes průměrný pokles teploty během přestávky
(exponenciální chladnutí s časovou konstantou zóny, oříznuté útlumovou
teplotou) — energie na ranní zátop je v tom zahrnutá.

Úspora každého opatření je rozdíl proti stavu po předchozím kroku.
Opatření se ovlivňují, takže zvlášť spočtené úspory se sčítat nesmí.

| Opatření | Investice | Úspora / rok |
|---|---:|---:|
| Oprava čidel | 8 500 Kč | 83 800 Kč |
| Výměna pohonu zónového ventilu | 13 800 Kč | 103 000 Kč |
| Vyčištění sítka a hydraulické vyvážení | 37 200 Kč | −31 300 Kč |
| Regulace čerpadla na Δp | 38 000 Kč | 107 300 Kč |
| Nová ekvitermní křivka | 4 500 Kč | 54 700 Kč |
| Útlum mimo provoz | 6 000 Kč | 236 200 Kč |
| **Celkem** | **108 000 Kč** | **553 600 Kč (−19,7 %)** |

Vyčištění sítka a vyvážení samo náklady **zvýší** — do studeného skladu
konečně poteče voda. Vydělá se na něm teprve nižší křivkou a regulací
čerpadla, které umožní; proto rozhraní ukazuje i balíčky opatření.
Komfort se hlídá zvlášť: po optimalizaci není žádná zóna v provozu pod
žádanou teplotou.

Ceny opatření jsou orientační bez DPH, ceny energií se berou
z `bms-monitor/plant.py` (plyn 1,60 Kč/kWh, elektřina 4,20 Kč/kWh).
NPV na 10 let s diskontem 4 % a růstem cen 3 % ročně.

## Napojení na bms-monitor

`bms_bridge.py` čte archiv `data.sqlite` (jen pro čtení) a `plant.py`:

- ceny energií — oba projekty počítají se stejnými sazbami;
- skutečnou ekvitermní křivku kotelny proloženou z `sp_calc` proti
  `t_outdoor` (z archivu vychází 3,41 / −0,1 K, v bms-monitoru je
  nastaveno 3,40 / 0);
- provoz kotelny: teploty rozdělovače a sběrače, výkon kotlů a odběr.

Větve ústředního topení bms-monitor neměří, proto je pro diagnostiku
vyrábí simulátor. Umístění se dá změnit proměnnou `BMS_MONITOR_DIR`
(výchozí `~/bms-monitor`). Bez bms-monitoru optimizer běží dál
s výchozími cenami z `building.py`.

## Spuštění

```bash
pip install -r requirements.txt
python -m web.server            # http://127.0.0.1:8010
python -m pytest                # 21 testů
```

Při startu se jednou spočítá celý řetěz (týden měření, diagnostika,
plán, roční bilance sedmi stavů) — asi 6 s. Rozhraní pak jen čte;
interaktivní simulace jednoho stavu trvá jednotky milisekund, roční
bilance kolem půl sekundy.

### Obrazovky

- **Proč tolik spotřebuje** — náklady dnes a po optimalizaci, vodopád
  ročních nákladů krok po kroku, seznam zjištění, balíčky opatření.
- **Závady** — všechna zjištění s důkazy, virtuální průtokoměry všech
  větví (bilance × model × návrh), grafy čidel a teplot prostorů.
- **Simulace větví** — schéma soustavy s průtoky, tlaky, teplotami
  a ventily; volba stavu (dnes / po každém opatření), venkovní teploty,
  křivky, režimu čerpadla, nastavení vyvažovacích ventilů; tlakový profil
  páteře a roční náklady na požádání.
- **Optimální nastavení** — křivka dnes / projekt / navržená s nutnými
  body, nastavení vyvažovacích ventilů, čerpadlo, tabulka všech parametrů.
- **Úspory a návratnost** — opatření, balíčky, kumulovaný tok peněz,
  spotřeba podle venkovní teploty, kontrola komfortu, předpoklady.
- **Napojení na bms-monitor** — stav napojení a křivka kotelny z archivu.

## Testy

`tests/` hlídají fyziku (jmenovitý bod těles, bilance hmoty a tlaku,
uzavřený ventil tlačí vodu jinam), diagnostiku (najde všechny skryté
závady, odhadne jejich velikost, virtuální průtokoměry sedí se
skutečným průtokem do 12 %, zdravá budova nemá kritická zjištění),
optimalizaci (vyvážení ± 5 %, komfort při −12 až +10 °C, křivka pod
dnešní) a API.

## Omezení

- Budova je simulovaná. U skutečné stavby se `building.py` vyplní
  z projektu a obchůzky (výkony těles, dimenze ventilů, délky páteře)
  a týden měření se vezme z BMS.
- Virtuální průtokoměry stojí na tom, že je známá tepelná ztráta zón (UA).
  Na skutečné stavbě se UA kalibruje z kalorimetru na rozdělovači —
  součet odhadnutých průtoků musí sedět s naměřeným.
- Ztráty rozvodů jsou jedno číslo pro celou páteř; ohřev teplé vody
  a cyklování kotlů se neřeší.
