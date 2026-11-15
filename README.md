# SANTINELA TCDE – nucleu de urmărire (plan v2)

Implementarea nucleului clasic și determinist din „SANTINELA TCDE – Plan de implementare v2”:
fuziunea rapoartelor umane („am văzut / am auzit”) cu detecțiile senzorilor într-o imagine
operațională comună (COP), pentru dronele care zboară sub orizontul radar.

Totul este explicabil și reproductibil: aceleași intrări dau aceleași ieșiri, bit cu bit.
Versiunea 0.3.0 · Python ≥ 3.10 · dependențe: numpy, scipy.

## Documentație

| Document | Conținut |
|---|---|
| [docs/OPERARE.md](docs/OPERARE.md) | instalare, linia de comandă, configurarea surselor, SAPIENT, ieșirea TAK, harta |
| [docs/ARHITECTURA.md](docs/ARHITECTURA.md) | fluxul datelor, modulele, deciziile de proiectare, ce nu e implementat |
| [docs/STANDARDE.md](docs/STANDARDE.md) | ASTERIX, SAPIENT, STANAG 4607, MISB, CoT/TAK, NFFI, Remote ID, ADS-B, NMEA; ce rămâne |
| [docs/VERIFICARE.md](docs/VERIFICARE.md) | criteriile planului, verificarea decodoarelor pe surse autoritative, erorile găsite |
| [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) | componente și date de la terți, licențe |

## Pornire rapidă

```bash
pip install -e '.[test]'
python -m pytest                                        # 341 de teste trec, 9 sărite (~17 s)
python -m santinela.cli --scenario dunare --out out/    # scenariul „Dunăre” (cu radar, detecții)
python -m santinela.cli --scenario dunare --radar-mode tracks --out out/tr   # radarul trimite trasee
python -m santinela.cli --scenario val --out out/val    # val dens de 12 ținte
python -m santinela.cli --calibrate exercitiu.csv       # Faza 0 pe date reale
python -m santinela.cli --inspect captura.pcap flux.nmea  # ce format are, ce configurare lipsește
python -m santinela.cli --conventions                   # convențiile decodoarelor, cu sursa lor
```

Harta: deschideți `viewer/index.html` și încărcați `out/cop.geojson`
(sau serviți directorul rădăcină cu `python -m http.server` și deschideți `/viewer/`).
Rularea scrie și `out/jurnal.json` (audit) și `out/cot.xml` (trasee confirmate pentru TAK).

## Ce este implementat

### Nucleul de urmărire

| Plan v2 | Modul | Stare |
|---|---|---|
| Faza 0 – conversie raport→măsurătoare, parametri pe populație cu IÎ 95 % | `measurements.py`, `calibration.py` | ✅ verificat numeric față de Monte Carlo |
| Viteze pe clase (`drones.js`, `ttx.speed`), poartă × 1,3, plic necunoscut 235 m/s | `speeds.py` | ✅ |
| Faza 1 – IMM (2 modele CV), porți χ² + cinematice, GNN cu regulă de ambiguitate, SPRT | `imm.py`, `tracker.py`, `track.py` | ✅ |
| Îmb. 1 – rapoarte întârziate prin re-filtrare din tampon; anularea unirilor | `track.py`, `tracker.py` | ✅ |
| Îmb. 2 – traseu netezit Rauch–Tung–Striebel | `imm.py`, `track.py` | ✅ |
| Îmb. 3 – corecția de sunet (343 m/s) | `measurements.py` | ✅ |
| Îmb. 4 – model de eroare și încredere Beta(2,1) pe observator | `observers.py` | ✅ |
| Îmb. 5 – informație negativă de la posturi fixe și radare (după altitudine) | `tracker.py`, `sensors.py`, `radar.py` | ✅ |
| Îmb. 6 – destinații probabile (distribuții-punte), strat separat „umbră” | `destinations.py` | ✅ |
| Îmb. 7 – mod grup pentru valuri dense | `groups.py` | ✅ |
| Faza 2 – hartă: traseu netezit, elipse, con T+5/10/15, grupuri | `export.py`, `viewer/` | ✅ GeoJSON + Leaflet |
| Faza 3 – confirmare cu regulă și autor în jurnal | `tracker.py` | ✅ |
| Faza 6 – fuziune de trasee (intersecția covarianțelor), nod de fuziune SAPIENT | `fusion.py`, `sapient_node.py` | ✅ |
| Radare: detecții polare, Doppler, trasee radar, acoperire după altitudine, înregistrare | `radar.py`, `tracker.py` | ✅ |
| Scenariile „Dunăre” și „val” | `sim.py` | ✅ date publice, observatori și radar fictivi |

