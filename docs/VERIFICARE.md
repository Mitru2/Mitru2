# Verificare

Starea la 16.11.2026: **388 de teste trec, 13 sunt sărite** (`python -m pytest`, ~27 s). Cele 13
sărite au nevoie de date oficiale care nu pot fi incluse în proiect; cu datele descărcate trec
toate **401** (secțiunea 3).

## 1. Criteriile planului v2

| Criteriu | Test | Rezultat |
|---|---|---|
| Faza 0: conversia raport → măsurătoare coincide cu Monte Carlo | `test_phase0_measurements.py` | medie < 25 m la 2,5 km, abateri standard < 5 %; IÎ 95 % conțin valorile reale |
| Faza 1: un raport întârziat dă exact traseul de la sosirea la timp | `test_phase1_core.py` | identic; anularea unei uniri dă exact traseele dinainte; reluarea dă același jurnal |
| Faza 2: elipsa de 95 % conține ținta în 90–98 % din cazuri | `test_phase2_map.py` | 95 %; scenariul cu 12 ținte paralele trece în mod grup |
| Faza 3: confirmare cu regulă și autor; observatorul fals nu confirmă singur | `test_phase3_confirmation.py` | îndeplinit |
| Faza 7: destinația corectă are probabilitatea maximă înainte de jumătatea drumului | `test_phase6_7_fusion_destinations.py` | ≥ 70 % din cazuri |
| Scenariul „Dunăre”: 4 ținte → exact 4 trasee confirmate | `test_radar.py`, `test_outputs.py` | seed-urile 1–3 în teste (1–8 verificate manual), ambele moduri radar; ținta cu modem mesh marcată „posibil operator în buclă” |
| Radar: Doppler, trasee radar prin viraj de 90°, penalizare doar peste altitudinea minimă, înregistrare | `test_radar.py` | eroarea vitezei radiale scade de peste 2 ori; un singur traseu local; înregistrarea găsește 1° / 120 m / 0,8 s |

## 2. Decodoarele, verificate pe surse autoritative

Principiu: un test de tip „codez și decodez cu același cod” nu prinde o eroare de specificație
(așa a scăpat inițial eroarea SA32 din STANAG 4607). De aceea fiecare decodor e verificat pe
octeți sau valori produse **independent** de el.

| Decodor | Sursa independentă | Rezultat | Erori găsite și corectate |
|---|---|---|---|
| ASTERIX — structură | asterix-specs (transcrierea structurată EUROCONTROL), revizia 5cf64b63 | UAP-uri și articole generate; interpretoarele scrise de mână egale câmp cu câmp cu specificația | I001/120 LSB (2⁻⁸ NM/s); I001/150 lipsă; I062/380 TIS extins |
| ASTERIX — trafic real | capturi Croatia Control (commit 790bca7) + ieșirile decodorului lor de referință | CAT034/048: 1940/1940 câmpuri cu aceiași octeți, toate valorile egale; CAT001/002 (ORADIS) și CAT062 identice | captura neconformă `asterix.pcap` (latitudine 4330°) e refuzată prin domenii, nu citită greșit |
| ASTERIX — domenii | restricțiile din asterix-specs | aplicate la decodare | 3 restricții imposibile din sursă (I015/627, I015/631) ignorate, documentat în generator |
| SAPIENT | definițiile oficiale Dstl (17 fișiere, SHA-256 fixate) + 231 de vectori ai validatorului Dstl (commit 6a2999d) | 38/38 vectori valizi trec fără abateri; 191/193 invalizi detectați | 2 vectori depășiți (regula e comentată chiar în validatorul Dstl), documentați în manifest |
| SAPIENT — mesajele proprii | validarea pe schema oficială | Registration și toate mesajele trimise de nod trec fără abateri | – |
| STANAG 4607 | formulele disectorului Wireshark (`packet-stanag4607.c`) | octeți construiți manual | SA32 (factor 2) și B16 corectate; tabelul de clasificare corectat |
| MISB ST 0601 | 66 de exemple din ST 0601.11 §8 (prin testele klvdata) | toate | tagurile 43/44: interval 0..510 (×2 pixeli), nu 0..512 |
| MISB ST 0903 | exemplele din anexa ST 0903 (prin testele jmisb) | toate | – |
| WMM2025 | valorile oficiale de test NOAA (12 puncte; 100 de puncte) | D/I la 0,005°, X/Y/Z la 0,001 nT; 100 de puncte sub 0,05 nT | – |
| EGM96 | cele 6 puncte de test NGA | sub 5 mm (grila globală); interpolarea regională < 0,06 m | – |
| Mode S / ADS-B | mesajele de referință publice (ex. `8D4840D6202CC371C32CE0576098`) | identificare, poziție CPR, viteză, CRC | – |
| TAK Protocol v1 | descriptorii takproto 3.0.1, citiți ca date | numere și tipuri de câmpuri identice | – |
| ASTERIX — starea senzorilor | trafic real Croatia Control | I034/050 (PSR/SSR/Mode S) bit cu bit și CAT065 identice cu decodorul de referință | generatorul pierdea prefixul grupurilor imbricate: nume suprapuse în I021/040, I021/090, I048/020, I063/060 |
| ASTERIX CAT063 — biasuri | formulele din observațiile I063/080–091 | aplicate exact (θ − b; (ρ − b)/(1 + câștig)) | – |
| cSHAKE128 / Keccak | exemplele NIST SP 800-185 #1 și #2; SHAKE128 din hashlib | identice | – |
| Ed25519 | vectorii RFC 8032 §7.1; biblioteca `cryptography` (independentă) | identice (semnare și verificare) | – |
| DRIP (Remote ID) | exemplul complet RFC 9575 anexa B.2.1 | DET ↔ cheie, cele 8 hash-uri de mesaj, semnăturile Wrapper și Manifest, paginile cu FEC reproduse octet cu octet; orice pagină pierdută refăcută | exemplul se abate de la text în 4 puncte (secțiunea 2.1) |
| DIS Entity State | disectorul Wireshark `packet-dis.c`; PDU-ul de test open-dis-python; capturile Wireshark | valorile așteptate de open-dis; 287 + 20 de PDU-uri coerente (inclusiv 10 PDU-uri într-o datagramă) | – |
| CAP 1.2 | schema oficială OASIS CAP-v1.2.xsd și textul CAP-v1.2-os | alertele, actualizările și anulările trec schema; regulile din text verificate separat | – |
| pcap / pcapng | capturile reale + capturi construite (VLAN, SLL, nanosecunde) | citite corect | – |

