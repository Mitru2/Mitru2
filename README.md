# SANTINELA TCDE – nucleu de urmărire (plan v2)

Implementarea nucleului clasic și determinist din „SANTINELA TCDE – Plan de implementare v2”:
fuziunea rapoartelor umane („am văzut / am auzit”) cu detecțiile senzorilor într-o imagine
operațională comună (COP), pentru dronele care zboară sub orizontul radar.

Totul este explicabil și reproductibil: aceleași intrări dau aceleași ieșiri, bit cu bit.

## Pornire rapidă

```bash
pip install -e '.[test]'
python -m pytest                                        # 43 de teste, ~12 s
python -m santinela.cli --scenario dunare --out out/    # scenariul „Dunăre”
python -m santinela.cli --scenario val --out out/val    # val dens de 12 ținte
python -m santinela.cli --calibrate exercitiu.csv       # Faza 0 pe date reale
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
| Scenariul de test „Dunăre” | `sim.py` | ✅ date publice, observatori fictivi |

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
- **Scenariul „Dunăre”**: 3 ținte → exact 3 trasee confirmate (seed-urile 1–3 în teste, 1–8 verificate manual),
  ținta cu modem mesh marcată „posibil operator în buclă”, observatorul care raportează numai fals
  nu confirmă nimic.

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
