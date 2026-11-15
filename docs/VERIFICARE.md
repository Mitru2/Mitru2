# Verificare

Starea la 15.11.2026: **341 de teste trec, 9 sunt sărite** (`python -m pytest`, ~17 s). Cele 9
sărite au nevoie de date oficiale care nu pot fi incluse în proiect; cu datele descărcate trec
toate (secțiunea 3).

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
| pcap / pcapng | capturile reale + capturi construite (VLAN, SLL, nanosecunde) | citite corect | – |

Statutul fiecărei convenții (verificat / raport / presupus) e în `santinela/conventions.py`.
Rămân **presupuse**: `cot.ce` (nivelul de încredere al razei CE în CoT) și SIDC-ul APP-6
(de confirmat pe APP-6 națională). Rămân **din raport** (sursa primară necitită aici):
încadrarea SAPIENT pe TCP, covarianța SAPIENT în `object_info`, STANAG 4607 D32.7 și D31,
NFFI → ADatP-36.

## 3. Testele care cer date oficiale

Datele nu sunt incluse (licențe neconfirmate sau incompatibile); se descarcă la cerere și se
verifică pe amprente SHA-256 fixate în `santinela/data/`:

```bash
python -m santinela.cli --fetch-sapient date/sapient_proto \
                        --fetch-sapient-vectors date/vectori_dstl \
                        --fetch-asterix-captures date/capturi_asterix

SANTINELA_SAPIENT_PROTO=date/sapient_proto \
SANTINELA_SAPIENT_VECTORS=date/vectori_dstl \
SANTINELA_ASTERIX_CAPTURES=date/capturi_asterix \
python -m pytest            # 350 de teste, 0 sărite
```

| Variabilă | Teste deblocate |
|---|---|
| `SANTINELA_SAPIENT_PROTO` | decodarea și validarea pe schema oficială, nodul de fuziune pe schema oficială (`test_sapient_node.py`, `test_sapient_vectors.py`) |
| `SANTINELA_SAPIENT_VECTORS` | cei 231 de vectori Dstl (`test_sapient_vectors.py`) |
| `SANTINELA_ASTERIX_CAPTURES` | traficul real Croatia Control (`test_captures.py`) |

Dacă un depozit sursă se schimbă, descărcarea se oprește la prima amprentă diferită; manifestul
se actualizează doar după revizuirea diferențelor.

## 4. Ce nu s-a putut verifica

- Comportamentul pe **echipamentele proprii**: fiecare radar/senzor trebuie verificat pe o captură
  reală (ediția UAP, semnul Doppler, referința înălțimii, altitudinea minimă, alarmele false).
- **Test Harness-ul Dstl** rulat ca proces (cere .NET); regulile lui sunt reproduse și verificate
  pe vectorii lui oficiali.
- **Parametrii trackerului** (`config.py`) pe date reale: se calibrează în mod umbră (Faza 9).