### 2.1 Abaterile exemplului RFC 9575 față de text

Exemplul din anexa B.2.1 e semnat corect, dar diferă de textul normativ în patru puncte. Codul
urmează textul și acceptă și variantele exemplului, cu o notă în rezultat:

1. Link-ul e trimis cu SAM `0x04` (Frame în tabelul 1), nu `0x01`; e recunoscut sigur pentru că
   DET-ul copilului e hash-ul cheiei lui.
2. Hash-ul manifestului curent include și hash-ul Link-ului (textul, §4.4.3 pasul 4, îl omite).
3. Hash-ul Link-ului din Manifest e calculat peste avizul semnat (fără octetul SAM), nu peste
   pagini (§4.4.3.1).
4. VNB/VNA sunt în epoca Unix, nu în epoca F3411 (2019-01-01); epoca e configurabilă (`drip_epoch`).

Legătura DET ↔ cheie se reproduce cu HOST_ID = cheia Ed25519 de 32 de octeți, fără eticheta
curbei din figura 2 a RFC 9374.

Statutul fiecărei convenții (verificat / raport / presupus) e în `santinela/conventions.py`.
Rămân **presupuse**: `cot.ce` (nivelul de încredere al razei CE în CoT), SIDC-ul APP-6
(de confirmat pe APP-6 națională) și regula „DIS doar ca exercițiu”. Rămân **din raport** (sursa
primară necitită aici): încadrarea SAPIENT pe TCP, covarianța SAPIENT în `object_info`, STANAG 4607
D32.7 și D31, NFFI → ADatP-36, valorile DIS Force ID (din surse NPS derivate din SISO-REF-010).

## 3. Testele care cer date oficiale

Datele nu sunt incluse (licențe neconfirmate sau incompatibile); se descarcă la cerere și se
verifică pe amprente SHA-256 fixate în `santinela/data/`:

```bash
python -m santinela.cli --fetch-sapient date/sapient_proto \
                        --fetch-sapient-vectors date/vectori_dstl \
                        --fetch-asterix-captures date/capturi_asterix \
                        --fetch-reference date/referinta

SANTINELA_SAPIENT_PROTO=date/sapient_proto \
SANTINELA_SAPIENT_VECTORS=date/vectori_dstl \
SANTINELA_ASTERIX_CAPTURES=date/capturi_asterix \
SANTINELA_REFERENCE=date/referinta \
python -m pytest            # 401 teste, 0 sărite
```

| Variabilă | Teste deblocate |
|---|---|
| `SANTINELA_SAPIENT_PROTO` | decodarea și validarea pe schema oficială, nodul de fuziune pe schema oficială (`test_sapient_node.py`, `test_sapient_vectors.py`) |
| `SANTINELA_SAPIENT_VECTORS` | cei 231 de vectori Dstl (`test_sapient_vectors.py`) |
| `SANTINELA_ASTERIX_CAPTURES` | traficul real Croatia Control (`test_captures.py`, `test_asterix_status.py`) |
| `SANTINELA_REFERENCE` | schema oficială CAP 1.2 și capturile DIS Wireshark (`test_dis_cap.py`; validarea CAP cere și `lxml`) |

Dacă un depozit sursă se schimbă, descărcarea se oprește la prima amprentă diferită; manifestul
se actualizează doar după revizuirea diferențelor.

## 4. Ce nu s-a putut verifica

- Comportamentul pe **echipamentele proprii**: fiecare radar/senzor trebuie verificat pe o captură
  reală (ediția UAP, semnul Doppler, referința înălțimii, altitudinea minimă, alarmele false).
- **Test Harness-ul Dstl** rulat ca proces (cere .NET); regulile lui sunt reproduse și verificate
  pe vectorii lui oficiali.
- **DRIP pe drone reale**: verificat pe exemplul RFC și pe lanțuri sintetice; lanțul real de
  încredere (cheile autorităților HDA/RAA) depinde de registrul național DRIP, care încă nu există.
- **ASTERIX CAT129** și **STANAG 4676**: fără specificație publică disponibilă.
- **Parametrii trackerului** (`config.py`) pe date reale: se calibrează în mod umbră (Faza 9).
