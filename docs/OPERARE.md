# Ghid de operare

## 1. Instalare

Cerințe: Python ≥ 3.10, numpy ≥ 1.24, scipy ≥ 1.10. Fără alte dependențe la rulare.

```bash
cd santinela-tcde
python -m venv .venv && . .venv/bin/activate
pip install -e '.[test]'
python -m pytest                     # 388 de teste trec, 13 sărite (vezi VERIFICARE.md)
```

## 2. Scenariile demonstrative

```bash
python -m santinela.cli --scenario dunare --out out/                    # radar care trimite detecții
python -m santinela.cli --scenario dunare --radar-mode tracks --out out/tr   # radar care trimite trasee
python -m santinela.cli --scenario dunare --radar-mode off --out out/fara    # fără radar
python -m santinela.cli --scenario val --out out/val                    # val dens de 12 ținte (mod grup)
python -m santinela.cli --scenario dunare --seed 5                      # alt seed
```

Fiecare rulare scrie în directorul `--out`:

| Fișier | Conținut |
|---|---|
| `cop.geojson` | traseele (netezite), elipsele 95 %, conul T+5/10/15, grupurile, destinațiile (strat „umbră”), observatorii, radarele și acoperirea lor |
| `jurnal.json` | jurnalul de audit: asocieri, confirmări (regula și autorul), uniri, anulări, refuzuri de înregistrare |
| `cot.xml` | traseele confirmate ca evenimente Cursor-on-Target, pentru TAK/ATAK |
| `cap/<traseu>.xml` | alertele CAP 1.2 pentru protecția civilă (statut `Exercise` în scenarii) |

**Harta:** deschideți `viewer/index.html` și încărcați `out/cop.geojson`, sau serviți
directorul rădăcină cu `python -m http.server` și deschideți `/viewer/`. Vizualizatorul încarcă
Leaflet de pe unpkg.com, deci are nevoie de internet pentru fundal și bibliotecă.

## 3. Faza 0 — calibrarea pe exercițiu

```bash
python -m santinela.cli --calibrate exercitiu.csv
```

Intrare: CSV-ul exercițiului de coordonate (raport vs. poziție reală). Ieșire: parametrii de
eroare pe populație cu intervale de încredere de 95 %, de trecut în `config.py` / `observers.py`.

## 4. Conectarea surselor reale

### 4.1 Recunoaștere și cerințe

```bash
python -m santinela.cli --inspect captura.pcap flux.nmea sapient.bin
```

Comanda recunoaște formatul fiecărui fișier (inclusiv pcap/pcapng), decodează, și spune pe surse
ce lipsește (poziția radarului, ediția ASTERIX, nordul de referință, unitățile…). Apoi tipărește
un șablon de configurare de completat.

### 4.2 Configurarea surselor

```json
{
  "tod_ref": 0.0,
  "cat048_mode": "plots",
  "sources": [
    {
      "sensor_id": "RADAR-ISACCEA",
      "lat": 45.27, "lon": 28.46, "antenna_height": 15.0,
      "sac": 25, "sic": 3,
      "max_range": 60000, "min_alt": 300,
      "pd": 0.85, "sigma_range": 50, "sigma_az_deg": 0.5, "sigma_rr": 1.0,
      "lambda_fa": 1e-10,
      "asterix_editions": {"48": "1.32", "34": "1.29", "63": "1.7", "65": "1.6"}
    },
    {
      "sensor_id": "RADAR-NAVAL-1", "lat": 45.15, "lon": 29.65,
      "heading_known": true, "heading_deg": 0.0, "kind": "radar"
    },
    {
      "sensor_id": "SAPIENT-NOD-7", "lat": 45.20, "lon": 28.80,
      "mag_declination_deg": null, "kind": "senzor", "negative_info": false
    }
  ],
  "drip_trust_anchors": [
    {"det": "2001:0030:0280:1405:a29b:3ff4:2226:c04e", "key": "<cheia Ed25519 a autoritatii, 64 hex>", "name": "HDA nationala"}
  ],
  "json_profiles": [
    {"name": "producator-x", "match": "targets", "source_const": "X-1",
     "lat": "pos.lat", "lon": "pos.lon", "altitude": "pos.alt",
     "time": "ts", "time_format": "epoch_ms", "speed": "spd", "speed_scale": 0.27778,
     "heading": "hdg"}
  ]
}
```

```bash
python -m santinela.cli --inspect captura.pcap --config surse.json
```

Câmpurile unei surse (`RadarSite`): `sensor_id`, `lat`, `lon` (obligatorii); `antenna_height`
(m deasupra terenului), `max_range` (m), `min_alt` (m, altitudinea minimă de detecție fiabilă din
diagrama de acoperire), `pd`, `sigma_range` (m), `sigma_az_deg`, `sigma_rr` (m/s), `lambda_fa`
(plot-uri false / m²), `sac`/`sic` (ASTERIX), `negative_info` (False pentru surse fără acoperire
cunoscută), `heading_deg` + `heading_known` (azimut relativ), `mag_declination_deg` (null = WMM
automat), `asterix_editions`, `kind` (`radar` | `sistem` | `senzor`).

Un profil JSON (`JsonProfile`) dă căile cu puncte spre câmpuri: `lat`/`lon` sau
`range`/`azimuth`/`elevation`, `time` + `time_format` (`auto` | `iso` | `epoch` | `epoch_ms` |
`tod`), `speed`/`heading` sau `vx`/`vy`, `altitude`, `drone_class`, `sigma_pos`, factori de scară
`range_scale` și `speed_scale`.

