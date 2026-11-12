# SANTINELA TCDE – nucleu de urmărire (plan v2)

Implementarea nucleului clasic și determinist din „SANTINELA TCDE – Plan de implementare v2”:
fuziunea rapoartelor umane („am văzut / am auzit”) cu detecțiile senzorilor într-o imagine
operațională comună (COP), pentru dronele care zboară sub orizontul radar.

Totul este explicabil și reproductibil: aceleași intrări dau aceleași ieșiri, bit cu bit.

## Pornire rapidă

```bash
pip install -e '.[test]'
python -m pytest                                        # 97 de teste, ~20 s
python -m santinela.cli --scenario dunare --out out/    # scenariul „Dunăre” (cu radar, detecții)
python -m santinela.cli --scenario dunare --radar-mode tracks --out out/tr   # radarul trimite trasee
python -m santinela.cli --scenario val --out out/val    # val dens de 12 ținte
python -m santinela.cli --calibrate exercitiu.csv       # Faza 0 pe date reale
python -m santinela.cli --inspect captura.bin flux.nmea # ce format are, ce configurare lipseste
```

Harta: deschideți `viewer/index.html` și încărcați `out/cop.geojson`
(sau serviți directorul rădăcină cu `python -m http.server` și deschideți `/viewer/`).

## Ce este implementat

| Plan v2 | Modul | Stare |
|---|---|---|
| Faza 0 – conversie raport→măsurătoare, parametri pe populație cu IÎ 95% | `measurements.py`, `calibration.py` | ✅ verificat numeric față de Monte Carlo |
| Viteze pe clase din fișe (`drones.js`, `ttx.speed`), poartă × 1,3, plic necunoscut 235 m/s | `speeds.py` | ✅ |
| Faza 1 – IMM (2 modele CV), porți χ² + cinematice, GNN cu regulă de ambiguitate, SPRT | `imm.py`, `tracker.py`, `track.py` | ✅ |
| Îmb. 1 – rapoarte întârziate prin re-filtrare din tampon; anulare unire cu același cod | `track.py`, `tracker.py` | ✅ |
| Îmb. 2 – traseu netezit Rauch–Tung–Striebel | `imm.py`, `track.py` | ✅ |
| Îmb. 3 – corecția de sunet (343 m/s) | `measurements.py` | ✅ |
| Îmb. 4 – model de eroare și încredere Beta(2,1) pe observator | `observers.py` | ✅ |
| Îmb. 5 – informație negativă de la posturi fixe (P_D 0,7) | `tracker.py`, `sensors.py` | ✅ |
| Îmb. 6 – destinații probabile (distribuții-punte), strat separat „umbră” | `destinations.py` | ✅ |
| Îmb. 7 – mod grup pentru valuri dense | `groups.py` | ✅ |
| Faza 2 – hartă: traseu netezit, elipse, con T+5/10/15, grupuri | `export.py`, `viewer/` | ✅ GeoJSON + vizualizator Leaflet |
| Faza 3 – confirmare cu regulă și autor în jurnal | `tracker.py` | ✅ |
| Faza 6 – fuziune de trasee prin intersecția covarianțelor; atributul „emisie mesh/4G” | `fusion.py`, `track.py` | ✅ (fără poarta SAPIENT) |
| Scenariul de test „Dunăre” | `sim.py` | ✅ date publice, observatori și radar fictivi |
| Radare: detecții polare, Doppler, trasee radar prin CI, acoperire după altitudine, înregistrare | `radar.py`, `tracker.py` | ✅ |
| Adaptor ASTERIX CAT048 / CAT062 / CAT021 | `asterix.py` | ✅ după specificația publică; **de validat pe înregistrări reale** |
| Adaptor universal (ASTERIX, SAPIENT, STANAG 4607, CoT, NFFI, Remote ID, NMEA, JSON), cu detectarea formatului | `universal.py` | ✅ |

### Criterii de acceptare acoperite de teste

- **Faza 0**: conversia raport→măsurătoare coincide cu Monte Carlo (medie < 25 m la 2,5 km,
  abateri standard < 5%); intervalele de încredere de 95% conțin valorile reale.
