"""Adaptor universal: recunoaste formatul fiecarui mesaj si il transforma in intrari pentru tracker.

Formate recunoscute automat:

* **ASTERIX** (binar): CAT048 detectii/trasee radar, CAT062 trasee de sistem,
  CAT021 ADS-B; alte categorii (CAT034, CAT240 ...) sunt recunoscute si ignorate;
* **SAPIENT** (BSI Flex 335) in forma JSON a mesajelor protobuf (``detectionReport``,
  ``statusReport``, ``registration``); forma binara cere un decodor protobuf
  furnizat (``sapient_decoder``), forma XML veche e doar semnalata;
* **NMEA 0183** de la radare navale/ARPA: ``TTM`` (traseu polar), ``TLL`` (traseu
  lat/lon), ``HDT``/``THS`` (cap, pentru azimut relativ);
* **JSON de producator**: printr-un profil declarat sau, daca lipseste, prin
  recunoasterea euristica a numelor de campuri uzuale.

Pentru fiecare sursa noua adaptorul noteaza ce ii lipseste (``requirements()``):
o sursa cu pozitii absolute (lat/lon) e acceptata imediat, fara informatie
negativa; una cu pozitii polare (distanta/azimut) asteapta pozitia senzorului.
Aeronavele cooperative (ADS-B, raspunsuri SSR/Mode S) nu intra in trackerul de
drone; sunt folosite pentru inregistrarea automata a radarelor.

Nimic de aici nu inlocuieste validarea pe inregistrari reale de la fiecare sursa.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .asterix import (AsterixError, Cat048Adapter, decode_block, iter_blocks, looks_like_asterix, tod_to_t)
from .geo import LocalFrame
from .measurements import SensorDetection, polar_gauss_moments
from .radar import RadarPlot, RadarSite, RadarTrackReport

NM = 1852.0
KNOT = NM / 3600.0


# ------------------------------------------------------------------ tipuri de iesire
@dataclass
class CooperativeReport:
    """Aeronava cooperativa (ADS-B, CAT021 sau traseu de sistem cu identitate)."""
    icao: str
    t: float
    lat: float
    lon: float
    height_m: float | None = None
    vx: float | None = None
    vy: float | None = None
    callsign: str | None = None
    source: str = "ADS-B"


@dataclass
class SourceStatus:
    key: str
    fmt: str
    count: int = 0
    accepted: int = 0
    site: str | None = None
    needs: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def need(self, msg: str) -> None:
        if msg not in self.needs:
            self.needs.append(msg)

    def note(self, msg: str) -> None:
        if msg not in self.notes:
            self.notes.append(msg)


# ------------------------------------------------------------------ utilitare
def detect_format(data) -> str:
    """'asterix' | 'nmea' | 'sapient-json' | 'sapient-protobuf' | 'sapient-xml' | 'json' | 'necunoscut'."""
    if isinstance(data, (bytes, bytearray)):
        if looks_like_asterix(data):
            return "asterix"
        n = len(data)
        # SAPIENT pe TCP: antet de 4 octeti cu lungimea mesajului protobuf
        if n > 4 and (int.from_bytes(data[:4], "little") == n - 4 or int.from_bytes(data[:4], "big") == n - 4):
            return "sapient-protobuf"
        try:
            text = bytes(data).decode("utf-8")
        except UnicodeDecodeError:
            return "necunoscut"
    elif isinstance(data, str):
        text = data
    elif isinstance(data, (dict, list)):
        return "sapient-json" if _is_sapient(data) else "json"
    else:
        return "necunoscut"
    s = text.lstrip("﻿ \t\r\n")
    if s.startswith(("$", "!")):
        return "nmea"
    if s.startswith("<"):
        return "sapient-xml" if re.search(r"DetectionReport|SensorRegistration|StatusReport", s) else "necunoscut"
    if s.startswith(("{", "[")):
        objs = _json_objects(s)
        if objs is None:
            return "necunoscut"
        return "sapient-json" if any(_is_sapient(o) for o in objs) else "json"
    return "necunoscut"


_SAPIENT_KEYS = {"detectionReport", "detection_report", "statusReport", "status_report", "registration",
                 "registrationAck", "registration_ack", "alert", "task", "taskAck", "task_ack"}


def _is_sapient(o) -> bool:
    if isinstance(o, list):
        return any(_is_sapient(x) for x in o)
    return isinstance(o, dict) and bool(_SAPIENT_KEYS & set(o))


def _json_objects(s: str):
    try:
        v = json.loads(s)
        return v if isinstance(v, list) else [v]
    except json.JSONDecodeError:
        out = []
        for line in s.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                return None
        return out


def _g(d: dict, *names, default=None):
    """Cauta o cheie in forme camelCase / snake_case / cu majuscule."""
    if not isinstance(d, dict):
        return default
    for n in names:
        for k in (n, _snake(n), n[:1].upper() + n[1:], n.lower()):
            if k in d:
                return d[k]
    return default


def _snake(n: str) -> str:
    return re.sub(r"(?<!^)([A-Z])", r"_\1", n).lower()


def _path(d, path: str):
    cur = d
    for part in path.split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


def parse_time_tod(v, fmt: str = "auto") -> float | None:
    """Ora zilei UTC (s) dintr-un timestamp ISO-8601, epoch (s/ms) sau deja ora zilei."""
    if v is None:
        return None
    if isinstance(v, dict):  # protobuf Timestamp in JSON: {"seconds": .., "nanos": ..}
        v = float(v.get("seconds", 0)) + float(v.get("nanos", 0)) * 1e-9
        fmt = "epoch"
    if isinstance(v, str):
        try:
            v = float(v)
        except ValueError:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt = dt.astimezone(timezone.utc)
            return dt.hour * 3600 + dt.minute * 60 + dt.second + dt.microsecond * 1e-6
    v = float(v)
    if fmt == "tod" or (fmt == "auto" and v < 86400 * 2):
        return v % 86400.0
    if fmt == "epoch_ms" or (fmt == "auto" and v > 1e12):
        v /= 1000.0
    return v % 86400.0


def utm_to_latlon(easting: float, northing: float, zone: int, northern: bool = True) -> tuple[float, float]:
    """UTM -> WGS84 (formulele standard, precizie sub 1 m)."""
    a, f, k0 = 6378137.0, 1 / 298.257223563, 0.9996
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    x = easting - 500000.0
    y = northing if northern else northing - 10000000.0
    m = y / k0
    mu = m / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
            + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
            + (151 * e1 ** 3 / 96) * math.sin(6 * mu) + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
    n1 = a / math.sqrt(1 - e2 * math.sin(phi1) ** 2)
    t1 = math.tan(phi1) ** 2
    c1 = ep2 * math.cos(phi1) ** 2
    r1 = a * (1 - e2) / (1 - e2 * math.sin(phi1) ** 2) ** 1.5
    d = x / (n1 * k0)
    lat = phi1 - (n1 * math.tan(phi1) / r1) * (
        d ** 2 / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * ep2) * d ** 4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * ep2 - 3 * c1 ** 2) * d ** 6 / 720)
    lon = (d - (1 + 2 * t1 + c1) * d ** 3 / 6
           + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * ep2 + 24 * t1 ** 2) * d ** 5 / 120) / math.cos(phi1)
    lon0 = math.radians((zone - 1) * 6 - 180 + 3)
    return math.degrees(lat), math.degrees(lon0 + lon)


# ------------------------------------------------------------------ profil JSON
@dataclass
class JsonProfile:
    """Corespondenta campurilor unui flux JSON de producator (cai cu puncte: "pos.lat")."""
    name: str
    match: str | None = None          # cheie care trebuie sa existe ca profilul sa se aplice
    source: str | None = None         # cale catre id-ul senzorului
    source_const: str | None = None   # sau un id fix
    track_id: str | None = None
    time: str | None = None
    time_format: str = "auto"         # auto | iso | epoch | epoch_ms | tod
    lat: str | None = None
    lon: str | None = None
    range: str | None = None
    azimuth: str | None = None
    elevation: str | None = None
    angles_in_radians: bool = False
    range_scale: float = 1.0          # ex. 1000 daca distanta e in km
    speed: str | None = None
    heading: str | None = None
    speed_scale: float = 1.0          # ex. 1/3.6 daca viteza e in km/h
    vx: str | None = None
    vy: str | None = None
    altitude: str | None = None
    drone_class: str | None = None
    sigma_pos: str | None = None
    heuristic: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> "JsonProfile":
        return cls(**d)


_HEUR = {
    "lat": ["lat", "latitude", "Lat", "Latitude"],
    "lon": ["lon", "lng", "long", "longitude", "Lon", "Longitude"],
    "range": ["range", "range_m", "distance", "dist", "slantRange"],
    "azimuth": ["azimuth", "azimuth_deg", "bearing", "az", "Azimuth"],
    "elevation": ["elevation", "elevation_deg", "el"],
    "time": ["timestamp", "time", "ts", "t", "dateTime", "time_utc"],
    "track_id": ["trackId", "track_id", "objectId", "object_id", "targetId", "target_id", "id"],
    "speed": ["speed", "groundSpeed", "ground_speed", "speed_mps"],
    "heading": ["heading", "course", "track", "cog"],
    "vx": ["vx", "velEast", "east_rate", "vel_e"],
    "vy": ["vy", "velNorth", "north_rate", "vel_n"],
    "altitude": ["altitude", "alt", "height", "altitude_m", "hae"],
    "drone_class": ["class", "classification", "type", "category"],
    "source": ["sensor", "sensorId", "sensor_id", "source", "radarId", "radar_id", "node"],
}


def heuristic_profile(obj: dict) -> JsonProfile | None:
    """Profil ghicit dupa numele campurilor; intoarce None daca nu exista pozitie."""
    flat = _flatten(obj)
    found = {}
    for key, names in _HEUR.items():
        for n in names:
            hit = next((p for p in flat if p.split(".")[-1] == n), None)
            if hit is not None:
                found[key] = hit
                break
    if not ({"lat", "lon"} <= set(found) or {"range", "azimuth"} <= set(found)):
        return None
    return JsonProfile(name="euristic", heuristic=True, **found)


def _flatten(o, prefix=""):
    out = []
    if isinstance(o, dict):
        for k, v in o.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict):
                out.extend(_flatten(v, p))
            else:
                out.append(p)
    return out


# ------------------------------------------------------------------ adaptorul
@dataclass
class UniversalAdapter:
    sites: list = field(default_factory=list)          # RadarSite configurate (cu SAC/SIC daca e cazul)
    frame: LocalFrame | None = None
    tod_ref: float = 0.0
    cat048_mode: str = "plots"
    json_profiles: list = field(default_factory=list)  # JsonProfile
    tracker: object = None                              # daca e dat, sursele noi se inregistreaza in el
    sapient_decoder: object = None                      # callable(bytes) -> dict (forma JSON)
    sapient_x_is_lon: bool = True                       # Location.x = longitudine (de confirmat pe ICD)
    drop_non_drone_classes: tuple = ("bird", "pasare", "human", "person", "vehicle", "car")
    min_class_confidence: float = 0.8
    adsb_registration: bool = True

    def __post_init__(self):
        if self.frame is None:
            self.frame = self.tracker.frame if self.tracker is not None else LocalFrame()
        self.stats: Counter = Counter()
        self.errors: list = []
        self.sources: dict[str, SourceStatus] = {}
        self.cooperative: list[CooperativeReport] = []
        self._by_asterix: dict = {}
        self._by_name: dict = {}
        self._cat048 = Cat048Adapter([], tod_ref=self.tod_ref, mode=self.cat048_mode)
        self._adsb = defaultdict(list)    # icao -> [CooperativeReport]
        self._pending_pairs = defaultdict(list)
        for s in list(self.sites):
            self.add_site(s)

    # ---------- configurare ----------
    @classmethod
    def from_config(cls, path_or_dict, **kw) -> "UniversalAdapter":
        cfg = path_or_dict if isinstance(path_or_dict, dict) else json.loads(Path(path_or_dict).read_text("utf-8"))
        sites = []
        for src in cfg.get("sources", []):
            vals = {k: v for k, v in src.items() if v is not None and k != "center"}
            if "lat" not in vals or "lon" not in vals:
                raise ValueError(f"sursa {src.get('sensor_id')}: lat/lon lipsesc din configurare")
            sites.append(RadarSite(**vals))
        profiles = [JsonProfile.from_dict(p) for p in cfg.get("json_profiles", [])]
        return cls(sites=sites, tod_ref=cfg.get("tod_ref", 0.0), cat048_mode=cfg.get("cat048_mode", "plots"),
                   json_profiles=profiles, **kw)

    def add_site(self, site: RadarSite) -> RadarSite:
        if site.center is None:
            site.attach(self.frame)
        if site not in self.sites:
            self.sites.append(site)
        self._by_name[site.sensor_id] = site
        if site.sac is not None and site.sic is not None:
            self._by_asterix[(site.sac, site.sic)] = site
            self._cat048.add_site(site)
        if self.tracker is not None and site.sensor_id not in getattr(self.tracker, "_radar_by_id", {}):
            self.tracker.add_radar(site)
        return site

    def _auto_site(self, name: str, lat: float, lon: float, kind: str, status: SourceStatus,
                   sac=None, sic=None) -> RadarSite:
        """Sursa necunoscuta cu pozitii absolute: o acceptam, fara informatie negativa."""
        site = RadarSite(name, lat, lon, sac=sac, sic=sic, negative_info=False, kind=kind,
                         min_alt=0.0, pd=0.8, sigma_range=100.0)
        self.add_site(site)
        status.site = name
        status.note("acceptata automat (pozitii absolute); fara informatie negativa pana la configurare")
        status.need("acoperirea si altitudinea minima de detectie (pentru informatie negativa)")
        status.need("precizia declarata (sigma pozitie/viteza) si rata de alarme false")
        return site

    def _status(self, key: str, fmt: str) -> SourceStatus:
        if key not in self.sources:
            self.sources[key] = SourceStatus(key, fmt)
        st = self.sources[key]
        st.count += 1
        return st

    def requirements(self) -> list[str]:
        """Ce lipseste pentru fiecare sursa vazuta, in limbaj de operator."""
        out = []
        for key in sorted(self.sources):
            st = self.sources[key]
            for n in st.needs:
                out.append(f"{key} [{st.fmt}]: lipseste {n}")
        return out

    def report(self) -> dict:
        return {
            "formate": dict(self.stats),
            "surse": {k: {"format": s.fmt, "mesaje": s.count, "acceptate": s.accepted, "senzor": s.site,
                          "lipsa": s.needs, "note": s.notes} for k, s in sorted(self.sources.items())},
            "erori": self.errors[-20:],
            "aeronave_cooperative": len(self.cooperative),
        }

    def config_template(self) -> dict:
        """Sablon de configurare pentru sursele care au nevoie de date (de completat de operator)."""
        tpl = []
        for key in sorted(self.sources):
            st = self.sources[key]
            if not st.needs:
                continue
            known = self._by_name.get(st.site) if st.site else None
            entry = {"sensor_id": st.site or key,
                     "lat": round(known.lat, 6) if known else None, "lon": round(known.lon, 6) if known else None,
                     "antenna_height": None, "max_range": None, "min_alt": None,
                     "sigma_range": None, "sigma_az_deg": None}
            m = re.match(r"SAC(\d+)/SIC(\d+)", key)
            if m:
                entry["sac"], entry["sic"] = int(m.group(1)), int(m.group(2))
            tpl.append(entry)
        return {"tod_ref": self.tod_ref, "sources": tpl, "json_profiles": []}

    # ---------- intrare ----------
    def feed(self, data, received_at: float | None = None, source: str | None = None) -> list:
        """Un mesaj (sau un lot) de orice format; intoarce intrarile pentru tracker."""
        fmt = detect_format(data)
        self.stats[fmt] += 1
        try:
            if fmt == "asterix":
                out = self._asterix(bytes(data), received_at)
            elif fmt == "nmea":
                out = self._nmea(data.decode("ascii", "replace") if isinstance(data, (bytes, bytearray)) else data,
                                 received_at, source or "NMEA")
            elif fmt == "sapient-json":
                out = self._sapient(self._objs(data), received_at)
            elif fmt == "sapient-protobuf":
                if self.sapient_decoder is None:
                    st = self._status(source or "SAPIENT-binar", fmt)
                    st.need("decodorul protobuf SAPIENT (definitiile BSI Flex 335 + pachetul protobuf), "
                            "dat ca sapient_decoder")
                    out = []
                else:
                    out = self._sapient([self.sapient_decoder(bytes(data[4:]))], received_at)
            elif fmt == "sapient-xml":
                st = self._status(source or "SAPIENT-XML", fmt)
                st.need("convertor pentru SAPIENT XML (versiunea veche, v6); cereti producatorului BSI Flex 335 v2")
                out = []
            elif fmt == "json":
                out = self._json(self._objs(data), received_at, source)
            else:
                st = self._status(source or "necunoscut", fmt)
                st.need("formatul nu a fost recunoscut: trimiteti un exemplu si documentul de interfata (ICD)")
                out = []
        except (AsterixError, ValueError, KeyError, TypeError) as e:
            self.errors.append((fmt, str(e)))
            out = []
        self._run_registration()
        return out

    def feed_file(self, path: str | Path, received_at: float | None = None, source: str | None = None) -> list:
        raw = Path(path).read_bytes()
        if looks_like_asterix(raw):
            return self.feed(raw, received_at, source)
        text = raw.decode("utf-8", "replace")
        fmt = detect_format(text)
        source = source or Path(path).stem  # NMEA nu are id de senzor: numele fisierului
        if fmt in ("nmea",):
            return self.feed(text, received_at, source)
        out = []
        for line in text.splitlines():
            if line.strip():
                out.extend(self.feed(line, received_at, source))
        return out

    @staticmethod
    def _objs(data):
        if isinstance(data, (dict, list)):
            return data if isinstance(data, list) else [data]
        s = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else data
        return _json_objects(s.strip()) or []

    # ---------- ASTERIX ----------
    def _asterix(self, buf: bytes, received_at) -> list:
        out = []
        for cat, blk in iter_blocks(buf):
            if cat not in (48, 62, 21):
                st = self._status(f"CAT{cat:03d}", "asterix")
                st.note("categorie recunoscuta, nefolosita de tracker (ignorata)")
                continue
            _, recs = decode_block(blk)
            for rec in recs:
                key = f"SAC{rec.get('sac')}/SIC{rec.get('sic')}"
                st = self._status(key, f"asterix CAT{cat:03d}")
                if cat == 48:
                    out.extend(self._cat048_rec(rec, st, received_at))
                elif cat == 62:
                    out.extend(self._cat062_rec(rec, st, received_at, key))
                else:
                    self._cat021_rec(rec, st, key)
        return out

    def _cat048_rec(self, rec, st, received_at) -> list:
        site = self._by_asterix.get((rec.get("sac"), rec.get("sic")))
        if site is None:
            st.need("pozitia radarului (lat, lon, inaltimea antenei), raza si altitudinea minima: "
                    "CAT048 da distanta si azimut fata de radar")
            return []
        st.site = site.sensor_id
        if site.min_alt == 300.0 and site.kind == "radar":
            st.note("altitudinea minima de detectie e valoarea implicita (300 m): confirmati din diagrama")
        n_coop = len(self._cat048.cooperative_plots)
        items = self._cat048.convert([rec], received_at)
        for p in self._cat048.cooperative_plots[n_coop:]:
            if p.icao:
                self._pending_pairs[p.sensor_id].append(p)
        if "doppler_mps" in rec:
            st.note("Doppler prezent: confirmati conventia de semn (pozitiv = departare)")
        st.accepted += len(items)
        return items

    def _cat062_rec(self, rec, st, received_at, key) -> list:
        if rec.get("track_terminated") or rec.get("sim") or "tod" not in rec:
            return []
        site = self._by_asterix.get((rec.get("sac"), rec.get("sic")))
        if "lat" in rec:
            if site is None:
                site = self._auto_site(f"SIS-{rec['sac']}-{rec['sic']}", rec["lat"], rec["lon"], "sistem", st,
                                       rec["sac"], rec["sic"])
            p = self.frame.to_xy(rec["lat"], rec["lon"]) - site.center
        elif "x_m" in rec:
            if site is None:
                st.need("punctul de referinta al sistemului (lat, lon) pentru I062/100 "
                        "(sau activarea I062/105 WGS-84 la sursa)")
                return []
            p = np.array([rec["x_m"], rec["y_m"]])
        else:
            return []
        st.site = site.sensor_id
        t = round(tod_to_t(rec["tod"], self.tod_ref), 4)
        if "icao" in rec or "mode3a" in rec:
            lat, lon = self.frame.to_latlon(*(site.center + p))
            self._add_coop(CooperativeReport(rec.get("icao") or f"M3A-{rec.get('mode3a')}", t, lat, lon,
                                             rec.get("height_m"), rec.get("vx_mps"), rec.get("vy_mps"),
                                             source=key))
            return []
        sx = rec.get("sigma_x_m", 100.0)
        sy = rec.get("sigma_y_m", 100.0)
        has_v = "vx_mps" in rec
        if has_v:
            cov = np.diag([sx ** 2, sy ** 2, rec.get("sigma_vx_mps", 3.0) ** 2, rec.get("sigma_vy_mps", 3.0) ** 2])
        else:
            cov = np.diag([sx ** 2, sy ** 2])
        if "sigma_x_m" not in rec:
            st.note("fara I062/500: precizia traseului e presupusa (100 m, 3 m/s)")
        st.accepted += 1
        return [RadarTrackReport(f"{site.sensor_id}:{rec['tod']:.4f}:{rec.get('track_number')}", site.sensor_id,
                                 rec.get("track_number", 0), t, float(p[0]), float(p[1]),
                                 rec.get("vx_mps"), rec.get("vy_mps"), cov, rec.get("height_m"),
                                 rec.get("track_confirmed", True), received_at=received_at)]

    def _cat021_rec(self, rec, st, key) -> None:
        lat = rec.get("lat_hr", rec.get("lat"))
        lon = rec.get("lon_hr", rec.get("lon"))
        tod = rec.get("tod_071", rec.get("tod_073", rec.get("tod_077")))
        if lat is None or tod is None or "icao" not in rec:
            return
        vx = vy = None
        if "groundspeed_mps" in rec:
            h = math.radians(rec["heading_deg"])
            vx, vy = rec["groundspeed_mps"] * math.sin(h), rec["groundspeed_mps"] * math.cos(h)
        st.site = "ADS-B"
        st.accepted += 1
        self._add_coop(CooperativeReport(rec["icao"], round(tod_to_t(tod, self.tod_ref), 4), lat, lon,
                                         rec.get("height_m"), vx, vy, rec.get("callsign"), source=key))

    def _add_coop(self, c: CooperativeReport) -> None:
        self.cooperative.append(c)
        self._adsb[c.icao].append(c)

    # ---------- inregistrare automata pe aeronave cooperative ----------
    def _ref_state(self, icao: str, t: float):
        hist = self._adsb.get(icao)
        if not hist:
            return None
        best = min(hist, key=lambda c: abs(c.t - t))
        if abs(best.t - t) > 10.0 or best.vx is None:
            return None
        xy = self.frame.to_xy(best.lat, best.lon)
        v = np.array([best.vx, best.vy])
        return np.concatenate([xy + v * (t - best.t), v]), best.height_m

    def _run_registration(self, batch: int = 20) -> None:
        """Perechi (plot radar cu adresa Mode S, pozitie ADS-B a aceleiasi aeronave) -> inregistrare."""
        if not (self.adsb_registration and self.tracker is not None):
            return
        for rid in sorted(self._pending_pairs):
            triples, waiting = [], []
            for p in self._pending_pairs[rid]:
                ref = self._ref_state(p.icao, p.t)
                if ref is None:
                    waiting.append(p)
                    continue
                state, h = ref
                if p.height_m is None and h is not None:
                    p.height_m = h
                triples.append((p, state, np.eye(2) * 15.0 ** 2))
            if len(triples) >= batch:
                self.tracker.register_radar(rid, triples, n_ref=len(triples), source="ADS-B/Mode S")
                self._pending_pairs[rid] = waiting[-500:]
            else:
                self._pending_pairs[rid] = (waiting + [p for p, _, _ in triples])[-500:]

    def flush_registration(self) -> None:
        """La sfarsitul unei inregistrari: foloseste si perechile ramase."""
        self._run_registration(batch=1)

    # ---------- NMEA ----------
    def _nmea(self, text: str, received_at, source: str) -> list:
        out = []
        st = self._status(source, "nmea")
        site = self._by_name.get(source)
        for line in text.splitlines():
            line = line.strip()
            if not line or line[0] not in "$!":
                continue
            body, _, cks = line[1:].partition("*")
            if cks:
                calc = 0
                for ch in body:
                    calc ^= ord(ch)
                if f"{calc:02X}" != cks[:2].upper():
                    self.errors.append(("nmea", f"suma de control gresita: {line}"))
                    continue
            f = body.split(",")
            kind = f[0][-3:]
            if kind in ("HDT", "THS") and site is not None and f[1]:
                site.heading_deg = float(f[1])
            elif kind == "TTM":
                out.extend(self._ttm(f, st, site, received_at, source))
            elif kind == "TLL":
                out.extend(self._tll(f, st, site, received_at, source))
            else:
                st.note(f"propozitia {kind} ignorata")
        return out

    def _nmea_t(self, hhmmss: str, received_at):
        if hhmmss:
            try:
                tod = int(hhmmss[:2]) * 3600 + int(hhmmss[2:4]) * 60 + float(hhmmss[4:])
                return round(tod_to_t(tod, self.tod_ref), 4)
            except ValueError:
                pass
        return received_at

    def _ttm(self, f, st, site, received_at, source) -> list:
        # TTM: 1 nr, 2 distanta, 3 relevment, 4 T/R, 5 viteza, 6 curs, 7 T/R, 8 CPA, 9 TCPA,
        #      10 unitati (N/K/S), 11 nume, 12 stare (L/Q/T), 13 referinta, 14 ora, 15 achizitie
        f = f + [""] * (16 - len(f))
        if site is None:
            st.need("pozitia radarului (lat, lon) si capul, daca relevmentele sunt relative: "
                    "TTM da distanta si relevment fata de radar; dati source=<id> si configurati senzorul")
            return []
        st.site = site.sensor_id
        if f[12] and f[12] != "T":
            return []  # L = pierdut, Q = in achizitie
        unit = (f[10] or "N").upper()
        dscale, vscale = {"N": (NM, KNOT), "K": (1000.0, 1 / 3.6), "S": (1609.344, 0.44704)}.get(unit, (NM, KNOT))
        if not (f[2] and f[3]):
            return []
        dist = float(f[2]) * dscale
        brg = float(f[3]) + (site.heading_deg if f[4].upper() == "R" else 0.0)
        if f[4].upper() == "R":
            st.note("relevmente relative: se foloseste capul din HDT/THS sau heading_deg din configurare")
        az = math.radians(brg)
        z, R = polar_gauss_moments(np.zeros(2), az, dist, math.radians(site.sigma_az_deg), site.sigma_range)
        vx = vy = None
        cov = R
        if f[5] and f[6]:
            spd = float(f[5]) * vscale
            crs = math.radians(float(f[6]) + (site.heading_deg if f[7].upper() == "R" else 0.0))
            vx, vy = spd * math.sin(crs), spd * math.cos(crs)
            cov = np.zeros((4, 4))
            cov[:2, :2] = R
            cov[2:, 2:] = np.eye(2) * 2.0 ** 2
        st.accepted += 1
        t = self._nmea_t(f[14], received_at)
        return [RadarTrackReport(f"{source}:{f[14] or received_at}:{f[1]}", site.sensor_id, int(f[1] or 0), t,
                                 float(z[0]), float(z[1]), vx, vy, cov, None, True, received_at=received_at)]

    def _tll(self, f, st, site, received_at, source) -> list:
        # TLL: 1 nr, 2 lat ddmm.mm, 3 N/S, 4 lon dddmm.mm, 5 E/W, 6 nume, 7 ora, 8 stare, 9 referinta
        f = f + [""] * (10 - len(f))
        if not (f[2] and f[4]) or (f[8] and f[8] != "T"):
            return []
        lat = int(f[2][:2]) + float(f[2][2:]) / 60.0
        lon = int(f[4][:3]) + float(f[4][3:]) / 60.0
        lat = -lat if f[3].upper() == "S" else lat
        lon = -lon if f[5].upper() == "W" else lon
        if site is None:
            site = self._auto_site(source, lat, lon, "radar", st)
        st.site = site.sensor_id
        p = self.frame.to_xy(lat, lon) - site.center
        s = max(site.sigma_range, 50.0)
        st.accepted += 1
        t = self._nmea_t(f[7], received_at)
        return [RadarTrackReport(f"{source}:{f[7] or received_at}:{f[1]}", site.sensor_id, int(f[1] or 0), t,
                                 float(p[0]), float(p[1]), None, None, np.eye(2) * s * s, None, True,
                                 received_at=received_at)]

    # ---------- SAPIENT ----------
    def _sapient(self, objs: list, received_at) -> list:
        out = []
        for o in objs:
            node = str(_g(o, "nodeId", "sourceId", default="SAPIENT"))
            st = self._status(f"SAPIENT:{node}", "sapient")
            tod = parse_time_tod(_g(o, "timestamp"))
            reg = _g(o, "registration")
            if reg is not None:
                defs = _g(reg, "nodeDefinition", default=[]) or []
                types = [str(_g(d, "nodeType", default="")) for d in (defs if isinstance(defs, list) else [defs])]
                st.note(f"inregistrare: {', '.join(t for t in types if t) or 'tip nespecificat'}")
            status = _g(o, "statusReport")
            if status is not None:
                loc = _g(status, "nodeLocation")
                ll = self._sapient_location(loc, st) if loc else None
                if ll and f"SAPIENT:{node}" not in self._by_name and node not in self._by_name:
                    site = RadarSite(node, ll[0], ll[1], negative_info=False, kind="senzor", min_alt=0.0)
                    self.add_site(site)
                    st.site = node
                    st.note("pozitia nodului invatata din statusReport")
                    st.need("acoperirea si altitudinea minima de detectie (pentru informatie negativa)")
            det = _g(o, "detectionReport")
            if det is not None:
                item = self._sapient_detection(node, det, tod, st, received_at)
                if item is not None:
                    st.accepted += 1
                    out.append(item)
        return out

    def _sapient_location(self, loc: dict, st) -> tuple | None:
        cs = str(_g(loc, "coordinateSystem", default="LAT_LNG_DEG_M")).upper()
        x, y = _g(loc, "x"), _g(loc, "y")
        if x is None or y is None:
            return None
        if "UTM" in cs:
            zone = str(_g(loc, "utmZone", default=""))
            m = re.match(r"(\d+)([A-Za-z]?)", zone)
            if not m:
                st.need("zona UTM (utmZone) pentru coordonatele UTM")
                return None
            northern = (m.group(2) or "N").upper() >= "N"
            return utm_to_latlon(float(x), float(y), int(m.group(1)), northern)
        x, y = float(x), float(y)
        if "RAD" in cs:
            x, y = math.degrees(x), math.degrees(y)
        if not self.sapient_x_is_lon:
            x, y = y, x
        st.note("Location: x = longitudine, y = latitudine (de confirmat pe ICD-ul producatorului)")
        return y, x

    def _sapient_detection(self, node, det, tod, st, received_at):
        cls = _g(det, "classification", default=[]) or []
        if isinstance(cls, dict):
            cls = [cls]
        top = max(cls, key=lambda c: float(_g(c, "confidence", default=0) or 0), default=None)
        drone_class = None
        if top is not None:
            ctype = str(_g(top, "type", default="")).lower()
            conf = float(_g(top, "confidence", default=0) or 0)
            if any(k in ctype for k in self.drop_non_drone_classes) and conf >= self.min_class_confidence:
                st.note("detectii clasificate sigur ca non-drona (pasare, persoana, vehicul) sunt ignorate")
                return None
            drone_class = None if ctype in ("", "uav", "drone", "unknown") else ctype
        if tod is None:
            st.need("timestamp in mesaje")
            return None
        t = round(tod_to_t(tod, self.tod_ref), 4)
        obj = _g(det, "objectId")
        rid = str(_g(det, "reportId", default=f"{node}:{tod:.3f}:{obj}"))
        loc = _g(det, "location")
        rb = _g(det, "rangeBearing")
        site = self._by_name.get(node)
        vel = _g(det, "enuVelocity", "velocity")
        if loc is not None:
            ll = self._sapient_location(loc, st)
            if ll is None:
                return None
            if site is None:
                site = self._auto_site(node, ll[0], ll[1], "senzor", st)
            p = self.frame.to_xy(*ll) - site.center
            ex = float(_g(loc, "xError", default=0) or 0)
            ey = float(_g(loc, "yError", default=0) or 0)
            cs = str(_g(loc, "coordinateSystem", default="")).upper()
            if "LAT_LNG" in cs and (ex or ey):  # erori in grade -> metri
                ex, ey = ex * 111320.0 * math.cos(math.radians(ll[0])), ey * 111320.0
                ex, ey = ey, ex  # (x=lon, y=lat) -> (est, nord)
            sx, sy = max(ex, 10.0) if ex else 50.0, max(ey, 10.0) if ey else 50.0
            if not (ex or ey):
                st.note("fara xError/yError: precizia pozitiei e presupusa (50 m)")
            R = np.diag([sx ** 2, sy ** 2])
            height = _g(loc, "z")
        elif rb is not None:
            if site is None:
                st.need("pozitia nodului (statusReport cu nodeLocation sau configurare): "
                        "rangeBearing e relativ la senzor")
                return None
            cs = str(_g(rb, "coordinateSystem", default="DEGREES_M")).upper()
            conv = (lambda a: a) if "RAD" in cs else math.radians
            az = conv(float(_g(rb, "azimuth")))
            if "MAGNETIC" in str(_g(rb, "datum", default="")).upper():
                st.need("declinatia magnetica a nodului (azimut magnetic)")
            el = conv(float(_g(rb, "elevation", default=0) or 0))
            rng = float(_g(rb, "range"))
            ground = rng * math.cos(el)
            s_az = conv(float(_g(rb, "azimuthError", default=0) or 0)) or math.radians(site.sigma_az_deg)
            s_r = float(_g(rb, "rangeError", default=0) or 0) or site.sigma_range
            z, R = polar_gauss_moments(np.zeros(2), az, ground, s_az, s_r)
            p = z
            height = (site.antenna_height + rng * math.sin(el)) if el else None
        else:
            st.need("location sau rangeBearing in detectionReport")
            return None
        vx = vy = None
        if vel is not None:
            vx, vy = _g(vel, "eastRate"), _g(vel, "northRate")
        if obj is not None:
            cov = R
            if vx is not None and vy is not None:
                cov = np.zeros((4, 4))
                cov[:2, :2] = R
                cov[2:, 2:] = np.eye(2) * max(float(_g(vel, "eastRateError", default=2.0) or 2.0), 0.5) ** 2
                vx, vy = float(vx), float(vy)
            else:
                vx = vy = None
            return RadarTrackReport(rid, site.sensor_id, _stable_int(obj), t, float(p[0]), float(p[1]),
                                    vx, vy, cov, float(height) if height is not None else None, True,
                                    drone_class=drone_class, received_at=received_at)
        lat, lon = self.frame.to_latlon(*(site.center + p))
        return SensorDetection(rid, site.sensor_id, t, lat, lon, R, drone_class=drone_class, pd=site.pd,
                               lambda_fa=site.lambda_fa, received_at=received_at,
                               altitude_m=float(height) if height is not None else None)

    # ---------- JSON de producator ----------
    def _json(self, objs: list, received_at, source) -> list:
        out = []
        for o in objs:
            if not isinstance(o, dict):
                continue
            prof = next((p for p in self.json_profiles if p.match is None or _path(o, p.match) is not None), None)
            if prof is None:
                prof = heuristic_profile(o)
            src = None
            if prof is not None:
                src = prof.source_const or (str(_path(o, prof.source)) if prof.source and _path(o, prof.source)
                                            is not None else None)
            src = src or source or "JSON"
            st = self._status(src, "json")
            if prof is None:
                st.need("profil JSON (ce camp e pozitia, timpul, id-ul traseului): nu am gasit lat/lon "
                        "sau distanta/azimut")
                continue
            if prof.heuristic:
                st.note("campuri recunoscute euristic: " + ", ".join(
                    f"{k}={v}" for k, v in prof.__dict__.items() if isinstance(v, str) and k not in ("name",)))
                st.need("confirmarea unitatilor (m, grade, m/s) si a profilului ghicit -> json_profiles")
            item = self._json_item(o, prof, src, st, received_at)
            if item is not None:
                st.accepted += 1
                out.append(item)
        return out

    def _json_item(self, o, prof: JsonProfile, src, st, received_at):
        def num(path, scale=1.0):
            v = _path(o, path) if path else None
            try:
                return None if v is None else float(v) * scale
            except (TypeError, ValueError):
                return None

        tod = parse_time_tod(_path(o, prof.time), prof.time_format) if prof.time else None
        if tod is None and received_at is None:
            st.need("campul de timp (time) in profilul JSON")
            return None
        t = round(tod_to_t(tod, self.tod_ref), 4) if tod is not None else received_at
        site = self._by_name.get(src)
        lat, lon = num(prof.lat), num(prof.lon)
        ang = (lambda a: a) if prof.angles_in_radians else math.radians
        if lat is not None and lon is not None:
            if site is None:
                site = self._auto_site(src, lat, lon, "senzor", st)
            p = self.frame.to_xy(lat, lon) - site.center
            s = num(prof.sigma_pos) or 50.0
            R = np.eye(2) * s * s
        else:
            rng, az = num(prof.range, prof.range_scale), num(prof.azimuth)
            if rng is None or az is None:
                return None
            if site is None:
                st.need("pozitia senzorului (lat, lon): fluxul da distanta/azimut")
                return None
            el = num(prof.elevation)
            ground = rng * math.cos(ang(el)) if el else rng
            p, R = polar_gauss_moments(np.zeros(2), ang(az), ground, math.radians(site.sigma_az_deg),
                                       site.sigma_range)
        vx, vy = num(prof.vx, prof.speed_scale), num(prof.vy, prof.speed_scale)
        spd, hdg = num(prof.speed, prof.speed_scale), num(prof.heading)
        if vx is None and spd is not None and hdg is not None:
            h = ang(hdg)
            vx, vy = spd * math.sin(h), spd * math.cos(h)
        alt = num(prof.altitude)
        cls = _path(o, prof.drone_class) if prof.drone_class else None
        cls = str(cls) if isinstance(cls, (str, int)) else None
        tid = _path(o, prof.track_id) if prof.track_id else None
        rid = f"{src}:{tod if tod is not None else received_at}:{tid}"
        if tid is not None:
            cov = R
            if vx is not None and vy is not None:
                cov = np.zeros((4, 4))
                cov[:2, :2] = R
                cov[2:, 2:] = np.eye(2) * 3.0 ** 2
            else:
                vx = vy = None
            return RadarTrackReport(rid, site.sensor_id, _stable_int(tid), t, float(p[0]), float(p[1]), vx, vy,
                                    cov, alt, True, drone_class=cls, received_at=received_at)
        la, lo = self.frame.to_latlon(*(site.center + p))
        return SensorDetection(rid, site.sensor_id, t, la, lo, R, drone_class=cls, pd=site.pd,
                               lambda_fa=site.lambda_fa, received_at=received_at, altitude_m=alt)


def _stable_int(v) -> int:
    """Numar stabil de traseu din orice identificator (determinist, fara hash() aleator)."""
    if isinstance(v, int):
        return v
    s = str(v)
    if s.isdigit():
        return int(s)
    h = 0
    for ch in s:
        h = (h * 131 + ord(ch)) % 2_147_483_647
    return h
