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

from . import adsb as adsbmod
from . import misb as misbmod
from . import cot as cotmod
from . import nffi as nffimod
from . import remoteid as ridmod
from . import stanag4607
from .asterix import (AsterixError, Cat048Adapter, decode_block, iter_blocks, looks_like_asterix, tod_to_t)
from .cot import looks_like_cot
from .nffi import FriendlyPosition, looks_like_nffi
from .protowire import ProtoError, ProtoSchema
from .remoteid import looks_like_remoteid
from .stanag4607 import Stanag4607Error, looks_like_4607
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
    operator_lat: float | None = None   # Remote ID: pozitia operatorului (mesajul System)
    operator_lon: float | None = None
    operator_id: str | None = None
    self_id: str | None = None


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
# Standarde cu distributie controlata: recunoscute prin declararea sursei, decodate
# doar printr-un modul suplimentar scris in mediul acreditat dupa specificatia oficiala.
CONTROLLED_STANDARDS = {
    "link16": "Link 16 (STANAG 5516 / MIL-STD-6016, mesaje J)",
    "jreap": "JREAP-A/B/C (STANAG 5518 / MIL-STD-3011)",
    "link11": "Link 11 (STANAG 5511)",
    "link22": "Link 22 (STANAG 5522)",
    "vmf": "VMF (MIL-STD-6017)",
    "othgold": "OTH-Gold",
    "adatp3": "ADatP-3 (STANAG 5500)",
}


def detect_format(data) -> str:
    """Formatul unui mesaj.

    'asterix' | 'stanag4607' | 'remoteid' | 'nmea' | 'cot' | 'nffi' | 'sapient-json' |
    'sapient-protobuf' | 'sapient-xml' | 'json' | 'necunoscut'.
    """
    if isinstance(data, (bytes, bytearray)):
        if looks_like_asterix(data):
            return "asterix"
        if looks_like_4607(data):
            return "stanag4607"
        if adsbmod.looks_like_beast(data):
            return "beast"
        if misbmod.looks_like_klv(data):
            return "klv"
        if misbmod.looks_like_mpegts(data):
            return "mpegts"
        # SAPIENT pe TCP: fiecare mesaj protobuf precedat de 4 octeti cu lungimea lui
        if _length_framed(bytes(data), "little") or _length_framed(bytes(data), "big"):
            return "sapient-protobuf"
        if looks_like_remoteid(data):
            return "remoteid"
        if ridmod.looks_like_radio_frame(data):
            return "remoteid-radio"
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
    if s.startswith("MSG,"):
        return "sbs"
    if nffimod.variant(s) == "ffi-mtf":
        return "ffi-mtf"
    if re.match(r"^[*@][0-9A-Fa-f]{14,40};", s):
        return "avr"
    if s.startswith("<"):
        if looks_like_cot(s):
            return "cot"
        if looks_like_nffi(s):
            return "nffi"
        return "sapient-xml" if re.search(r"DetectionReport|SensorRegistration|StatusReport", s) else "necunoscut"
    if s.startswith(("{", "[")):
        objs = _json_objects(s)
        if objs is None:
            return "necunoscut"
        if any(_is_sapient(o) for o in objs):
            return "sapient-json"
        if any(isinstance(o, dict) and isinstance(o.get("aircraft"), list) for o in objs):
            return "readsb"
        return "json"
    return "necunoscut"


def _length_framed(data: bytes, order: str) -> bool:
    """Bufferul e un lant exact de cadre [lungime pe 4 octeti][mesaj]?"""
    i, n = 0, len(data)
    if n <= 4:
        return False
    while i < n:
        if i + 4 > n:
            return False
        ln = int.from_bytes(data[i:i + 4], order)
        if ln == 0 or i + 4 + ln > n:
            return False
        i += 4 + ln
    return True


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