### Interoperabilitate

Un singur punct de intrare, `UniversalAdapter.feed(mesaj)`, recunoaște formatul, decodează,
normalizează semantica (unități, nord, datum, HAE→MSL, declinație magnetică) și spune pe surse
ce configurare lipsește. Detalii: [docs/STANDARDE.md](docs/STANDARDE.md).

| Standard / format | Stare |
|---|---|
| **ASTERIX** CAT001/002/015/021/034/048/062/205/247, încadrare ORADIS | ✅ decodor generat din asterix-specs; ediție per sursă; domenii verificate |
| **SAPIENT** BSI Flex 335 v2.0 (protobuf, JSON, XML) | ✅ nod de fuziune bidirecțional, validare pe schema oficială Dstl |
| **STANAG 4607** (GMTI) | ✅ decodare și codare |
| **MISB ST 0601 / 0903 / 1201** (STANAG 4609), KLV din MPEG-TS | ✅ |
| **Cursor-on-Target** + **TAK Protocol v1**, **APP-6D / 2525D** | ✅ intrare și ieșire (UDP multicast, TCP, TLS) |
| **NFFI** 1.3/1.4 (ADatP-36) | ✅ forțe proprii; FFI MTF semnalat |
| **Remote ID** (ASTM F3411 / EN 4709-002): BT 4/5, Wi-Fi Beacon, NAN | ✅ |
| **ADS-B** Mode S DF17/18 (Beast, AVR, SBS, readsb) | ✅ CRC-24, CPR; calibrarea radarelor |
| **NMEA 0183** (`TTM`, `TLL`, `HDT`), JSON de producător | ✅ |
| **pcap / pcapng** | ✅ |
| **WMM2025** (declinație), **EGM96** (geoid) | ✅ |
| Link 16, JREAP, Link 11/22, VMF, OTH-Gold, ADatP-3 | ⛔ doar prin gateway acreditat (punct de extensie `register_decoder`) |

### Verificare

341 de teste trec (350 cu datele oficiale descărcate). Decodoarele sunt verificate pe surse
independente: asterix-specs și trafic ASTERIX real (1940/1940 câmpuri identice cu decodorul de
referință), cei 231 de vectori ai validatorului SAPIENT Dstl, formulele disectorului Wireshark
pentru STANAG 4607, exemplele din standardele MISB, valorile oficiale de test WMM2025 și NGA
EGM96. Verificarea a găsit și corectat 7 erori de specificație. Detalii:
[docs/VERIFICARE.md](docs/VERIFICARE.md).

## Ce rămâne

- **Faza 4** (OpenTAKServer, VPN, PWA), **Faza 5** (rutare, vizibilitate pe relief, sarcini),
  **Faza 8** (AI) și **Faza 9** (calibrare în mod umbră) cer infrastructură, date de relief,
  acces la senzori sau date din exploatare.
- ASTERIX CAT129/016/063/065, STANAG 4676, DIS/CAP, verificarea criptografică a autentificării
  Remote ID.
- **Capturi reale de la fiecare echipament** și confirmarea cu producătorul: ediția UAP, semnul
  Doppler, referința înălțimii, altitudinea minimă, alarmele false.

Parametrii din `config.py` sunt puncte de pornire alese, nu cifre din literatură; se calibrează
în mod umbră. Punctele din secțiunea „Ce rămâne neverificat” a planului rămân valabile.

## Structura depozitului

```
santinela/            pachetul Python (nucleu + adaptoare + ieșiri)
santinela/data/       WMM2025, grila EGM96 regională, manifeste cu amprente pentru datele oficiale
tests/                testele (pytest)
tools/                generatorul specificațiilor ASTERIX + licența asterix-specs
viewer/               vizualizatorul hărții (Leaflet)
docs/                 documentația
```