- **Faza 1**: un raport sosit cu întârziere dă exact traseul de la sosirea la timp; anularea unei
  uniri dă exact traseele dinainte; reluarea scenariului dă același rezultat și același jurnal.
- **Faza 2**: elipsa de 95% conține ținta simulată în 95% din cazuri (cerința: 90–98%);
  scenariul cu 12 ținte paralele trece în mod grup.
- **Faza 3**: fiecare confirmare are regula și autorul în jurnal; un observator cu rapoarte false
  repetate nu poate confirma singur un traseu.
- **Faza 7**: destinația corectă are probabilitatea maximă înainte de jumătatea drumului în ≥ 70% din cazuri.
- **Radar**: ASTERIX pe octeți construiți manual după specificație, Doppler (eroarea vitezei radiale
  scade de peste 2 ori), trasee radar printr-un viraj de 90° rămân un singur traseu local, penalizarea
  radar doar peste altitudinea minimă, înregistrarea găsește 1° / 120 m / 0,8 s pe o țintă cooperativă.
- **Scenariul „Dunăre”**: 4 ținte → exact 4 trasee confirmate (seed-urile 1–3 în teste, 1–8 verificate manual, ambele moduri radar),
  ținta cu modem mesh marcată „posibil operator în buclă”, observatorul care raportează numai fals
  nu confirmă nimic.

## Radare

- **Intrare**: blocuri ASTERIX CAT048 → `Cat048Adapter` → `RadarPlot` (I048/040, opțional Doppler
  I048/120 și înălțime I048/110 sau nivel de zbor I048/090) sau `RadarTrackReport` (I048/161 + 042 +
  200 + 210). Radarele se identifică prin SAC/SIC; ora zilei se raportează la `tod_ref`, cu trecere
  peste miezul nopții. Alte categorii ASTERIX din flux sunt ignorate.
- **Detecții**: distanța oblică devine distanță la sol când se știe înălțimea; poziția și covarianța
  se calculează prin cuadratură. Viteza radială intră în filtru ca măsurătoare neliniară (EKF).
- **Trasee radar**: se fuzionează prin intersecția covarianțelor (erorile actualizărilor succesive
  ale aceluiași traseu sunt corelate). Numărul de traseu al radarului e respectat: actualizările
  lui merg la același traseu local, inclusiv la manevre.
- **Acoperire după altitudine**: tăcerea radarului scade scorul unui traseu tentativ doar dacă ținta
  e raportată deasupra altitudinii minime de detecție a radarului și în interiorul orizontului radar
  (4/3 din raza Pământului). Fără altitudine raportată se presupune „foarte jos”, deci radarul nu
  penalizează nimic — cazul Luncavița. Un raport ulterior „foarte jos” anulează retroactiv penalizarea.
- **Încredere**: radarele și ceilalți senzori au și ei scor Beta; un radar cu verdicte false
  repetate nu mai confirmă singur un traseu, iar densitatea lui de alarme false crește.
- **Înregistrare** (eroare de azimut, de distanță, decalaj de ceas): cele mai mici pătrate ponderate,
  față de o referință fără radarul calibrat. Corecția se aplică doar dacă erorile standard sunt sub
  0,25° / 40 m / 0,5 s. Referința bună sunt țintele cooperative (`Tracker.register_radar`, ex. ADS-B);
  cu o referință din câteva rapoarte umane estimarea e refuzată și jurnalul arată de ce.
- **Scenariul „Dunăre”**: radarul (min. 300 m) nu vede țintele A, B, C (foarte jos) și vede ținta D
  (Orlan-10 la 1200 m); ecourile false de păsări nu produc trasee confirmate.

Rămân de verificat cu producătorul/operatorul radarului: convenția de semn pentru Doppler,
referința înălțimii, altitudinea minimă reală din diagrama de acoperire, densitatea de alarme false.
CAT062, CAT021, SAPIENT și NMEA sunt acum acoperite de adaptorul universal (mai jos).

## Adaptorul universal

Un singur punct de intrare pentru toate sursele: `UniversalAdapter.feed(mesaj)` recunoaște formatul
și întoarce intrări pentru tracker.

