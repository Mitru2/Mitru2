"""DIS (IEEE 1278.1, Distributed Interactive Simulation): Entity State PDU, pentru exercitii.

Simulatoarele de exercitiu (ex. generatoare de forte, simulatoare de drone) publica entitatile ca
Entity State PDU pe UDP (de obicei portul 3000). SANTINELA le poate primi ca tinte de exercitiu,
ca operatorii sa se antreneze pe imaginea operationala comuna. Datele DIS sunt simulate: intra in
tracker doar daca adaptorul are ``dis_exercise=True`` si sunt marcate ca sursa de exercitiu.

Structura (verificata pe disectorul Wireshark packet-dis.c, pe PDU-ul de test open-dis si pe
capturile publice Wireshark DIS_EntityState_1/2):

* antet de 12 octeti: versiune, exercitiu, tip PDU (1 = Entity State), familie (1), marcaj de timp
  (31 de biti = fractiune din ora, bitul 0 = absolut), lungime, completare/stare;
* Entity ID (site, aplicatie, entitate), Force ID, numar de parametri variabili, tipul entitatii
  (fel, domeniu, tara, categorie, subcategorie, specific, extra) si tipul alternativ;
* viteza liniara (3 x float32, m/s) si pozitia (3 x float64, m) in sistemul geocentric WGS-84
  (ECEF), orientarea (psi, theta, phi, radiani), aspectul, parametrii de extrapolare (40 o.),
  marcajul (1 + 11 caractere), capabilitatile si parametrii variabili (16 o. fiecare).

Toate campurile sunt big-endian. O datagrama poate contine mai multe PDU-uri.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass

HEADER = 12
ES_MIN = 144
PDU_ENTITY_STATE = 1
KINDS = {0: "altul", 1: "platforma", 2: "munitie", 3: "forma de viata", 4: "mediu", 5: "obiect cultural",
         6: "aprovizionare", 7: "radio", 8: "consumabil", 9: "senzor/emitator"}
DOMAINS = {0: "altul", 1: "terestru", 2: "aerian", 3: "suprafata", 4: "submarin", 5: "spatial"}
FORCES = {0: "altul", 1: "prieten", 2: "advers", 3: "neutru"}   # SISO-REF-010 UID 6 (primele valori)

WGS84_A = 6378137.0
WGS84_F = 1 / 298.257223563
WGS84_E2 = WGS84_F * (2 - WGS84_F)


class DisError(ValueError):
    pass


def geodetic_to_ecef(lat: float, lon: float, h: float) -> tuple[float, float, float]:
    la, lo = math.radians(lat), math.radians(lon)
    n = WGS84_A / math.sqrt(1 - WGS84_E2 * math.sin(la) ** 2)
    return ((n + h) * math.cos(la) * math.cos(lo), (n + h) * math.cos(la) * math.sin(lo),
            (n * (1 - WGS84_E2) + h) * math.sin(la))


def ecef_to_geodetic(x: float, y: float, z: float) -> tuple[float, float, float]:
    """Latitudine, longitudine (grade) si inaltime elipsoidala (m); iterativ, sub 1 mm."""
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    lat = math.atan2(z, p * (1 - WGS84_E2))
    h = 0.0
    for _ in range(10):
        n = WGS84_A / math.sqrt(1 - WGS84_E2 * math.sin(lat) ** 2)
        if abs(math.cos(lat)) > 1e-9:
            h = p / math.cos(lat) - n
        else:
            h = abs(z) - n * (1 - WGS84_E2)
        new = math.atan2(z, p * (1 - WGS84_E2 * n / (n + h)))
        if abs(new - lat) < 1e-13:
            lat = new
            break
        lat = new
    return math.degrees(lat), math.degrees(lon), h


def ecef_to_enu(lat: float, lon: float, v: tuple) -> tuple[float, float, float]:
    """Un vector ECEF (ex. viteza) in componente est, nord, sus la (lat, lon)."""
    la, lo = math.radians(lat), math.radians(lon)
    sl, cl, so, co = math.sin(la), math.cos(la), math.sin(lo), math.cos(lo)
    vx, vy, vz = v
    return (-so * vx + co * vy, -sl * co * vx - sl * so * vy + cl * vz, cl * co * vx + cl * so * vy + sl * vz)


def enu_to_ecef(lat: float, lon: float, e: float, n: float, u: float) -> tuple[float, float, float]:
    la, lo = math.radians(lat), math.radians(lon)
    sl, cl, so, co = math.sin(la), math.cos(la), math.sin(lo), math.cos(lo)
    return (-so * e - sl * co * n + cl * co * u, co * e - sl * so * n + cl * so * u, cl * n + sl * u)


@dataclass
class EntityState:
    version: int
    exercise: int
    timestamp_raw: int
    site: int
    application: int
    entity: int
    force: int
    entity_type: tuple             # (fel, domeniu, tara, categorie, subcategorie, specific, extra)
    velocity_ecef: tuple
    location_ecef: tuple
    orientation: tuple             # psi, theta, phi (radiani)
    appearance: int
    dr_algorithm: int
    marking: str
    capabilities: int
    n_variable: int

    @property
    def uid(self) -> str:
        return f"DIS-{self.site}-{self.application}-{self.entity}"

    @property
    def absolute_time(self) -> bool:
        return bool(self.timestamp_raw & 1)

    @property
    def seconds_past_hour(self) -> float:
        return (self.timestamp_raw >> 1) * 3600.0 / 0x7FFFFFFF

    @property
    def kind(self) -> str:
        return KINDS.get(self.entity_type[0], str(self.entity_type[0]))

    @property
    def domain(self) -> str:
        return DOMAINS.get(self.entity_type[1], str(self.entity_type[1]))

    @property
    def force_name(self) -> str:
        return FORCES.get(self.force, f"fortat {self.force}")

    @property
    def type_text(self) -> str:
        return ".".join(str(v) for v in self.entity_type)

    def geodetic(self) -> tuple[float, float, float]:
        return ecef_to_geodetic(*self.location_ecef)

    def velocity_enu(self) -> tuple[float, float, float]:
        lat, lon, _ = self.geodetic()
        return ecef_to_enu(lat, lon, self.velocity_ecef)


def iter_pdus(buf: bytes):
    """(tip, familie, octetii PDU-ului) pentru fiecare PDU din datagrama."""
    i = 0
    buf = bytes(buf)
    while i + HEADER <= len(buf):
        n = struct.unpack(">H", buf[i + 8:i + 10])[0]
        if n < HEADER or i + n > len(buf):
            raise DisError(f"lungime PDU incoerenta ({n}) la octetul {i}")
        yield buf[i + 2], buf[i + 3], buf[i:i + n]
        i += n
    if i != len(buf):
        raise DisError("octeti in plus dupa ultimul PDU")


def looks_like_dis(buf) -> bool:
    if not isinstance(buf, (bytes, bytearray)) or len(buf) < HEADER:
        return False
    if not 1 <= buf[0] <= 7 or not 1 <= buf[2] <= 72 or buf[3] > 13:
        return False
    try:
        return all(1 <= v[0] <= 7 for _, _, v in iter_pdus(buf))
    except DisError:
        return False


def decode_entity_state(p: bytes) -> EntityState:
    if len(p) < ES_MIN or p[2] != PDU_ENTITY_STATE:
        raise DisError("nu e Entity State PDU (min. 144 de octeti)")
    site, app, ent = struct.unpack(">HHH", p[12:18])
    force, nvar = p[18], p[19]
    et = struct.unpack(">BBHBBBB", p[20:28])
    vel = struct.unpack(">fff", p[36:48])
    loc = struct.unpack(">ddd", p[48:72])
    ori = struct.unpack(">fff", p[72:84])
    appearance = struct.unpack(">I", p[84:88])[0]
    dr = p[88]
    charset = p[128]
    raw = p[129:140]
    marking = raw.split(b"\x00")[0].decode("ascii", "replace") if charset == 1 else raw.hex()
    caps = struct.unpack(">I", p[140:144])[0]
    if len(p) < ES_MIN + 16 * nvar:
        raise DisError("parametri variabili trunchiati")
    return EntityState(p[0], p[1], struct.unpack(">I", p[4:8])[0], site, app, ent, force, et, vel, loc, ori,
                       appearance, dr, marking, caps, nvar)


def decode(buf: bytes) -> list[EntityState]:
    return [decode_entity_state(p) for t, _, p in iter_pdus(buf) if t == PDU_ENTITY_STATE]


def encode_entity_state(site: int, application: int, entity: int, lat: float, lon: float, hae: float,
                        v_enu: tuple = (0.0, 0.0, 0.0), force: int = 2, entity_type=(1, 2, 0, 0, 0, 0, 0),
                        marking: str = "", seconds_past_hour: float = 0.0, exercise: int = 1,
                        version: int = 7) -> bytes:
    """Entity State PDU (pentru teste si simulare)."""
    loc = geodetic_to_ecef(lat, lon, hae)
    vel = enu_to_ecef(lat, lon, *v_enu)
    ts = (int(round(seconds_past_hour / 3600.0 * 0x7FFFFFFF)) << 1) | 1
    body = (struct.pack(">HHHBB", site, application, entity, force, 0) + struct.pack(">BBHBBBB", *entity_type)
            + struct.pack(">BBHBBBB", *entity_type) + struct.pack(">fff", *vel) + struct.pack(">ddd", *loc)
            + struct.pack(">fff", 0.0, 0.0, 0.0) + struct.pack(">I", 0) + bytes([2]) + bytes(39)
            + bytes([1]) + marking.encode("ascii")[:11].ljust(11, b"\x00") + struct.pack(">I", 0))
    head = struct.pack(">BBBBIHH", version, exercise, PDU_ENTITY_STATE, 1, ts, HEADER + len(body), 0)
    return head + body


def tod_from_past_hour(sec: float, approx_tod: float) -> float:
    """Ora zilei din secundele trecute de la inceputul orei, aleasa langa ``approx_tod``."""
    hour = int(approx_tod // 3600)
    cands = [h * 3600 + sec for h in (hour - 1, hour, hour + 1)]
    return min(cands, key=lambda c: abs(c - approx_tod)) % 86400.0
