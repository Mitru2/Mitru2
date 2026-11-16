# Standarde și formate suportate

Toate sursele intră printr-un singur punct, `UniversalAdapter.feed(mesaj)`, care recunoaște
formatul și întoarce intrări pentru tracker. Convențiile pe care se bazează decodoarele sunt
scrise într-un singur loc (`santinela/conventions.py`), fiecare cu statut:
**verificat** (citit din sursa oficială), **raport** (din raportul de interoperabilitate din
13.11.2026, care citează sursa) sau **presupus** (de confirmat). Lista completă:
`python -m santinela.cli --conventions`.

Cum a fost verificat fiecare decodor: [VERIFICARE.md](VERIFICARE.md).

## 1. Tabel general

| Format | Recunoscut după | Produce | Cere de la operator |
|---|---|---|---|
| **ASTERIX** CAT048 | blocuri cu lungime coerentă | detecții sau trasee radar | poziția radarului (SAC/SIC → lat, lon, antenă), dacă nu vine prin CAT034 |
| ASTERIX CAT062 | idem | trasee de sistem (CI) | nimic dacă are I062/105; altfel punctul de referință |
| ASTERIX CAT021 | idem | aeronave ADS-B → calibrarea radarelor | nimic |
| ASTERIX CAT001/002/034 | idem | plot-uri vechi; ora completă și poziția radarului (I034/120) | – |
| ASTERIX CAT015 (INCS) | idem | trasee/detecții necooperative (radar pasiv, multistatic) | poziția senzorului pentru formele polare |
| ASTERIX CAT205 | idem | poziția emițătorului (radiogoniometrie) sau doar relevment | poziția senzorului pentru relevment |
| ASTERIX CAT016 | idem | punctul de referință INCS (pentru CAT015 polar), emițătoare/receptoare | – |
| ASTERIX CAT063 | idem | starea și biasurile fiecărui senzor, de la SDPS | poziția senzorului (SAC/SIC) ca să se aplice |
| ASTERIX CAT065 | idem | starea SDPS-ului, loturi, rapoarte de serviciu | – |
| ASTERIX CAT247 | idem | edițiile declarate de sursă | – |
| ASTERIX cu încadrare ORADIS | antet ORADIS | ca mai sus | – |
| **SAPIENT** protobuf (BSI Flex 335 v2.0) | 4 octeți de lungime LE + mesaj | detecții, trasee, starea nodului | definițiile `.proto` oficiale (descărcate, verificate pe amprente) |
| SAPIENT JSON / XML | chei `detectionReport` / `statusReport` | idem | poziția nodului (se învață din `statusReport`) |
| **STANAG 4607** (GMTI) | antet de pachet + segmente | detecții cu viteză radială | – |
| **MISB ST 0601 / 0903 / 1201** (STANAG 4609) | cheia UL KLV; MPEG-TS (0x47) | ținte VMTI cu poziție, ținta desemnată | – |
| **Cursor-on-Target** XML / **TAK Protocol v1** | `<event`, `bf 01 bf`, `bf`+varint | detecții, trasee, forțe proprii | – |
| **NFFI** 1.3/1.4 (ADatP-36) | rădăcina XML NFFI | forțe proprii | – |
| FFI MTF (APP-11) | text MTF | semnalat, nu decodat | un gateway acreditat |
| **Remote ID** (ASTM F3411 / EN 4709-002) | mesaj de 25 o., BT AD `16 FA FF 0D`, Wi-Fi IE `DD FA0BBC 0D`, NAN | drona cooperativă + poziția operatorului + starea autentificării DRIP | adresa emițătorului ca `source`; ancore DRIP de încredere (opțional) |
| **DIS** Entity State (IEEE 1278.1) | antet DIS coerent, lungimi înlănțuite | ținte de exercițiu, echipe proprii | `dis_exercise=True` (altfel doar raportat) |
| **ADS-B** Mode S DF17/18 | Beast (`1a`), AVR (`*…;`), SBS (`MSG,`), readsb JSON | aeronave cooperative | poziția receptorului pentru CPR local (opțional) |
| **NMEA 0183** `TTM`, `TLL`, `HDT` | `$…*hh`, suma de control verificată | trasee radar navale | poziția radarului pentru `TTM`; capul pentru relevmente relative |
| JSON de producător | orice alt JSON | detecții sau trasee | un profil (`JsonProfile`); fără el, câmpurile sunt ghicite după nume |
| **pcap / pcapng** | magia fișierului | conținutul UDP/TCP trece prin detectarea de mai sus | – |

Principii comune:

- **Sursele cu poziții absolute** (lat/lon) sunt acceptate imediat, dar fără informație negativă
  până la configurarea acoperirii. **Sursele polare** (distanță/azimut) așteaptă poziția senzorului.