| Format | Recunoscut după | Ce produce | Ce cere |
|---|---|---|---|
| ASTERIX CAT048 | blocuri cu lungime coerentă | detecții sau trasee radar | poziția radarului (SAC/SIC → lat, lon, antenă) |
| ASTERIX CAT062 | idem | trasee de sistem | nimic dacă are I062/105 (WGS-84); altfel punctul de referință |
| ASTERIX CAT021 | idem | aeronave ADS-B → calibrarea radarelor | nimic |
| alte categorii ASTERIX (CAT034, CAT240 …) | idem | sunt ignorate, apar în raport | – |
| SAPIENT JSON (BSI Flex 335) | chei `detectionReport` / `statusReport` | detecții sau trasee | poziția nodului (se învață din `statusReport`) |
| SAPIENT binar (protobuf) | antet de 4 octeți cu lungimea | – | un decodor protobuf dat ca `sapient_decoder` |
| NMEA 0183 (`TTM`, `TLL`, `HDT`) | `$…*hh`, cu suma de control verificată | trasee radar navale | poziția radarului pentru `TTM`; capul pentru relevmente relative |
| JSON de producător | orice alt JSON | detecții sau trasee | un profil (`JsonProfile`); fără el, câmpurile sunt ghicite după nume |

- **Sursele cu poziții absolute** (lat/lon) sunt acceptate imediat, dar fără informație negativă până
  la configurarea acoperirii. **Sursele polare** (distanță/azimut) așteaptă poziția senzorului.
- **Aeronavele cooperative** (ADS-B, plot-uri SSR/Mode S, trasee de sistem cu cod de transponder) nu
  intră în trackerul de drone. Plot-urile Mode S ale unui radar se împerechează cu ADS-B după adresa
  ICAO, iar radarul se calibrează automat (azimut, distanță, ceas).
- `requirements()` spune, pe surse, ce lipsește; `config_template()` dă un șablon de completat, iar
  `UniversalAdapter.from_config(fișier)` îl încarcă. Din linia de comandă: `--inspect` și `--config`.
- Clasificările sigure de non-dronă din SAPIENT (pasăre, persoană, vehicul, ≥ 0,8) sunt ignorate.

Nesigur, de confirmat pe documentul de interfață al fiecărei surse: în SAPIENT, `Location.x` e
longitudinea (`sapient_x_is_lon`); ediția UAP a fiecărui echipament ASTERIX; unitățile fluxurilor JSON
ghicite. Captura PCAP nu e citită direct: se extrage întâi conținutul UDP/TCP.

## Standarde NATO și conexe

| Standard | Ce e | Stare | Ce intră în sistem |
|---|---|---|---|
| **SAPIENT** (BSI Flex 335 v2, adoptat de NATO pentru C-UAS) | senzori anti-dronă | ✅ JSON și **binar** | detecții, trasee; poziția nodului din `statusReport` |
| **STANAG 4607** (AEDP-7) | GMTI, radare de supraveghere | ✅ | detecții cu viteză pe linia de vizare (Doppler) |
| **ASTERIX** CAT048, CAT062, CAT021 | radar, trasee de sistem, ADS-B (folosit în NATINAMDS/ACCS) | ✅ | detecții, trasee, calibrare pe ADS-B |
| **ASTERIX** CAT001, CAT002, CAT034 | formate radar vechi, mesaje de serviciu | ✅ | plot-uri/trasee; ora completă și **poziția radarului** din I034/120 |
| **Cursor-on-Target** (MIL-STD-2525 / APP-6) | TAK/ATAK | ✅ intrare **și ieșire** | ținte aeriene, poziții proprii; `out/cot.xml` pentru ATAK |
| **NFFI** (STANAG 5527) | forțe proprii | ✅ | pozițiile echipelor, strat separat pe hartă |
| **Remote ID** (ASTM F3411 / EN 4709-002) | drone cooperative | ✅ | marchează traseele identificate (`annotate_tracks`) |
| **Link 16** (STANAG 5516 / MIL-STD-6016), **JREAP** (STANAG 5518 / MIL-STD-3011), **Link 11/22**, **VMF**, **OTH-Gold**, **ADatP-3** | legături de date tactice | 🔒 interfață pregătită | prin `register_decoder()`, după specificația oficială |