def _snake_keys(o):
    """Forma JSON protobuf (camelCase) -> numele din .proto (snake_case), recursiv."""
    if isinstance(o, dict):
        return {(_snake(k) if isinstance(k, str) else k): _snake_keys(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_snake_keys(v) for v in o]
    return o


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
    sapient_proto_dir: str | None = None                # definitiile oficiale .proto (BSI Flex 335 v2)
    sapient_root_message: str = "SapientMessage"
    sapient_verify_manifest: bool = True                # verifica amprentele fata de manifestul fixat
    drop_non_drone_classes: tuple = ("bird", "pasare", "human", "person", "vehicle", "car")
    min_class_confidence: float = 0.8
    adsb_registration: bool = True
    adsb_receiver: tuple | None = None                  # (lat, lon) receptor 1090, pentru CPR local
    opportunity_gate_m: float = 1500.0                  # plot radar langa o aeronava ADS-B -> cooperativ
    gmti_max_speed: float = 235.0                       # m/s; wrap mai mic decat dublul ei -> Doppler ambiguu
    opportunity_min_alt_m: float = 300.0                # doar aeronave peste aceasta inaltime

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
        self.friendly: dict[str, FriendlyPosition] = {}   # pozitii de echipe (NFFI, CoT prieten)
        self._plugins: dict[str, dict] = {}             # nume -> {"decode": f, "detect": f}
        self._declared: dict[str, str] = {}             # sursa -> format declarat
        self._last_full_tod: dict = {}                  # (sac, sic) -> ultima ora completa (CAT002/034)
        self.asterix_editions: dict = {}                # (sac, sic) -> {categorie: "x.y"} din CAT247
        ref = self.adsb_receiver if self.adsb_receiver else (self.frame.lat0, self.frame.lon0)
        self._modes = adsbmod.ModeSDecoder(ref_latlon=ref)
        self.sapient_nodes: dict = {}                       # profil pe nod (din Registration)
        self.sapient_deviations = defaultdict(list)         # abateri de la standard, pe nod
        self.sapient_alerts: list = []
        self.sapient_task_acks: list = []
        self._sapient_schema = None
        if self.sapient_proto_dir:
            from .sapient_proto import load_schema
            schema = load_schema(self.sapient_proto_dir, strict=self.sapient_verify_manifest)
            self._sapient_schema = schema
            root = self.sapient_root_message
            if self.sapient_decoder is None:
                self.sapient_decoder = lambda b: schema.decode(root, b)
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

    def register_decoder(self, name: str, decode, detect=None) -> None:
        """Modul suplimentar pentru un format (ex. Link 16 in mediul acreditat).

        ``decode(data, received_at, source)`` intoarce intrari pentru tracker (RadarTrackReport,
        SensorDetection, RadarPlot); ``detect(data) -> bool`` optional, pentru recunoastere automata.
        """
        self._plugins[name] = {"decode": decode, "detect": detect}

    def declare_source(self, source: str, fmt: str) -> None:
        """Formatul unei surse, cand nu se poate recunoaste din continut (ex. legaturi de date tactice)."""
        self._declared[source] = fmt

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
        fmt = self._declared.get(source) if source else None
        if fmt is None:
            fmt = next((n for n, p in self._plugins.items() if p["detect"] and p["detect"](data)), None)
        if fmt is None:
            fmt = detect_format(data)
        self.stats[fmt] += 1
        try:
            if fmt in self._plugins:
                st = self._status(source or fmt, fmt)
                out = list(self._plugins[fmt]["decode"](data, received_at, source))
                for item in out:
                    self._ensure_site_for(item, st)
                st.accepted += len(out)
            elif fmt in CONTROLLED_STANDARDS:
                st = self._status(source or fmt, fmt)
                st.need(f"decodor pentru {CONTROLLED_STANDARDS[fmt]}: specificatia are distributie controlata; "
                        "se implementeaza in mediul acreditat si se inregistreaza cu register_decoder()")
                out = []
            elif fmt == "stanag4607":
                out = self._gmti(bytes(data), received_at)
            elif fmt in ("klv", "mpegts"):
                packets = ([p for _, p in misbmod.iter_klv(bytes(data))] if fmt == "klv"
                           else misbmod.klv_from_mpegts(bytes(data)))
                out = []
                for pkt in packets:
                    out += self._klv(pkt, received_at, source)
            elif fmt in ("beast", "avr", "sbs", "readsb"):
                out = self._adsb_raw(fmt, data, received_at, source or f"ADS-B-{fmt}")
            elif fmt == "remoteid":
                out = self._remoteid(bytes(data), received_at, source or "RemoteID")
            elif fmt == "remoteid-radio":
                out = []
                for carrier, payload in ridmod.extract(bytes(data)):
                    out += self._remoteid(payload, received_at, source or f"RemoteID-{carrier}")
            elif fmt == "cot":
                out = self._cot(data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else data,
                                received_at, source or "CoT")
            elif fmt == "ffi-mtf":
                st = self._status(source or "FFI-MTF", fmt)
                st.need("FFI MTF (APP-11, ADatP-36 / FMN Spiral 5): specificatia are distributie controlata; "
                        "cereti gateway-ului FFT iesire NFFI XML sau CoT, ori un decodor din mediul acreditat")
                out = []
            elif fmt == "nffi":
                self._nffi(data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else data,
                           source or "NFFI")
                out = []
            elif fmt == "asterix":
                out = self._asterix(bytes(data), received_at)
            elif fmt == "nmea":
                out = self._nmea(data.decode("ascii", "replace") if isinstance(data, (bytes, bytearray)) else data,
                                 received_at, source or "NMEA")
            elif fmt == "sapient-json":
                out = self._sapient(self._objs(data), received_at)
            elif fmt == "sapient-protobuf":
                if self.sapient_decoder is None:
                    st = self._status(source or "SAPIENT-binar", fmt)
                    st.need("definitiile oficiale SAPIENT .proto (BSI Flex 335 v2): sapient_proto_dir=<director>")
                    out = []
                else:
                    out = self._sapient(self._sapient_frames(bytes(data)), received_at)
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
        except (AsterixError, Stanag4607Error, ProtoError, misbmod.KlvError, ValueError, KeyError, TypeError) as e:
            self.errors.append((fmt, str(e)))
            out = []
        self._run_registration()
        return out

    def feed_file(self, path: str | Path, received_at: float | None = None, source: str | None = None) -> list:
        raw = Path(path).read_bytes()
        if detect_format(raw) in ("asterix", "stanag4607", "remoteid", "sapient-protobuf"):
            return self.feed(raw, received_at, source)
        text = raw.decode("utf-8", "replace")
        fmt = detect_format(text)
        source = source or Path(path).stem  # NMEA nu are id de senzor: numele fisierului
        if fmt in ("nmea", "cot", "nffi", "sapient-xml"):
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
        blocks = list(iter_blocks(buf))
        for cat, blk in blocks:          # intai versiunile (CAT247), apoi datele
            if cat == 247:
                for rec in decode_block(blk)[1]:
                    sid = (rec.get("sac"), rec.get("sic"))
                    self.asterix_editions.setdefault(sid, {}).update(
                        {c: v for c, v in rec.get("versions", {}).items()})
                    st = self._status(f"SAC{sid[0]}/SIC{sid[1]}", "asterix CAT247")
                    st.note("editii declarate prin CAT247: " + ", ".join(
                        f"CAT{c:03d} {v}" for c, v in sorted(rec.get("versions", {}).items())))
        for cat, blk in blocks:
            if cat == 247:
                continue
            if cat in (1, 2, 34, 48, 62, 21, 15, 205):
                blk = self._edition_filter(cat, blk)
                if blk is None:
                    continue
            if cat in (1, 2, 34):
                out.extend(self._legacy_asterix(cat, blk, received_at))
                continue
            if cat in (15, 205):
                out.extend(self._spec_asterix(cat, blk, received_at))
                continue
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

    def _edition_filter(self, cat: int, blk: bytes):
        """Verifica editia sursei (din CAT247 sau configurare) fata de tabelele decodorului."""
        from .asterix import REFERENCE_EDITION, TABLE_MAJOR
        recs = decode_block(blk)[1] if cat != 1 else None
        sid = None
        if recs:
            sid = (recs[0].get("sac"), recs[0].get("sic"))
        elif len(blk) > 6:
            sid = (blk[4], blk[5]) if blk[3] & 0x80 else None
        if sid is None:
            return blk
        key = f"SAC{sid[0]}/SIC{sid[1]}"
        site = self._by_asterix.get(sid)
        eds = dict(site.asterix_editions or {}) if site is not None and site.asterix_editions else {}
        eds.update(self.asterix_editions.get(sid, {}))
        ed = eds.get(cat)
        st = self._status(key, f"asterix CAT{cat:03d}")
        st.count -= 1  # doar verificare, nu un mesaj nou
        if ed is None:
            st.note(f"editia CAT{cat:03d} necunoscuta: declarati-o (asterix_editions) sau activati CAT247 la sursa")
            return blk
        try:
            major = int(str(ed).split(".")[0])
        except ValueError:
            major = None
        if major != TABLE_MAJOR.get(cat):
            st.need(f"editia CAT{cat:03d} {ed} nu e suportata (tabelele sunt pentru editia "
                    f"{TABLE_MAJOR.get(cat)}.x); datele acestei surse sunt oprite")
            return None
        ref = REFERENCE_EDITION.get(cat)
        if ref and str(ed) != ref:
            st.note(f"CAT{cat:03d} editia {ed} (referinta {ref}): verificati diferentele de UAP")
        return blk

    def _spec_asterix(self, cat: int, blk: bytes, received_at) -> list:
        """CAT015 (INCS: supraveghere necooperativa, ex. radar pasiv/multistatic) si CAT205
        (goniometre radio), decodate generic dupa asterix-specs."""
        out = []
        for rec in decode_block(blk, full=True)[1]:
            f = rec["fields"]
            sid = (f.get("010", {}).get("SAC"), f.get("010", {}).get("SIC"))
            key = f"SAC{sid[0]}/SIC{sid[1]}"
            st = self._status(key, f"asterix CAT{cat:03d}")
            item = self._cat015(f, sid, key, st, received_at) if cat == 15 else \
                self._cat205(f, sid, key, st, received_at)
            if item is not None:
                st.accepted += 1
                out.append(item)
        return out

    def _cat015(self, f, sid, key, st, received_at):
        mt = (f.get("000") or {}).get("MT")
        if mt == 5:
            return None  # sfarsit de traseu
        desc = f.get("020") or {}
        if desc.get("TTAX") in (1, 2, 3):
            st.note("tinte de referinta/sintetice/simulate (I015/020 TTAX) ignorate")
            return None
        tod = f.get("145")
        if tod is None:
            st.need("I015/145 (ora de aplicabilitate)")
            return None
        t = round(tod_to_t(tod, self.tod_ref), 4)
        site = self._by_asterix.get(sid)
        hp = f.get("600") or {}
        height = (f.get("601") or {}).get("GH")
        if "P84" in hp:
            lat, lon = hp["P84"]["LATITUDE"], hp["P84"]["LONGITUDE"]
            if site is None:
                site = self._auto_site(f"INCS-{sid[0]}-{sid[1]}", lat, lon, "senzor", st, sid[0], sid[1])
            p = self.frame.to_xy(lat, lon) - site.center
            if "HPP" in hp:
                sx, sy, rho = hp["HPP"]["SDHPX"], hp["HPP"]["SDHPY"], hp["HPP"]["COSDHPXY"]
            elif "HPR" in hp:
                sx, sy, rho = hp["HPR"]["RSHPX"] / 2.0, hp["HPR"]["RSHPY"] / 2.0, hp["HPR"]["CORSHPXY"]
                st.note("precizia din I015/600 HPR (marja); folosita ca 2 sigma")
            else:
                sx = sy = 100.0
                rho = 0.0
                st.note("fara I015/600 HPP: precizia pozitiei presupusa (100 m)")
            sx, sy = max(sx, 5.0), max(sy, 5.0)
            R = np.array([[sx * sx, rho * sx * sy], [rho * sx * sy, sy * sy]])
        else:
            if desc.get("MOMU") == 1:
                st.need("I015/600 P84: senzor multistatic fara pozitie WGS-84 (distanta/azimut nu sunt monostatice)")
                return None
            rng = (f.get("625") or {}).get("R")
            az = (f.get("627") or {}).get("AZ")
            if rng is None or az is None:
                st.need("pozitia tintei: I015/600 P84 sau I015/625 R + I015/627 AZ")
                return None
            if site is None:
                st.need("pozitia senzorului INCS (configurare cu SAC/SIC): raportul e polar")
                return None
            el = math.radians((f.get("628") or {}).get("EL") or 0.0)
            s_r = (f.get("625") or {}).get("SDR") or site.sigma_range
            s_az = math.radians((f.get("627") or {}).get("SDASZ") or site.sigma_az_deg)
            p, R = polar_gauss_moments(np.zeros(2), math.radians(az), rng * math.cos(el), s_az, s_r)
            if height is None and (f.get("628") or {}).get("EL") is not None:
                height = site.antenna_height + rng * math.sin(el)
        if "626" in f:
            st.note("I015/626 Doppler (poate fi bistatic): neutilizat in filtru")
        hv = (f.get("602") or {}).get("HV")
        trn = f.get("161")
        status = f.get("170") or {}
        if trn is not None and mt in (2, 4):
            vx = vy = None
            cov = R
            if hv is not None:
                vx, vy = hv["X"], hv["Y"]
                sd = (f.get("602") or {}).get("SDHV") or {"X": 3.0, "Y": 3.0}
                cov = np.zeros((4, 4))
                cov[:2, :2] = R
                cov[2:, 2:] = np.diag([max(sd["X"], 0.5) ** 2, max(sd["Y"], 0.5) ** 2])
            return RadarTrackReport(f"{key}:{tod:.3f}:{trn}", site.sensor_id, int(trn), t, float(p[0]), float(p[1]),
                                    vx, vy, cov, height, status.get("CNF", 0) == 0, received_at=received_at)
        lat, lon = self.frame.to_latlon(*(site.center + p))
        return SensorDetection(f"{key}:{tod:.3f}:{trn}", site.sensor_id, t, lat, lon, R, pd=site.pd,
                               lambda_fa=site.lambda_fa, received_at=received_at, altitude_m=height)

    def _cat205(self, f, sid, key, st, received_at):
        mt = f.get("000")
        tod = f.get("030")
        if tod is None:
            return None
        if mt == 2 or ("050" not in f and "070" in f):
            st.note("relevmente goniometrice fara pozitie (I205/070/080): neutilizate, cer triangulare")
            return None
        if mt == 4 or "050" not in f:
            return None
        lat, lon = f["050"]["LAT"], f["050"]["LON"]
        site = self._by_asterix.get(sid)
        if site is None:
            site = self._auto_site(f"RDF-{sid[0]}-{sid[1]}", lat, lon, "senzor", st, sid[0], sid[1])
        sig = max(float(f.get("110") or 0.0), 50.0) if f.get("110") else 500.0
        if not f.get("110"):
            st.note("fara I205/110: incertitudinea pozitiei emitatorului presupusa (500 m)")
        if "090" in f:
            st.note("goniometru pe canale radio (I205/090): emitatorul poate fi drona sau operatorul ei")
        t = round(tod_to_t(tod, self.tod_ref), 4)
        trn = f.get("160")
        if trn is not None:
            return RadarTrackReport(f"{key}:{tod:.3f}:{trn}", site.sensor_id, int(trn), t,
                                    *(self.frame.to_xy(lat, lon) - site.center).tolist(), None, None,
                                    np.eye(2) * sig * sig, None, True, received_at=received_at)
        return SensorDetection(f"{key}:{tod:.3f}:{f.get('040')}", site.sensor_id, t, lat, lon,
                               np.eye(2) * sig * sig, pd=site.pd, lambda_fa=site.lambda_fa, received_at=received_at)

    def _legacy_asterix(self, cat: int, blk: bytes, received_at) -> list:
        """CAT002/034 (serviciu: ora, pozitia radarului) si CAT001 (plot-uri/trasee vechi)."""
        out = []
        _, recs = decode_block(blk)
        for rec in recs:
            key = f"SAC{rec.get('sac')}/SIC{rec.get('sic')}"
            st = self._status(key, f"asterix CAT{cat:03d}")
            sid = (rec.get("sac"), rec.get("sic"))
            if cat in (2, 34):
                if "tod" in rec:
                    self._last_full_tod[sid] = rec["tod"]
                if "site_lat" in rec and sid not in self._by_asterix:
                    site = RadarSite(f"R-{sid[0]}-{sid[1]}", rec["site_lat"], rec["site_lon"],
                                     antenna_height=max(float(rec["site_height_m"]), 0.0), sac=sid[0], sic=sid[1],
                                     negative_info=False)
                    self.add_site(site)
                    st.site = site.sensor_id
                    st.note("pozitia radarului invatata din I034/120")
                    st.need("acoperirea si altitudinea minima de detectie (pentru informatie negativa)")
                continue
            # CAT001: ora trunchiata (I001/141, perioada 512 s) se completeaza cu ultima ora
            # completa din CAT002/034 sau cu ora de sosire
            if "tod_trunc" not in rec:
                continue
            base = self._last_full_tod.get(sid)
            if base is None:
                if received_at is None:
                    st.need("ora completa: mesaje CAT002/CAT034 de la acelasi radar sau ora de sosire")
                    continue
                base = (self.tod_ref + received_at) % 86400.0
                st.note("ora CAT001 completata din ora de sosire (lipsesc CAT002/034)")
            k = round((base - rec["tod_trunc"]) / 512.0)
            rec["tod"] = (rec["tod_trunc"] + 512.0 * k) % 86400.0
            if rec.get("is_track"):
                rec.setdefault("track_number", 0)
            out.extend(self._cat048_rec(rec, st, received_at))
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
        kept = []
        for it in items:
            icao = self._opportunity(it, site) if isinstance(it, RadarPlot) else None
            if icao is None:
                kept.append(it)
                continue
            it.icao, it.cooperative = icao, True   # tinta de oportunitate: avion ADS-B
            self._pending_pairs[it.sensor_id].append(it)
            st.note("plot-uri primare potrivite cu aeronave ADS-B: excluse din trackerul de drone, "
                    "folosite la inregistrarea radarului")
        items = kept
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

    def _ensure_site_for(self, item, st) -> None:
        """Sursele din module suplimentare cu pozitii absolute primesc automat un senzor."""
        sid = getattr(item, "sensor_id", None)
        if sid and sid not in self._by_name:
            lat = getattr(item, "lat", None)
            lon = getattr(item, "lon", None)
            if lat is None:
                st.need(f"pozitia senzorului {sid} (configurare)")
                return
            self._auto_site(sid, lat, lon, "senzor", st)

    def _sapient_frames(self, data: bytes) -> list:
        """Mesaje SAPIENT binare, fiecare precedat de 4 octeti de lungime (little-endian implicit)."""
        order = "little" if _length_framed(data, "little") else "big"
        out, i = [], 0
        while i + 4 <= len(data):
            n = int.from_bytes(data[i:i + 4], order)
            out.append(self.sapient_decoder(data[i + 4:i + 4 + n]))
            i += 4 + n
        return out

    # ---------- STANAG 4607 (GMTI) ----------
    def _gmti(self, buf: bytes, received_at) -> list:
        out = []
        for dw in stanag4607.decode(buf):
            key = f"4607:{dw.platform_id or 'necunoscut'}"
            st = self._status(key, "stanag4607")
            st.site = key
            st.note("STANAG 4607: confirmati semnul vitezei pe linia de vizare (pozitiv = departare)")
            if dw.exercise:
                st.note("pachete marcate EXERCITIU")
            if dw.data_kind != "real":
                st.note(f"dwell-uri cu date {dw.data_kind} (antet P7) ignorate")
                continue
            if dw.geoid_model not in (None, "nespecificat"):
                st.note(f"inaltimile D32.6 sunt fata de {dw.geoid_model} (segmentul job): neconvertite la elipsoid")
            f = dw.fields
            if key not in self._by_name:
                self._auto_site(key, f["sensor_lat"], f["sensor_lon"], "radar", st)
            site = self._by_name[key]
            t = round(tod_to_t(dw.tod, self.tod_ref), 4)
            s_xy = self.frame.to_xy(f["sensor_lat"], f["sensor_lon"])
            for k, tg in enumerate(dw.targets):
                if "lat" not in tg:
                    continue
                p = self.frame.to_xy(tg["lat"], tg["lon"])
                los = p - s_xy
                rng = float(np.linalg.norm(los)) or 1.0
                u = los / rng
                s_r = tg.get("slant_range_unc_cm", 0) / 100.0 or site.sigma_range
                s_c = tg.get("cross_range_unc_dm", 0) / 10.0 or rng * math.radians(site.sigma_az_deg)
                w = np.array([-u[1], u[0]])
                R = np.outer(u, u) * s_r ** 2 + np.outer(w, w) * s_c ** 2
                rr = tg.get("los_velocity_cms")
                wrap = tg.get("wrap_velocity_cms")
                if rr is not None and wrap and wrap / 100.0 < 2 * self.gmti_max_speed:
                    # viteza poate fi ambigua cu +/- wrap: nu o folosim in filtru, doar pozitia
                    st.note("viteza pe linia de vizare ambigua (wrap velocity mica): neutilizata in filtru")
                    rr = None
                cls = tg.get("classification")
                if cls is not None:
                    label, simulated = stanag4607.classification(int(cls))
                    if simulated:
                        st.note("tinte marcate SIMULATE in clasificarea D32.10")
                    if int(cls) % 128 in (3, 4):
                        st.note("clasificare 'aeronava' (3/4): indiciu slab, STANAG 4607 nu are clasa UAS")
                out.append(SensorDetection(
                    f"{key}:{dw.fields.get('revisit_index', 0):.0f}:{dw.fields.get('dwell_index', 0):.0f}:{k}",
                    key, t, tg["lat"], tg["lon"], R,
                    drone_class=None,  # clasa 4607 nu e o clasa de drona (fara UAS in tabel)
                    pd=site.pd, lambda_fa=site.lambda_fa, received_at=received_at,
                    altitude_m=tg.get("height_m"), rr=None if rr is None else rr / 100.0,
                    rr_sigma=(tg.get("los_velocity_unc_cms", 0) / 100.0) or 1.0,
                    sensor_lat=f["sensor_lat"], sensor_lon=f["sensor_lon"]))
                st.accepted += 1
        return out

    # ---------- MISB KLV (EO/IR, STANAG 4609) ----------
    def _klv(self, pkt: bytes, received_at, source) -> list:
        out = []
        if pkt[:16] == misbmod.UL_0903:
            uas, vmti = None, misbmod.decode_0903(pkt)
        else:
            uas = misbmod.decode_0601(pkt)
            vmti = uas.vmti
        f = uas.fields if uas else {}
        name = source or f.get("PlatformTailNumber") or f.get("PlatformDesignation") or f.get("MissionID") or "EOIR"
        st = self._status(f"EOIR:{name}", "klv")
        if uas is not None and uas.checksum_ok is False:
            self.errors.append(("klv", f"{name}: suma de control ST 0601 gresita; pachet ignorat"))
            return []
        t_epoch = (uas.time if uas else None) or (vmti.time if vmti else None)
        if t_epoch is None:
            st.need("PrecisionTimeStamp (ST 0601 tag 2 / ST 0903 tag 2)")
            return []
        t = round(tod_to_t(parse_time_tod(t_epoch, "epoch"), self.tod_ref), 4)
        slat, slon = f.get("SensorLatitude"), f.get("SensorLongitude")
        key = f"EOIR-{name}"
        if key not in self._by_name:
            if slat is None and not (vmti and any(v.location for v in vmti.targets)):
                st.need("pozitia senzorului (ST 0601 tag 13/14) sau tinte VMTI cu locatie")
                return []
            ref = (slat, slon) if slat is not None else (vmti.targets[0].location["lat"], vmti.targets[0].location["lon"])
            self._auto_site(key, ref[0], ref[1], "senzor", st)
            st.note("senzor EO/IR (platforma posibil mobila): fara informatie negativa")
        site = self._by_name[key]
        st.site = key
        slant = f.get("SlantRange")
        default_sigma = max(30.0, 0.01 * slant) if slant else 50.0

        def det(rid, lat, lon, sig_e, sig_n, rho, alt):
            nonlocal out
            R = np.array([[sig_e ** 2, rho * sig_e * sig_n], [rho * sig_e * sig_n, sig_n ** 2]])
            out.append(SensorDetection(rid, key, t, lat, lon, R, pd=site.pd, lambda_fa=site.lambda_fa,
                                       received_at=received_at, altitude_m=alt))

        tlat, tlon = f.get("TargetLocationLatitude"), f.get("TargetLocationLongitude")
        if tlat is not None and tlon is not None:
            ce90 = f.get("TargetErrorEstimateCE90")
            sig = ce90 / 2.146 if ce90 else default_sigma  # CE90 = 2,146 sigma (normala circulara)
            if not ce90:
                st.note("tinta desemnata fara CE90: precizia presupusa din distanta oblica")
            det(f"{key}:{t_epoch:.6f}:tinta", tlat, tlon, sig, sig, 0.0, None)
            st.note("tinta desemnata ST 0601 (tag 40-42): inaltimea tag 42 e fata de nivelul marii")
        if vmti is not None:
            fcl, fco = f.get("FrameCenterLatitude"), f.get("FrameCenterLongitude")
            for vt in vmti.targets:
                rid = f"{key}:{t_epoch:.6f}:vmti{vt.target_id}"
                if vt.location and vt.location.get("lat") is not None:
                    L = vt.location
                    se, sn = L.get("sig_e") or default_sigma, L.get("sig_n") or default_sigma
                    det(rid, L["lat"], L["lon"], max(se, 1.0), max(sn, 1.0), L.get("rho_en") or 0.0, L.get("hae"))
                elif vt.offset_lat is not None and fcl is not None:
                    det(rid, fcl + vt.offset_lat, fco + vt.offset_lon, default_sigma, default_sigma, 0.0, vt.hae)
                    st.note("tinte VMTI ca decalaj fata de centrul cadrului (tag 10/11 + ST 0601 tag 23/24)")
                else:
                    st.need("tinte VMTI doar in pixeli: cer geolocatie (locatie tag 17 sau decalaje + centrul cadrului)")
            if vmti.version is not None and vmti.version < 4:
                st.note(f"VMTI versiunea {vmti.version}: codare veche (nu IMAPB)")
        st.accepted += len(out)
        return out

    # ---------- ADS-B direct ----------
    def _adsb_raw(self, fmt: str, data, received_at, source) -> list:
        st = self._status(source, fmt)
        st.site = "ADS-B"
        st.note("aeronave cooperative: calibreaza radarele si exclud ecourile de avioane din trackerul de drone")
        t_now = received_at if received_at is not None else 0.0
        n0 = len(self.cooperative)
        if fmt in ("beast", "avr"):
            if fmt == "beast":
                msgs = [m for typ, m in adsbmod.iter_beast(bytes(data)) if typ in (0x33, 0x34)]
            else:
                text = data.decode("ascii", "replace") if isinstance(data, (bytes, bytearray)) else data
                msgs = list(adsbmod.parse_avr(text))
            if received_at is None:
                st.need("ora de sosire (received_at): mesajele Beast/AVR nu au ora absoluta")
            for m in msgs:
                state = self._modes.decode(m, t_now)
                if state is not None:
                    self._coop_from_state(state, t_now)
            if self._modes.bad_crc:
                st.note(f"mesaje cu CRC gresit ignorate: {self._modes.bad_crc}")
        elif fmt == "sbs":
            text = data.decode("ascii", "replace") if isinstance(data, (bytes, bytearray)) else data
            for rec in adsbmod.parse_sbs(text):
                self._coop_from_rec(rec, received_at)
        else:  # readsb aircraft.json
            for o in self._objs(data):
                for rec in adsbmod.parse_aircraft_json(o):
                    rec["tod"] = parse_time_tod(rec.pop("epoch"), "epoch")
                    self._coop_from_rec(rec, received_at)
        st.accepted += len(self.cooperative) - n0
        return []

    def _coop_from_state(self, s, t) -> None:
        vx = vy = None
        if s.speed_kt is not None and s.track_deg is not None:
            h = math.radians(s.track_deg)
            vx, vy = s.speed_kt * adsbmod.KT * math.sin(h), s.speed_kt * adsbmod.KT * math.cos(h)
        self._add_coop(CooperativeReport(s.icao, t, s.lat, s.lon,
                                         s.alt_ft * adsbmod.FT if s.alt_ft is not None else None,
                                         vx, vy, s.callsign, source="ADS-B"))

    def _coop_from_rec(self, rec: dict, received_at) -> None:
        st = self._modes.state.setdefault(rec["icao"], adsbmod.AdsbState(rec["icao"]))
        for k in ("speed_kt", "track_deg", "alt_ft", "callsign"):
            if rec.get(k) is not None:
                setattr(st, k, rec[k])
        if rec.get("lat") is None:
            return
        st.lat, st.lon = rec["lat"], rec["lon"]
        t = round(tod_to_t(rec["tod"], self.tod_ref), 4) if rec.get("tod") is not None else received_at
        self._coop_from_state(st, t)

    def _opportunity(self, plot, site) -> str | None:
        """Aeronava ADS-B (peste inaltimea minima) aflata in poarta unui plot radar fara transponder."""
        if not self._adsb:
            return None
        az = math.radians(plot.azimuth_deg)
        r = plot.range_m
        p = site.center + r * np.array([math.sin(az), math.cos(az)])
        best, best_d = None, self.opportunity_gate_m
        for icao, hist in self._adsb.items():
            c = hist[-1]
            dt = plot.t - c.t
            if abs(dt) > 5.0 or (c.height_m or 0.0) < self.opportunity_min_alt_m:
                continue
            if c.vx is None and abs(dt) > 1.5:
                continue  # fara viteza, doar mesaje aproape simultane
            q = self.frame.to_xy(c.lat, c.lon) + (np.array([c.vx, c.vy]) * dt if c.vx is not None else 0.0)
            d = float(np.linalg.norm(q - p))
            if d < best_d:
                best, best_d = icao, d
        return best

    # ---------- Remote ID ----------
    def _remoteid(self, buf: bytes, received_at, source) -> list:
        st = self._status(source, "remoteid")
        st.site = source
        approx = (self.tod_ref + (received_at or 0.0)) % 86400.0
        for loc in ridmod.decode(buf):
            if loc.ts_tenths_hour is None:
                tod = approx
            else:
                tod = ridmod.tod_from_tenths(loc.ts_tenths_hour, approx)
            vx = vy = None
            if loc.speed is not None and loc.direction is not None:
                h = math.radians(loc.direction)
                vx, vy = loc.speed * math.sin(h), loc.speed * math.cos(h)
            c = CooperativeReport(loc.uas_id or f"RID-{source}", round(tod_to_t(tod, self.tod_ref), 4),
                                  loc.lat, loc.lon, loc.alt_geo, vx, vy, source="RemoteID")
            c.operator_lat, c.operator_lon = loc.operator_lat, loc.operator_lon
            c.operator_id, c.self_id = loc.operator_id, loc.self_id
            self._add_coop(c)
            if loc.operator_lat is not None:
                st.note("mesaj System: pozitia operatorului e disponibila (operator_lat/lon)")
            if loc.auth_present:
                st.note("mesaj Authentication prezent: NU e verificat criptografic")
            st.accepted += 1
        st.note("drone cooperative (Remote ID): marcheaza traseele potrivite, nu intra ca tinte")
        return []

    # ---------- Cursor-on-Target ----------
    def _cot(self, text: str, received_at, source) -> list:
        out = []
        st = self._status(source, "cot")
        for ev in cotmod.parse(text):
            tod = parse_time_tod(ev.time) if ev.time else None
            t = round(tod_to_t(tod, self.tod_ref), 4) if tod is not None else received_at
            role = ev.role
            if role == "prieten":
                self.friendly[ev.uid] = FriendlyPosition(ev.uid, tod, ev.lat, ev.lon, ev.hae, ev.callsign,
                                                         ev.type, source="CoT")
                continue
            if role == "cooperativ":
                self._add_coop(CooperativeReport(ev.uid, t, ev.lat, ev.lon, ev.hae, source="CoT"))
                continue
            if role != "tinta":
                st.note(f"tipul {ev.type} ignorat (nu e tinta aeriana sau forta proprie)")
                continue
            if t is None:
                st.need("atributul time in evenimentele CoT")
                continue
            if source not in self._by_name:
                self._auto_site(source, ev.lat, ev.lon, "senzor", st)
            site = self._by_name[source]
            st.site = source
            p = self.frame.to_xy(ev.lat, ev.lon) - site.center
            ce = ev.ce if ev.ce is not None and ev.ce < cotmod.UNKNOWN_ERR else None
            if ce is None:
                st.note("ce necunoscut: precizia pozitiei CoT e presupusa (100 m)")
            s = max(ce or 100.0, 10.0)
            vx = vy = None
            cov = np.eye(2) * s * s
            if ev.speed is not None and ev.course is not None:
                h = math.radians(ev.course)
                vx, vy = ev.speed * math.sin(h), ev.speed * math.cos(h)
                cov = np.diag([s * s, s * s, 5.0 ** 2, 5.0 ** 2])
            if ev.how.startswith("h"):
                st.note("evenimente introduse de om (how=h-*): tratate ca rapoarte, cu precizia din ce")
            out.append(RadarTrackReport(f"{source}:{ev.uid}:{ev.time}", site.sensor_id, _stable_int(ev.uid), t,
                                        float(p[0]), float(p[1]), vx, vy, cov, ev.hae, True,
                                        received_at=received_at))
            st.accepted += 1
        return out

    # ---------- NFFI ----------
    def _nffi(self, text: str, source) -> None:
        st = self._status(source, "nffi")
        v = nffimod.variant(text)
        st.note(f"varianta {v or 'nffi'}" + ("" if v in ("nffi-1.3", "nffi-1.4") else
                                             " (versiune nedeclarata: verificati schema XSD a sursei)"))
        for fp in nffimod.parse(text, source):
            self.friendly[fp.unit_id] = fp
            st.accepted += 1
        st.note("pozitii ale fortelor proprii (echipe); nu intra in trackerul de drone")

    # ---------- marcarea traseelor cooperative ----------
    def annotate_tracks(self, tracker, max_dist: float = 300.0, max_dt: float = 10.0) -> dict:
        """Traseele care se suprapun cu o drona/aeronava cooperativa primesc identitatea ei."""
        found = {}
        for tr in tracker.live_tracks(tracker.now):
            for c in reversed(self.cooperative):
                if abs(c.t - tr.last_time) > max_dt:
                    continue
                pos = tr.state_at(c.t).x[:2]
                if np.linalg.norm(pos - self.frame.to_xy(c.lat, c.lon)) <= max_dist:
                    tr.cooperative_id = f"{c.source}:{c.icao}"
                    found[tr.track_id] = tr.cooperative_id
                    break
        return found

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
                site.heading_known = True
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
        from .sapient_proto import validate as sapient_validate
        for o in objs:
            unknown: list = []
            if self._sapient_schema is not None and any(k[:1].islower() and any(ch.isupper() for ch in k)
                                                        for k in o):
                try:  # forma JSON canonica protobuf (camelCase): conversie exacta dupa schema
                    o = self._sapient_schema.from_json("SapientMessage", o, unknown=unknown)
                except (ProtoError, ValueError) as e:
                    self.errors.append(("sapient", f"JSON SAPIENT invalid: {e}"))
                    continue
            o = _snake_keys(o)
            node = str(o.get("node_id") or o.get("source_id") or "SAPIENT")
            st = self._status(f"SAPIENT:{node}", "sapient")
            prof = self.sapient_nodes.setdefault(node, {"zone": None, "vel_kph": False, "types": [], "icd": None})
            if unknown:
                st.note(f"campuri necunoscute in BSI Flex 335 v2.0, ignorate: {', '.join(unknown[:5])}")
            if self._sapient_schema is not None:
                issues = sapient_validate(self._sapient_schema, o)
                if issues:
                    self.sapient_deviations[node].extend(issues)
                    st.note(f"abateri de la BSI Flex 335 v2.0 (campuri obligatorii/UUID/ULID): {len(issues)} "
                            f"in ultimul mesaj, ex. {issues[0]}")
            tod = parse_time_tod(o.get("timestamp"))
            if "registration" in o:
                self._sapient_registration(node, o["registration"], prof, st)
            if "status_report" in o:
                self._sapient_status(node, o["status_report"], st)
            if "alert" in o:
                self.sapient_alerts.append({"node_id": node, "t": tod, **o["alert"]})
                st.note("mesaje Alert primite (vezi sapient_alerts)")
            if "task_ack" in o:
                self.sapient_task_acks.append({"node_id": node, **o["task_ack"]})
            if "error" in o:
                self.errors.append(("sapient", f"{node}: {o['error'].get('error_message')}"))
            det = o.get("detection_report")
            if det is not None:
                item = self._sapient_detection(node, det, tod, st, received_at, prof)
                if item is not None:
                    st.accepted += 1
                    out.append(item)
        return out

    def _sapient_registration(self, node, reg: dict, prof: dict, st) -> None:
        prof["icd"] = reg.get("icd_version")
        prof["types"] = [str(d.get("node_type", "")) for d in reg.get("node_definition", []) or []]
        prof["name"] = reg.get("name")
        for mode in reg.get("mode_definition", []) or []:
            dd = mode.get("detection_definition") or []
            for d in (dd if isinstance(dd, list) else [dd]):
                lt = d.get("location_type") or {}
                if lt.get("zone"):
                    prof["zone"] = str(lt["zone"])
                vt = d.get("velocity_type") or {}
                units = (vt.get("enu_velocity_units") or {}).get("east_north_rate_units")
                if units in ("SPEED_UNITS_KPH", 2):
                    prof["vel_kph"] = True
        st.note(f"inregistrare: {', '.join(t for t in prof['types'] if t) or 'tip nespecificat'}, "
                f"ICD {prof['icd'] or '?'}")
        if prof["icd"] and "2" not in str(prof["icd"]).split(".")[0][-2:]:
            st.note(f"versiune ICD declarata '{prof['icd']}': decodorul e scris pentru BSI Flex 335 v2.0")

    def _sapient_status(self, node, status: dict, st) -> None:
        if status.get("system") in ("SYSTEM_GOODBYE", 5):
            st.note("nodul a anuntat deconectarea (SYSTEM_GOODBYE)")
        loc = status.get("node_location")
        ll = self._sapient_location(loc, st, node) if loc else None
        if ll and node not in self._by_name:
            site = RadarSite(node, ll[0], ll[1], negative_info=False, kind="senzor", min_alt=0.0)
            self.add_site(site)
            st.site = node
            st.note("pozitia nodului invatata din statusReport")
            st.need("acoperirea si altitudinea minima de detectie (pentru informatie negativa)")

    _LOC_CS = {1: "LAT_LNG_DEG_M", 2: "LAT_LNG_RAD_M", 5: "UTM_M", 0: "UNSPECIFIED"}
    _RB_CS = {1: "DEGREES_M", 2: "RADIANS_M", 3: "DEGREES_KM", 4: "RADIANS_KM", 0: "UNSPECIFIED"}
    _RB_DATUM = {1: "TRUE", 2: "MAGNETIC", 3: "GRID", 4: "PLATFORM", 0: "UNSPECIFIED"}

    @staticmethod
    def _enum(v, table: dict, prefix: str) -> str:
        if isinstance(v, int):
            return table.get(v, str(v))
        v = str(v or "UNSPECIFIED").upper()
        return v[len(prefix):] if v.startswith(prefix) else v

    def _sapient_location(self, loc: dict, st, node: str | None = None) -> tuple | None:
        """(lat, lon) din Location; x = longitudine / est (conventia 'sapient.location.x')."""
        cs = self._enum(loc.get("coordinate_system"), self._LOC_CS, "LOCATION_COORDINATE_SYSTEM_")
        datum = self._enum(loc.get("datum"), {0: "UNSPECIFIED", 1: "WGS84_E", 2: "WGS84_G"}, "LOCATION_DATUM_")
        x, y = loc.get("x"), loc.get("y")
        if x is None or y is None:
            return None
        x, y = float(x), float(y)
        if datum == "WGS84_G" and loc.get("z") is not None:
            st.note("z raportat fata de geoid (WGS84_G): conversia la inaltime elipsoidala cere modelul EGM, "
                    "neaplicata (diferenta ~30-40 m in Romania)")
        if cs == "UNSPECIFIED":
            st.note("abatere: coordinate_system nespecificat; presupus LAT_LNG_DEG_M")
            cs = "LAT_LNG_DEG_M"
        if cs == "UTM_M":
            if abs(x) <= 180.0 and abs(y) <= 180.0:
                # abatere cunoscuta din mostrele Dstl (care pun latitudinea pe x): ordinea e ambigua; alegem
                # varianta plauzibila cea mai apropiata de nod sau de zona de operare si o semnalam
                cands = [(lat, lon) for lat, lon in ((x, y), (y, x)) if abs(lat) <= 90.0]
                site = self._by_name.get(node) if node else None
                ref = (site.lat, site.lon) if site is not None else (self.frame.lat0, self.frame.lon0)
                lat, lon = min(cands, key=lambda c: (c[0] - ref[0]) ** 2 + (c[1] - ref[1]) ** 2)
                st.note("abatere: valori lat/lon in campuri UTM (cunoscuta din mostrele Dstl); ordinea x/y "
                        "aleasa dupa apropierea de nod/zona de operare")
                return lat, lon
            zone = loc.get("utm_zone") or (self.sapient_nodes.get(node, {}).get("zone") if node else None)
            parsed = self._utm_zone(zone, node, st)
            if parsed is None:
                return None
            return utm_to_latlon(x, y, parsed[0], parsed[1])
        if cs == "LAT_LNG_RAD_M":
            x, y = math.degrees(x), math.degrees(y)
        return y, x

    def _utm_zone(self, zone, node, st) -> tuple | None:
        """'30N' (emisfera, forma oficiala) sau '30U' (banda de latitudine). 'S' e ambiguu."""
        m = re.match(r"^\s*(\d{1,2})\s*([A-Za-z]?)\s*$", str(zone or ""))
        if not m:
            st.need("zona UTM (Registration: location_type.zone sau Location.utm_zone), ex. '35N'")
            return None
        num, letter = int(m.group(1)), m.group(2).upper()
        if letter in ("", "N"):
            return num, True
        if letter == "S":
            site = self._by_name.get(node) if node else None
            if site is not None:
                st.note("zona UTM cu litera 'S' (emisfera sud sau banda S): rezolvata dupa pozitia nodului")
                return num, site.lat >= 0
            st.need("zona UTM 'S' e ambigua (emisfera sud vs. banda S, 32-40 grade N): configurati pozitia nodului")
            return None
        if letter in "CDEFGHJKLM":
            return num, False
        if letter in "PQRTUVWX":
            st.note(f"zona UTM data ca banda de latitudine ('{zone}'), nu ca emisfera")
            return num, True
        st.need(f"zona UTM nerecunoscuta '{zone}'")
        return None

    @staticmethod
    def _covariance(det: dict) -> np.ndarray | None:
        """Covarianta x/y din object_info 'covariance_*' (practica observata, nestandardizata)."""
        vals = {}
        for oi in det.get("object_info", []) or []:
            typ = str(oi.get("type", "")).lower()
            if typ.startswith("covariance_"):
                try:
                    vals[typ[len("covariance_"):]] = float(oi.get("value"))
                except (TypeError, ValueError):
                    return None
        if not {"xx", "yy"} <= set(vals):
            return None
        xy = vals.get("xy", vals.get("yx", 0.0))
        if "xy" in vals and "yx" in vals and abs(vals["xy"] - vals["yx"]) > 1e-6 * max(1.0, abs(vals["xy"])):
            return np.full((2, 2), np.nan)  # nesimetrica
        return np.array([[vals["xx"], xy], [xy, vals["yy"]]])

    def _sapient_detection(self, node, det, tod, st, received_at, prof):
        cls = det.get("classification", []) or []
        if isinstance(cls, dict):
            cls = [cls]
        top = max(cls, key=lambda c: float(c.get("confidence", 0) or 0), default=None)
        drone_class = None
        if top is not None:
            ctype = str(top.get("type", "")).lower()
            conf = float(top.get("confidence", 0) or 0)
            if any(k in ctype for k in self.drop_non_drone_classes) and conf >= self.min_class_confidence:
                st.note("detectii clasificate sigur ca non-drona (pasare, persoana, vehicul) sunt ignorate")
                return None
            drone_class = None if ctype in ("", "uav", "drone", "unknown") else ctype
        if tod is None:
            st.need("timestamp in mesaje")
            return None
        t = round(tod_to_t(tod, self.tod_ref), 4)
        obj = det.get("object_id")
        rid = str(det.get("report_id") or f"{node}:{tod:.3f}:{obj}")
        loc, rb = det.get("location"), det.get("range_bearing")
        site = self._by_name.get(node)
        if loc is not None:
            ll = self._sapient_location(loc, st, node)
            if ll is None:
                return None
            if site is None:
                site = self._auto_site(node, ll[0], ll[1], "senzor", st)
            p = self.frame.to_xy(*ll) - site.center
            cs = self._enum(loc.get("coordinate_system"), self._LOC_CS, "LOCATION_COORDINATE_SYSTEM_")
            ex, ey = float(loc.get("x_error") or 0), float(loc.get("y_error") or 0)
            if cs.startswith("LAT_LNG") and (ex or ey):  # erori unghiulare -> metri
                k = 1.0 if cs == "LAT_LNG_DEG_M" else math.degrees(1.0)
                ex = ex * k * 111320.0 * math.cos(math.radians(ll[0]))
                ey = ey * k * 111320.0
            R = np.diag([max(ex, 10.0) ** 2 if ex else 50.0 ** 2, max(ey, 10.0) ** 2 if ey else 50.0 ** 2])
            if not (ex or ey):
                st.note("fara x_error/y_error: precizia pozitiei e presupusa (50 m)")
            height = loc.get("z")
        elif rb is not None:
            if site is None:
                st.need("pozitia nodului (statusReport cu node_location sau configurare): "
                        "range_bearing e relativ la nod")
                return None
            r = self._range_bearing(rb, site, node, st)
            if r is None:
                return None
            p, R, height = r
        else:
            st.need("location sau range_bearing in detection_report")
            return None
        cov_oi = self._covariance(det)
        if cov_oi is not None:
            if np.all(np.isfinite(cov_oi)) and np.all(np.linalg.eigvalsh(cov_oi) > 0):
                R = cov_oi
                st.note("covarianta pozitiei citita din object_info (covariance_*)")
            else:
                st.note("abatere: covarianta din object_info nesimetrica sau nepozitiv definita; ignorata")
        vel = det.get("enu_velocity")
        vx = vy = None
        if vel is not None and vel.get("east_rate") is not None and vel.get("north_rate") is not None:
            k = 1 / 3.6 if prof.get("vel_kph") else 1.0
            vx, vy = float(vel["east_rate"]) * k, float(vel["north_rate"]) * k
        if det.get("id"):
            st.note("detectii cu identitate (campul id, ex. inmatriculare): pastrata pe raport")
        if obj is not None:
            cov = R
            if vx is not None:
                cov = np.zeros((4, 4))
                cov[:2, :2] = R
                k = 1 / 3.6 if prof.get("vel_kph") else 1.0
                se = float(vel.get("east_rate_error") or 2.0) * k
                sn = float(vel.get("north_rate_error") or 2.0) * k
                cov[2:, 2:] = np.diag([max(se, 0.5) ** 2, max(sn, 0.5) ** 2])
            rep = RadarTrackReport(rid, site.sensor_id, _stable_int(obj), t, float(p[0]), float(p[1]),
                                   vx, vy, cov, float(height) if height is not None else None, True,
                                   drone_class=drone_class, received_at=received_at)
            rep.ident = det.get("id")
            return rep
        lat, lon = self.frame.to_latlon(*(site.center + p))
        return SensorDetection(rid, site.sensor_id, t, lat, lon, R, drone_class=drone_class, pd=site.pd,
                               lambda_fa=site.lambda_fa, received_at=received_at,
                               altitude_m=float(height) if height is not None else None)

    def _range_bearing(self, rb: dict, site: RadarSite, node, st):
        """(pozitie fata de nod, covarianta, inaltime) din RangeBearing, cu referinta de nord corecta."""
        cs = self._enum(rb.get("coordinate_system"), self._RB_CS, "RANGE_BEARING_COORDINATE_SYSTEM_")
        datum = self._enum(rb.get("datum"), self._RB_DATUM, "RANGE_BEARING_DATUM_")
        if cs == "UNSPECIFIED":
            st.note("abatere: coordinate_system nespecificat in range_bearing; presupus grade/metri")
            cs = "DEGREES_M"
        ang = (lambda a: float(a)) if cs.startswith("RADIANS") else (lambda a: math.radians(float(a)))
        rscale = 1000.0 if cs.endswith("_KM") else 1.0
        az = ang(rb.get("azimuth") or 0.0)
        if datum in ("TRUE", "UNSPECIFIED"):
            if datum == "UNSPECIFIED":
                st.note("abatere: datum nespecificat in range_bearing; presupus nord adevarat")
        elif datum == "MAGNETIC":
            if site.mag_declination_deg is None:
                st.need("declinatia magnetica a nodului (mag_declination_deg; ~+6 grade in Dobrogea, din WMM)")
                return None
            az += math.radians(site.mag_declination_deg)
        elif datum == "GRID":
            zone = self.sapient_nodes.get(node, {}).get("zone")
            zn = self._utm_zone(zone, node, st)[0] if zone and self._utm_zone(zone, node, st) else \
                int((site.lon + 180.0) // 6) + 1
            lon0 = (zn - 1) * 6 - 180 + 3
            gamma = math.atan(math.tan(math.radians(site.lon - lon0)) * math.sin(math.radians(site.lat)))
            az += gamma
            st.note(f"azimut fata de nordul grilei: corectat cu convergenta meridianelor ({math.degrees(gamma):.2f} grd)")
        elif datum == "PLATFORM":
            if not site.heading_known:
                st.need("capul platformei nodului (heading_deg) pentru azimutul relativ la platforma")
                return None
            az += math.radians(site.heading_deg)
        el = ang(rb.get("elevation") or 0.0)
        rng = float(rb.get("range")) * rscale
        ground = rng * math.cos(el)
        s_az = ang(rb.get("azimuth_error")) if rb.get("azimuth_error") else math.radians(site.sigma_az_deg)
        s_r = float(rb.get("range_error")) * rscale if rb.get("range_error") else site.sigma_range
        p, R = polar_gauss_moments(np.zeros(2), az, ground, s_az, s_r)
        height = (site.antenna_height + rng * math.sin(el)) if rb.get("elevation") is not None else None
        return p, R, height

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