- **Aeronavele cooperative** (ADS-B, plot-uri SSR/Mode S, trasee de sistem cu cod de transponder,
  Remote ID) nu intră în trackerul de drone ca ținte noi. Plot-urile Mode S ale unui radar se
  împerechează cu ADS-B după adresa ICAO și radarul se calibrează automat; țintele de
  oportunitate (aeronave cooperative văzute de radar) sunt excluse din tracker și folosite la
  calibrare.
- **Altitudinile** din tracker sunt față de nivelul mării: HAE se convertește cu EGM96.
- **Azimuturile magnetice** se corectează automat cu declinația WMM2025 la poziția și data
  senzorului (sau cu `mag_declination_deg` din configurare).
- `requirements()` spune, pe surse, ce lipsește; `config_template()` dă un șablon de completat.

Ieșiri: GeoJSON (hartă), CoT/TAK, SAPIENT (nod de fuziune) și **CAP 1.2** (alerte pentru
protecția civilă, secțiunea 11).

## 2. ASTERIX (EUROCONTROL)

- **Decodor condus de specificație.** UAP-urile și structura articolelor (fix, extins, repetitiv,
  explicit, compus) sunt generate automat din proiectul asterix-specs (`tools/gen_asterix_spec.py`
  → `santinela/asterix_spec.py`, revizia 5cf64b63). Interpretoarele scrise de mână pentru
  câmpurile folosite de tracker sunt testate câmp cu câmp contra specificației generate.
- **Ediții.** Ediția fiecărei categorii vine din CAT247 sau se declară per SAC/SIC
  (`asterix_editions`). Ediția majoră trebuie să coincidă cu tabelele (CAT048 1.32, CAT062 1.21,
  CAT021 2.7, CAT034 1.29, CAT015 1.2, CAT205 1.0, CAT247 1.3); altfel sursa e oprită și apare în
  `requirements()`.
- **Domenii.** Valorile în afara domeniului din specificație (ex. latitudine > 90°) refuză
  înregistrarea și sunt raportate ca abateri: e semn de ediție greșită sau de date corupte.
- **Ora.** Ora zilei se raportează la `tod_ref`, cu trecere peste miezul nopții; ora trunchiată
  CAT001 (I001/141) se completează din CAT002/034 sau din ora de sosire.
- **CAT048**: I048/040 (polar), I048/120 (Doppler), I048/110 (înălțime 3D) sau I048/090 (nivel de
  zbor); trasee: I048/161 + 042 + 200 + 210. Distanța oblică devine distanță la sol când se știe
  înălțimea; poziția și covarianța se calculează prin cuadratură.
- **CAT015 / CAT205** (supraveghere necooperativă, radiogoniometrie): CAT015 cu WGS-84 și
  covarianță corelată se folosește direct; formele polare se raportează la punctul de referință
  al sistemului din **CAT016** (I016/400, înălțime MSL din I016/405) sau la poziția configurată;
  țintele simulate sunt ignorate.
- **Starea radarului (I034/050)**: `NOGO` = „datele trebuie aruncate de un SDPS operațional” →
  plot-urile și traseele radarului sunt aruncate cât timp starea persistă; `RDPR` (repornirea
  procesorului) → numerele de traseu ale radarului se resetează în tracker; canal PSR neselectat,
  suprasarcină sau sursă de timp invalidă → tăcerea radarului nu mai e informație negativă; o
  stație care raportează doar SSR/Mode S (fără parte PSR) nu dă niciodată informație negativă
  pentru drone (nu le poate vedea).
- **CAT063 (starea senzorilor de la SDPS)**: conexiune (operațional / degradat / inițializare /
  neconectat), PSR NOGO, suprasarcini, „nu se primesc plot-uri” → suspendă informația negativă;
  posibil atac de spoofing (SPO) → semnalat. Biasurile PSR se aplică plot-urilor după formulele
  specificației: θ = θ_m − b_az, ρ = (ρ_m − b_ρ) / (1 + câștig). Estimarea proprie a trackerului
  continuă doar ca verificare (jurnal: `inregistrare_radar_comparatie`). Biasul de marcare a
  timpului (I063/070) e înregistrat, dar neaplicat: specificația nu-i definește semnul.
- **CAT065 (starea SDPS)**: operațional / degradat / neconectat, suprasarcină, sursă de timp,
  sfârșit de lot, rapoarte de serviciu (radar principal căzut, sincronizat pe radarul de rezervă…).
- Textele valorilor din tabele (ex. „Not currently connected”) sunt generate din asterix-specs
  (`TABLES` în `asterix_spec.py`).

## 3. SAPIENT (BSI Flex 335 v2.0, baza AEDP-4869)

- **Nod de fuziune bidirecțional** (`sapient_node.py`): Registration/RegistrationAck,
  StatusReport, DetectionReport, Alert/AlertAck, Task (`look_at`)/TaskAck, Error; nod-copil spre
  un nod superior (`send_upstream`). TCP, fiecare mesaj precedat de lungimea lui pe 4 octeți LE.
