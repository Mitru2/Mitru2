# Arhitectura SANTINELA TCDE

Documentul descrie cum circulă datele prin sistem, ce face fiecare modul și de ce a fost ales
fiecare mecanism. Pentru operare vezi [OPERARE.md](OPERARE.md), pentru standarde
[STANDARDE.md](STANDARDE.md), pentru verificare [VERIFICARE.md](VERIFICARE.md).

## 1. Fluxul datelor

```
 surse                         adaptor                        tracker                     ieșiri
 ─────                         ───────                        ───────                     ──────
 rapoarte umane ──────────────────────────────────┐
 (am văzut / am auzit)                            │
                                                  ▼
 ASTERIX, SAPIENT, 4607, ──► UniversalAdapter ──► Measurement ──► Tracker ──► Track ──► GeoJSON (hartă)
 CoT, NFFI, Remote ID,       detectare format     (z, R, H)        GNN pe scanări      ├─► CoT / TAK
 ADS-B, MISB/KLV, NMEA,      decodare             RadarTrackReport SPRT, IMM, RTS      ├─► SAPIENT (nod de fuziune)
 DIS, JSON, pcap             normalizare          NegativeEvent    uniri, grupuri      ├─► CAP 1.2 (protecție civilă)
                             stare senzori, DRIP                                       └─► jurnal (audit)
                             (convenții)          cooperative ──► înregistrarea radarelor
```

1. **Adaptorul universal** (`universal.py`) primește octeți sau text, recunoaște formatul
   (`detect_format`), îl decodează cu modulul de standard și normalizează semantica (unități,
   nord, datum, HAE→MSL, declinație) după registrul `conventions.py`.
2. Rezultatul e una din intrările trackerului:
   - `SensorDetection` → `Measurement` (poziție cu covarianță, opțional viteză radială);
   - `RadarTrackReport` (traseu extern, fuzionat prin intersecția covarianțelor);
   - poziții cooperative (ADS-B, SSR, Remote ID) — nu intră ca drone, servesc la calibrarea
     radarelor și la marcarea traseelor;
   - pozițiile forțelor proprii (NFFI, CoT „friendly”).
3. **Rapoartele umane** (`Report`) sunt convertite în `measurements.py`: azimut + distanță
   estimată (log-normală) + întârzierea sunetului → poziție gaussiană prin cuadratură
   Gauss–Hermite 7×7.
4. **Trackerul** (`tracker.py`) asociază, confirmă, unește și șterge trasee; fiecare traseu
   (`track.py`) e o funcție pură de tamponul lui de măsurători.
5. **Ieșirile** (`export.py`, `cot.py`, `sapient_node.py`, `trackmodel.py`) pornesc toate de la
   același model comun `CommonTrack` (identitate APP-6, sursă, calitate, elipsă 95 %).

## 2. Modulele

### Nucleul de urmărire (plan v2, Fazele 0–3, 6–7)

| Modul | Rol |
|---|---|
| `config.py` | `Params` (dataclass înghețată): porți, praguri SPRT, P_D, λ_FA, timpi |
| `geo.py` | proiecție locală (est, nord) în metri; elipse |
| `speeds.py` | viteze pe clase (`drones.js` / `ttx.speed`), poartă ×1,3, plic necunoscut 235 m/s |
| `observers.py` | model de eroare și încredere Beta(2,1) pe observator și pe senzor |
| `measurements.py` | `Report`, `SensorDetection`, `Measurement`; conversia raport → măsurătoare; corecția de sunet; momentele polar → cartezian |
| `imm.py` | IMM cu două modele CV (liniștit / manevră), actualizare EKF pentru viteza radială, actualizare CI cu ω, netezitor RTS |
| `track.py` | `Track`: tampon de măsurători + evenimente de informație negativă; re-filtrare la rapoarte întârziate; SPRT |
| `tracker.py` | asociere GNN pe scanări cu regula de ambiguitate, confirmare cu regulă și autor, uniri anulabile, informație negativă, mod grup, înregistrarea radarelor, identitatea dată de operator |
| `groups.py` | mod grup pentru valuri dense (Îmb. 7) |
| `destinations.py` | destinații probabile prin distribuții-punte (Îmb. 6), strat „umbră” |
| `fusion.py` | intersecția covarianțelor (Julier–Uhlmann) pentru trasee externe |
| `sensors.py` | matricea senzor–variantă, posturi fixe (P_D 0,7) |
| `radar.py` | `RadarSite` (poziție, antenă, altitudine minimă, orizont radar, declinație, ediții ASTERIX), `RadarRegistration` (cele mai mici pătrate ponderate) |
| `calibration.py` | Faza 0: parametri de eroare pe populație, cu IÎ 95 %, din CSV-ul exercițiului |
| `sim.py` | scenariile „Dunăre” și „val”, generator de rapoarte |
| `export.py` | GeoJSON pentru `viewer/index.html` |
| `cli.py` | linia de comandă |