### 4.3 Din cod

```python
from santinela import LocalFrame, Tracker
from santinela.universal import UniversalAdapter

tracker = Tracker(frame=LocalFrame(45.27, 28.46))        # vezi și santinela/sim.py: make_tracker
ad = UniversalAdapter.from_config("surse.json", tracker=tracker)

# o dată pe ciclu (ex. 30 s): tot ce a sosit în ciclu, apoi informația negativă
items = []
for datagrama, t, port in sosite_in_ciclu:
    items += ad.feed(datagrama, received_at=t, source=f"udp:{port}")
tracker.ingest(items, now=t_ciclu)
tracker.apply_negative_info(t_ciclu)

ad.annotate_tracks(tracker)      # marchează traseele cooperative (Remote ID, ADS-B)
print(ad.requirements())         # ce mai lipsește din configurare
```

Bucla completă, cu rapoarte întârziate: `santinela/sim.py`, funcția `run`.

`feed_file(cale)` citește un fișier întreg (pcap/pcapng, flux binar, text pe linii).

Parametri ai adaptorului (constructor `UniversalAdapter(...)` sau `from_config(..., **kw)`):
`apply_sdps_bias` (biasurile PSR din CAT063, implicit da), `utc_ref_unix` (ora Unix pentru t = 0,
pentru ferestrele DRIP), `drip_epoch`, `dis_exercise` (DIS intră în COP doar dacă e `True`),
`dis_sigma_m`.

### 4.4 Remote ID și autentificarea DRIP

Dați ca `source` adresa emițătorului (MAC Bluetooth / Wi-Fi): paginile mesajului Authentication se
reasamblează pe emițător. Starea autentificării apare în nota sursei, în `CooperativeReport.auth`,
în jurnal (`autentificare_remoteid`) și pe traseu (`cooperative_auth`). O autentificare invalidă
apare în `errors` ca posibilă falsificare și face traseul „suspect”. Cheile autorităților de
încredere se dau în `drip_trust_anchors` (DET-ul scris cu toate cele 8 grupe) sau cu `adapter.drip.trust_anchor(det, cheie)`.

## 5. SAPIENT

```bash
python -m santinela.cli --fetch-sapient sapient_proto     # descarcă și verifică amprentele SHA-256
python -m santinela.cli --sapient-server 5020 --sapient-proto sapient_proto
```

Nodul de fuziune ascultă pe TCP (mesaje cu prefix de lungime pe 4 octeți LE), răspunde la
Registration cu RegistrationAck, primește StatusReport/DetectionReport/Alert, trimite Task
(`look_at`) și poate raporta mai departe spre un nod superior (`SapientFusionNode.send_upstream`).
Abaterile de la standard sunt raportate pe nod (`UniversalAdapter.sapient_deviations`), fără
respingere; cu `strict=True` mesajele neconforme sunt respinse.

## 6. Ieșire spre TAK / ATAK

```python
from santinela.cot import CotSender, CotPublisher
pub = CotPublisher(CotSender("udp", "239.2.3.1", 6969), tracker.frame)       # mesh SA
# sau CotSender("tls", "tak.server", 8089, protocol="tak", ssl_context=ctx)  # TAK Server
pub.publish(tracker, now_utc)      # trimite traseele noi/actualizate, șterge pe cele dispărute
```

UID-ul fiecărui traseu e stabil; `stale` = 60 s implicit. Identitatea „ostil” apare doar după
`Tracker.set_identity(track_id, "ostil", author="...", reason="...")`.

## 7. Alerte CAP 1.2 pentru protecția civilă

```python
from santinela.cap import track_alert, cancel_alert, utc
xml = track_alert(traseu, tracker.frame, utc(), "santinela.unitate", "Actual", tracker.now,
                  instruction="Textul autorității pentru populație")
```

Statutul (`Actual`, `Exercise`, `Test`, `System`, `Draft`) e obligatoriu și se alege explicit.
Alerta e `Restricted` implicit; publicarea către populație (`scope="Public"`) e decizia autorității.
Anularea: `cancel_alert(identificator_anterior, ora_anterioara, expeditor, ora, statut)`.

## 8. DIS (exerciții)

Datele DIS (simulatoare de exercițiu, UDP 3000) sunt recunoscute automat, dar intră în imaginea
operațională doar cu `UniversalAdapter(..., dis_exercise=True)`, ca sursă de tip „exercitiu”.

## 9. Date oficiale pentru verificare

```bash
python -m santinela.cli --fetch-sapient date/sapient_proto \
                        --fetch-sapient-vectors date/vectori_dstl \
                        --fetch-asterix-captures date/capturi_asterix \
                        --fetch-reference date/referinta
```

Variabilele de mediu care activează testele suplimentare sunt în
[VERIFICARE.md](VERIFICARE.md#3-testele-care-cer-date-oficiale). Datele descărcate rămân pe
mașina dumneavoastră: nu se redistribuie.

## 10. Convenții

```bash
python -m santinela.cli --conventions
```

Afișează fiecare convenție semantică (unități, nord, datum, codări) cu statutul ei
(verificat / raport / presupus) și sursa. Înainte de punerea în funcțiune, convențiile marcate
„presupus” și cele dependente de echipament se confirmă cu producătorul.