- **Decodor propriu** (`protowire.py`): citește fișierele `.proto` ca date, decodează și codează
  binar, mapează JSON canonic (camelCase, RFC 3339, enumerări ca text) și validează opțiunile
  `is_mandatory` / `is_uuid` / `is_ulid` plus regulile suplimentare ale validatorului Dstl.
- **Definițiile oficiale nu sunt incluse** (licența nu a putut fi confirmată): se descarcă cu
  `--fetch-sapient` și se verifică pe amprentele SHA-256 din
  `santinela/data/sapient_bsi_flex_335_v2_0.json`. Orice diferență oprește încărcarea.
- **Convenții verificate în `location.proto`**: `Location.x` = longitudine (LAT_LNG) / est (UTM);
  datum WGS84_E = înălțime elipsoidală, WGS84_G = față de geoid; zona UTM din Registration
  (format `30N`). `RangeBearing`: unități grade/radiani × m/km; nord adevărat, magnetic (+ WMM),
  grilă UTM (+ convergența meridianelor) sau capul platformei.
- **Toleranță documentată.** Mostrele Dstl pun lat/lon în câmpuri UTM_M; ordinea se alege după
  plauzibilitate și apropierea de nod, cu notă de abatere. `strict=True` respinge orice abatere.
- SAPIENT v2.0 nu are câmp standard pentru viteza radială; covarianța circulă ca perechi text în
  `object_info` (nestandardizat, statut „raport”).

## 4. STANAG 4607 (AEDP-7) — GMTI

- Antet de pachet, segmente Mission, Job, Dwell; masca de existență D2…D32.18 de la bitul cel mai
  semnificativ; codări SA32 (45/2³⁰), BA32 (360/2³²), SA16/BA16 (90/2¹⁴), B16 = v/128.
- D32.7 (viteza pe linia de vizare) în cm/s, corectată pentru mișcarea platformei, pozitivă la
  depărtare; viteza ambiguă (wrap) nu se folosește.
- Tabelul de clasificare corectat (nu există clasă UAS); clasa țintei nu devine clasă de dronă.
  Clasificarea pachetului, tipul datelor (exercițiu/simulat/sintetizat) și modelul de geoid din
  segmentul Job sunt citite; datele simulate sunt semnalate.

## 5. MISB / STANAG 4609 (EO/IR)

- **ST 0601** (UAS Datalink Local Set): mapări liniare domeniu → interval pe tag, indicatorul de
  eroare, suma de control pe cuvinte de 16 biți; poziția platformei, a senzorului, ținta
  desemnată (taguri 40–42) cu erorile CE90/LE90.
- **ST 0903** (VMTI): ținte mobile cu pachetul de locație (tag 17, IMAPB) sau decalaje față de
  centrul cadrului; versiunile < 4 cu codarea liniară veche.
- **ST 1201** (IMAPB) implementat generic; **MPEG-TS**: extragerea KLV din PID-urile de date.

## 6. Cursor-on-Target și TAK

- **Intrare** XML CoT și TAK Protocol v1: mesh UDP (`bf 01 bf` + TakMessage), stream TCP
  (`bf` + varint + TakMessage). Rolul se deduce din tip (`a-h`, `a-f`, `a-u`…).
- **Ieșire** (`cot.py`): UID stabil per traseu, elipsa de 95 %, `stale`, mesaje de ștergere,
  `CotPublisher` pe UDP multicast 239.2.3.1:6969, TCP sau TLS; XML sau TAK Protocol.
- **APP-6D / MIL-STD-2525D**: SIDC de 20 de cifre (UAV = set 01, entitate 110300); identitatea
  „ostil” doar prin decizia operatorului.

## 7. NFFI / FFT

NFFI XML 1.3/1.4 (STANAG 5527, acum ADatP-36): poziții proprii, folosite ca forțe proprii
(nu ca ținte). FMN Spiral 5 cere FFI MTF (APP-11): e recunoscut și semnalat ca nevoie de gateway.

## 8. Remote ID (ASTM F3411 / ASD-STAN EN 4709-002)

Mesaje de 25 de octeți: Basic ID, Location/Vector, Authentication (prezența semnalată), Self-ID,
System (poziția operatorului), Operator ID, Message Pack. Purtătoare: Bluetooth 4 (legacy) și 5,
Wi-Fi Beacon, Wi-Fi NAN. Drona cu Remote ID devine cooperativă; un traseu care se potrivește cu ea
e marcat.

**Autentificarea** (`drip.py`) e verificată criptografic după DRIP (RFC 9575 / RFC 9374):

- paginile mesajului Authentication se reasamblează pe emițător (sursa = adresa MAC Bluetooth /
  Wi-Fi); o pagină pierdută, inclusiv pagina 0, se reface din paritatea XOR (FEC);