### Stratul de interoperabilitate

| Modul | Standard / format |
|---|---|
| `universal.py` | detectare format, decodare, normalizare, configurarea surselor, raport de cerințe |
| `conventions.py` | registrul convențiilor semantice, fiecare cu statut și sursă |
| `asterix.py` + `asterix_spec.py` | ASTERIX: decodor generic condus de specificație + interpretoare pentru CAT001/002/015/021/034/048/062/205/247; încadrare ORADIS |
| `protowire.py` | protobuf fără dependențe: citește `.proto`, decodează/codează, validează opțiunile, JSON canonic |
| `sapient_proto.py` | definițiile oficiale SAPIENT pe amprente, regulile validatorului Dstl, vectorii de test |
| `sapient_node.py` | nod de fuziune SAPIENT bidirecțional (TCP, prefix de lungime) |
| `stanag4607.py` | GMTI (AEDP-7): decodare și codare |
| `cot.py` | Cursor-on-Target XML și TAK Protocol v1 (mesh/stream), emițător UDP/TCP/TLS |
| `trackmodel.py` | modelul comun de traseu, SIDC APP-6D / 2525D, tip CoT |
| `nffi.py` | NFFI XML 1.3/1.4; recunoaște FFI MTF |
| `remoteid.py` | Remote ID ASTM F3411 / EN 4709-002 (BT 4/5, Wi-Fi Beacon, NAN) |
| `drip.py` | autentificarea Remote ID: DRIP (RFC 9575 / 9374), Ed25519, cSHAKE128, pagini cu FEC, stări pe emițător |
| `dis.py` | DIS Entity State (IEEE 1278.1), conversii ECEF ↔ WGS-84, pentru exerciții |
| `cap.py` | ieșire CAP 1.2 (OASIS): alerte pentru protecția civilă |
| `adsb.py` | Mode S DF17/18: CRC-24, CPR global/local; Beast, AVR, SBS, readsb JSON |
| `misb.py` | MISB ST 0601 / ST 0903 (VMTI) / ST 1201, KLV din MPEG-TS |
| `geomag.py` | declinația magnetică WMM2025 |
| `geoid.py` | ondulația EGM96, HAE ↔ MSL |
| `pcap.py` | cititor pcap/pcapng (Ethernet, VLAN, SLL/SLL2, IP brut, loopback) |

## 3. Decizii de proiectare

- **Determinism.** Starea unui traseu e o funcție pură de tamponul lui (măsurători + evenimente
  de informație negativă, sortate după oră și id). Un raport întârziat se inserează la locul lui
  și traseul se re-filtrează integral; un raport în ordine folosește o cale incrementală care face
  aceleași operații în aceeași ordine. Reluarea dă aceleași trasee și același jurnal, bit cu bit.
- **GNN pe scanări.** O scanare = rapoartele unei surse la același moment (`(sursă, round(t))`).
  Surse diferite pot vedea aceeași dronă în același ciclu, deci nu intră în aceeași atribuire
  unu-la-unu.
- **Regula de ambiguitate.** Dacă un al doilea traseu are un cost aproape la fel de bun
  (marjă 2,0), raportul merge în coada operatorului și contează pentru pragul de mod grup.
  Excepție: un traseu tânăr (≤ 2 rapoarte) ale cărui rapoarte intră toate în poarta traseului
  ales e un duplicat și se unește automat (anulabil).
- **Confirmare.** SPRT (scor Blackman) peste prag **și** (≥ 2 surse independente, sau un senzor,
  sau o singură sursă cu încredere ≥ 0,6). λ_FA a unei surse crește cu rapoartele ei marcate
  false. Fiecare confirmare are regula și autorul în jurnal.
