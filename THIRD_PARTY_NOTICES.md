# Componente și date de la terți

Acest fișier enumeră materialele de la terți **incluse** în proiect, cele **folosite doar ca
referință** (fără cod copiat) și cele **descărcate la cerere și neredistribuite**, cu licențele
lor. Codul SANTINELA nu folosește biblioteci de la terți la rulare în afară de numpy și scipy.

## 1. Incluse în proiect

### asterix-specs — specificațiile ASTERIX structurate (BSD-3-Clause)

`santinela/asterix_spec.py` este generat de `tools/gen_asterix_spec.py` din proiectul
asterix-specs (Zoran Bošnjak), revizia 5cf64b635424891b9f7e5d8fc51f6531e5790f89. Textul complet
al licenței e în `tools/LICENSE.asterix-specs` și în antetul fișierului generat.

```
Copyright (c) 2019, KZPS
All rights reserved.
```

Redistribuirea și utilizarea sunt permise în condițiile BSD-3-Clause (vezi fișierul de licență).
Specificațiile ASTERIX propriu-zise sunt publicate de EUROCONTROL.

### klvdata — tabelul de mapări MISB ST 0601 (MIT)

Tabelul tag → (nume, tip, domeniu, interval) din `santinela/misb.py` și vectorii din
`tests/test_misb.py` (exemplele din MISB ST 0601.11 §8) provin din klvdata 0.0.3.

```
Copyright (c) 2017 Matthew Pare (paretech@gmail.com)
```

### jmisb — exemplele MISB ST 0903 (MIT)

Exemplele de pachete de locație și de codare IMAPB din `tests/test_misb.py` (anexa ST 0903)
provin din testele jmisb.

```
Copyright 2021 jmisb.org
```

### pygeomag — valorile de test WMM2025 (MIT)

Valorile oficiale de test WMM2025 din `tests/test_geomag.py` sunt cele publicate de NOAA, în
forma redistribuită de pygeomag 1.1.0. Implementarea din `santinela/geomag.py` este proprie.

```
Copyright (c) 2023 Justin Myers
```

### egm96 — grila regională EGM96 (MIT)

`santinela/data/egm96_ro_5min.npy` (41–50 N, 18–33 E, pas 5′) este extrasă din grila globală
EGM96 de 5′ a pachetului egm96 0.3.0. Modelul EGM96 este produs de NGA și NASA.

```
Copyright (c) 2026 Jonas Ehrlich
```

### takproto — descriptorii TAK Protocol v1 (MIT)

Numele, numerele și tipurile câmpurilor TAK Protocol v1 din `santinela/cot.py` (`TAK_SCHEMA`) au
fost citite ca date din descriptorii takproto 3.0.1; codul takproto nu a fost rulat și nu e inclus.

```
Copyright Sensors & Signals LLC https://www.snstac.com
Copyright 2020 Delta Bravo-15 <deltabravo15ga@gmail.com>
```

### World Magnetic Model 2025 — coeficienți (domeniu public)

`santinela/data/WMM2025.COF` — coeficienții publicați de NOAA NCEI și British Geological Survey.
WMM este produs de guvernul SUA (NOAA) și distribuit fără restricții de copyright în SUA.

### Textul licenței MIT (pentru klvdata, jmisb, pygeomag, egm96, takproto)

```
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## 2. Folosite doar ca referință (fără cod copiat)

| Sursă | Licență | Ce s-a folosit |
|---|---|---|
| Wireshark, disectorul `packet-stanag4607.c` | GPL-2.0 | formulele de codare SA32/BA32/B16 și tabelul de clasificare, ca fapte de verificare; codul nu e copiat |
| opendroneid-core-c | Apache-2.0 | aranjamentul purtătoarelor radio Remote ID (BT AD, Wi-Fi IE, NAN), ca fapte de interfață |
| „The 1090 MHz Riddle” (Junzi Sun) | – | mesajele Mode S de referință din `tests/test_p1_sources.py` |
| standardele BSI Flex 335, AEDP-7, MISB, ASTM F3411, ADatP-36, MIL-STD-2525D | – | interfețele publicate |

## 3. Descărcate la cerere, neredistribuite

Aceste materiale **nu** sunt în proiect. În `santinela/data/` sunt doar manifeste cu adresa,
commitul și amprenta SHA-256 a fiecărui fișier.

| Material | Licență | Manifest | Comandă |
|---|---|---|---|
| Definițiile `.proto` SAPIENT BSI Flex 335 v2.0 (Dstl, `SAPIENT-Proto-Files`) | neconfirmată | `sapient_bsi_flex_335_v2_0.json` | `--fetch-sapient DIR` |
| Vectorii de test ai validatorului Dstl (`BSI-Flex-335-v2-Test-Harness`, commit 6a2999d) | Apache-2.0, Crown copyright | `sapient_test_vectors.json` | `--fetch-sapient-vectors DIR` |
| Capturile ASTERIX și ieșirile de referință Croatia Control (`CroatiaControlLtd/asterix`, commit 790bca7) | GPL-2.0 | `asterix_test_captures.json` | `--fetch-asterix-captures DIR` |

## 4. Încărcate de vizualizator din rețea

`viewer/index.html` încarcă Leaflet 1.9.4 de pe unpkg.com (BSD-2-Clause, © 2010–2023 Volodymyr
Agafonkin, © 2010–2011 CloudMade) și dalele OpenStreetMap (date © contributorii OpenStreetMap,
ODbL). Nu sunt incluse în proiect.