- **DRIP Link**: avizul unei autorități (HDA/RAA) pentru DET-ul și cheia dronei; cu o ancoră de
  încredere configurată (`drip_trust_anchors`), cheia dronei devine „de încredere”; fără ea, cheia
  e folosită (legată criptografic de DET prin hash cSHAKE128), dar nu e de încredere;
- **DRIP Wrapper** (până la 4 mesaje semnate, și pe transport extins) și **DRIP Manifest**
  (hash-uri ale mesajelor trimise în clar): semnătura Ed25519 a dronei, mesajele confirmate în clar,
  fereastra VNB–VNA (dacă se dă ora Unix `utc_ref_unix`);
- starea pe emițător după anexa A din RFC 9575: Partial, Unsupported, Unverifiable, Verified,
  Trusted, Unverified, Questionable, Conflicting;
- o autentificare invalidă e semnalată ca **posibilă falsificare**, iar traseul potrivit cu acel
  Remote ID devine „suspect”, nu „neutru”;
- autentificările ASTM de tip 1–4 depind de un serviciu extern și sunt raportate „Unsupported”.

O semnătură validă arată doar că emițătorul are cheia; corelarea poziției semnate cu senzorii
(`annotate_tracks`) e validarea cerută de RFC 9575 §6.4.2.

## 9. ADS-B direct (1090 MHz)

Mode S DF17/18 cu CRC-24 și decodare CPR globală (pereche par/impar) sau locală (față de
receptor); formatele Beast (cu escape), AVR, SBS/BaseStation și readsb JSON. Folosit pentru
aeronavele cooperative și calibrarea radarelor, fără CAT021.

## 10. DIS (exerciții)

Entity State PDU (IEEE 1278.1): antet, Entity ID, Force ID, tip de entitate, viteză și poziție
în ECEF WGS-84 (convertite în latitudine/longitudine/înălțime și viteză est/nord), marcaj. Datele
DIS sunt simulate: intră în imaginea operațională doar cu `dis_exercise=True`, ca sursă de tip
„exercitiu” fără informație negativă. Țintele aeriene ne-prietene devin trasee de exercițiu,
forțele prietene (Force ID 1) poziții de echipe; restul e ignorat, cu notă.

## 11. CAP 1.2 (alerte pentru protecția civilă)

`cap.py` transformă un traseu confirmat într-o alertă OASIS CAP 1.2: zona = elipsa de 95 % a
poziției curente plus elipsele conului T+5/10/15 min, ca poligoane WGS-84; eveniment, severitate
și răspuns după identitatea APP-6 (ostil → `Severe`, `Shelter`; suspect → `Moderate`, `Monitor`);
certitudine după starea traseului; `Update` / `Cancel` cu referințe. Alerta implicită e
`Restricted` (pentru autorități); statutul (`Actual`, `Exercise`, `Test` …) se alege explicit,
iar instrucțiunile pentru populație sunt textul autorității. Scenariile scriu `out/cap/*.xml` cu
statutul `Exercise`.

## 12. Legături de date cu distribuție controlată

Link 16, JREAP, Link 11/22, VMF, OTH-Gold și ADatP-3 **nu se implementează în afara mediului
acreditat**. Arhitectura recomandată: un **gateway acreditat** (comercial sau național) livrează
spre SANTINELA CoT, ASTERIX CAT062 sau SAPIENT, iar SANTINELA le citește ca pe orice altă sursă.
Punctul de extensie `declare_source()` / `register_decoder()` există doar pentru un decodor scris
în mediul acreditat; fără el, sursa apare în `requirements()` cu nevoia de gateway.

## 13. Ce rămâne

| Element | Stare |
|---|---|
| ASTERIX CAT129 | neimplementat: nu am găsit o specificație EUROCONTROL publicată pentru această categorie |
| STANAG 4676 (trasee ISR, AEDP-12) | neimplementat: schema XSD e în anexa C a AEDP-12, cu distribuție contra cost / restricționată |
| DIS: alte PDU-uri (Fire, Detonation, Transmitter, Signal) | neimplementate; doar Entity State |
| Autentificarea Remote ID ASTM de tip 1–4 | nesuportată (cere serviciul extern al validatorului) |
| Rularea Test Harness-ului Dstl complet (ca proces) | nerulată; regulile lui sunt reproduse și testate pe vectorii oficiali |
| Capturi reale de la echipamentele proprii | lipsesc: necesare pentru fiecare radar/senzor (ediția UAP, convenția Doppler, referința înălțimii) |

De confirmat cu producătorul/operatorul fiecărui radar: convenția de semn pentru Doppler,
referința înălțimii, altitudinea minimă reală din diagrama de acoperire, densitatea de alarme
false, ediția UAP.