- **Conversia raportului** se face prin cuadratură Gauss–Hermite 7×7 pe (azimut, ln distanță):
  o transformată unscented simplă subestima împrăștierea transversală cu ~12 % la σ_ln = 0,35.
- **Informație negativă.** Posturile fixe și radarele scad scorul unui traseu tentativ doar
  acolo unde chiar ar fi trebuit să vadă ținta: radarul numai deasupra altitudinii minime și în
  interiorul orizontului radar 4120·(√h_a + √h_t) m. Fără altitudine raportată se presupune
  „foarte jos”, deci radarul nu penalizează nimic.
- **Trasee externe** (radar, CAT062, CAT015, SAPIENT cu `track_info`) se fuzionează prin
  intersecția covarianțelor, pentru că erorile actualizărilor succesive ale aceluiași traseu
  extern sunt corelate. Numărul de traseu extern e respectat.
- **Înregistrarea radarelor** (azimut, distanță, ceas) folosește ținte cooperative (ADS-B, Mode S,
  ținte de oportunitate) și se aplică doar sub pragurile de eroare standard
  (0,25° / 40 m / 0,5 s); altfel e refuzată, cu motivul în jurnal.
- **Netezirea RTS** rulează pe momentele combinate ale IMM; conul T+5/10/15 și poziția curentă
  rămân cele filtrate.
- **Destinațiile** sunt un strat separat, nu intră în con.
- **Identitatea ostilă** nu se atribuie automat: doar operatorul (`Tracker.set_identity`, cu autor).
- **Decodoarele nu ghicesc în tăcere.** Ce nu se știe (ediție ASTERIX, poziția senzorului,
  unități, nordul de referință) apare în `requirements()`; valorile în afara domeniului din
  specificație refuză înregistrarea în loc s-o citească greșit.
- **Starea senzorilor contează pentru informația negativă.** Un radar cu NOGO, canal PSR
  neselectat, suprasarcină, „fără plot-uri” sau deconectat (I034/050, CAT063) nu mai penalizează
  traseele prin tăcere; un radar doar SSR/Mode S nu penalizează niciodată drone.
- **Identitatea cooperativă se câștigă, nu se declară.** Un Remote ID cu autentificare DRIP
  invalidă face traseul „suspect”; doar o semnătură validă cu cheie de încredere, corelată cu
  senzorii, susține „neutru — autentificat”.
- **Simularea nu se amestecă cu realitatea.** DIS intră doar în modul exercițiu, iar alertele CAP
  au statutul ales explicit (`Exercise` în scenarii).
- **Fără dependențe de rețea la rulare.** Singurele dependențe sunt numpy și scipy. Protobuf,
  ASTERIX, KLV, pcap, WMM, EGM96, Keccak/cSHAKE128 și Ed25519 sunt implementate în proiect; datele oficiale care nu pot fi
  redistribuite (definițiile SAPIENT, vectorii Dstl, capturile Croatia Control) se descarcă la
  cerere și se verifică pe amprente SHA-256 fixate.

## 4. Ce nu este implementat (și de ce)

- **Faza 4** (OpenTAKServer, VPN, punte CoT, PWA cu Capacitor), **Faza 5** (Valhalla,
  vizibilitate pe relief, atribuirea sarcinilor) și **Faza 8** (componente AI) cer infrastructură,
  date de relief, acces la senzori sau decizii de acreditare care nu există în acest depozit.
  `sensors.py` conține matricea senzor–variantă cu valorile necunoscute marcate `None`.
- **Faza 9** (calibrarea și activarea automatului) cere date din mod umbră;
  `Tracker.merge_metrics()` dă deja rata de uniri automate anulate (criteriul < 2 %).
- **Legături de date cu distribuție controlată** (Link 16, JREAP, Link 11/22, VMF, OTH-Gold,
  ADatP-3) nu se implementează în afara mediului acreditat — vezi [STANDARDE.md](STANDARDE.md).
- `drones.js` nu e în depozit: tabelul din plan e copiat în `speeds.py`, iar
  `--drones-js cale/drones.js` îl înlocuiește cu valorile din fișe.

Parametrii din `config.py` sunt puncte de pornire alese, nu cifre din literatură; se calibrează
în mod umbră.