- **SAPIENT binar**: `UniversalAdapter(sapient_proto_dir="…")` citește definițiile oficiale `.proto`
  (publicate de Dstl) cu un decodor protobuf propriu, fără dependențe. Fără ele, raportul cere directorul.
- **Legături de date cu distribuție controlată**: specificațiile (MIL-STD-6016, MIL-STD-3011 etc.) nu
  sunt publice și nu au fost reproduse. Sursa se declară cu `declare_source("GW-L16", "link16")`;
  până la înregistrarea decodorului, `requirements()` spune ce lipsește. Decodorul se scrie în mediul
  acreditat și întoarce aceleași obiecte (`RadarTrackReport`, `SensorDetection`), deci restul
  sistemului nu se schimbă.
- Toate decodoarele sunt scrise după specificațiile publice și verificate doar pe date construite în
  teste; înainte de utilizare se validează pe înregistrări reale de la fiecare echipament (ediția UAP
  ASTERIX, semnul vitezei radiale în STANAG 4607, tabelul de clasificare, schema NFFI folosită).

## Decizii de proiectare

- **Starea unui traseu este o funcție pură de tamponul lui** (măsurători + evenimente de informație
  negativă, sortate după oră și id). Rapoartele întârziate se inserează la locul lor și traseul se
  re-filtrează; un raport care ajunge în ordine folosește o cale incrementală care face aceleași
  operații în aceeași ordine, deci rezultatul e identic.
- **GNN pe scanări**: o scanare = rapoartele unei surse la același moment. Surse diferite pot vedea
  aceeași dronă în același ciclu, deci nu intră în aceeași atribuire unu-la-unu.
- **Regula de ambiguitate**: dacă un al doilea traseu are cost de asociere aproape la fel de bun
  (marjă 2,0), raportul nu se atribuie automat; intră în coada operatorului și contează pentru
  pragul de mod grup. Excepție: un traseu tânăr (≤ 2 rapoarte) ale cărui rapoarte intră toate în
  poarta traseului ales e un duplicat și se unește automat (anulabil).
- **Regula de confirmare**: SPRT peste prag **și** (≥ 2 surse independente, sau un senzor, sau o
  singură sursă cu încredere ≥ 0,6). Densitatea de rapoarte false λ_FA a sursei crește odată cu
  rapoartele marcate false, deci scorul unui observator nesigur nici nu mai urcă.
- **Conversia raportului** folosește cuadratură Gauss–Hermite 7×7 pe (azimut, ln distanță): o
  transformată unscented simplă subestima împrăștierea transversală cu ~12% la σ_ln = 0,35.
- **Netezirea** RTS rulează pe momentele combinate ale IMM (aproximare standard); conul și
  poziția curentă rămân cele filtrate.
- **Destinațiile** se calculează ca verosimilitate a predicției filtrului de a ajunge în fiecare
  destinație, marginalizată peste ora de sosire. Rezultatul e un strat separat și nu intră în con.

## Ce nu este implementat (și de ce)

- **Faza 4** (OpenTAKServer, VPN, punte CoT, PWA cu Capacitor), **Faza 5** (Valhalla, vizibilitate
  pe relief, atribuirea sarcinilor), **adaptorul SAPIENT** din Faza 6 și **Faza 8** (componente AI)
  cer infrastructură, date de relief, acces la senzori sau decizii de acreditare care nu există în
  acest depozit. `sensors.py` conține matricea senzor–variantă cu valorile necunoscute marcate
  `None`, de completat cu producătorul.
- **Faza 9** (calibrare și activarea automatului) cere date din mod umbră; `Tracker.merge_metrics()`
  dă deja rata de uniri automate anulate (criteriul < 2%).
- `drones.js` nu este în acest depozit: tabelul din plan e copiat în `speeds.py`, iar
  `--drones-js cale/drones.js` îl înlocuiește la pornire cu valorile din fișe.

Parametrii din `config.py` sunt puncte de pornire alese, nu cifre din literatură; se calibrează în
mod umbră. Punctele din secțiunea „Ce rămâne neverificat” a planului rămân valabile.
