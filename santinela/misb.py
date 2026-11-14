"""MISB / STANAG 4609: metadate KLV din fluxurile video EO/IR.

* **ST 0601** (UAS Datalink Local Set): pozitia senzorului, linia de vizare, centrul cadrului,
  tinta desemnata (taguri 40-42) cu erorile CE90/LE90 (45/46);
* **ST 0903** (VMTI): tintele mobile detectate in imagine, cu pozitia lor (tag 17,
  pachet de locatie cu sigma si corelatii) sau decalaje fata de centrul cadrului (10/11);
* **ST 1201** (IMAPB): codarea in virgula fixa folosita de ST 0903 (versiunea >= 4);
* **MPEG-TS** (STANAG 4609): extragerea pachetelor KLV din fluxul video.

Surse folosite (citite ca date, nu executate): maparile ST 0601 si exemplele din ST 0601.11
sectiunea 8 din klvdata 0.0.3 (MIT, paretech); tagurile ST 0903, pachetul de locatie, codarea
IMAPB si exemplele din anexa ST 0903 din jmisb (MIT, jmisb.org). Suma de control (tag 1) este
suma pe 16 biti a cuvintelor big-endian peste tot pachetul, fara ultimii 2 octeti.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field

UL_0601 = bytes.fromhex("060E2B34020B01010E01030101000000")
UL_0903 = bytes.fromhex("060E2B34020B01010E01030306000000")
UL_PREFIX = bytes.fromhex("060E2B34")


class KlvError(ValueError):
    pass


# generat din klvdata 0.0.3 (MIT, paretech), misb0601.py: tag -> (nume, tip, domeniu, interval)
ST0601 = {
    1: ('Checksum', 'bytes', None, None),
    2: ('PrecisionTimeStamp', 'time', None, None),
    3: ('MissionID', 'str', None, None),
    4: ('PlatformTailNumber', 'str', None, None),
    5: ('PlatformHeadingAngle', 'map', (0, 65535), (0.0, 360.0)),
    6: ('PlatformPitchAngle', 'map', (-32767, 32767), (-20.0, 20.0)),
    7: ('PlatformRollAngle', 'map', (-32767, 32767), (-50.0, 50.0)),
    8: ('PlatformTrueAirspeed', 'map', (0, 255), (0.0, 255.0)),
    9: ('PlatformIndicatedAirspeed', 'map', (0, 255), (0.0, 255.0)),
    10: ('PlatformDesignation', 'str', None, None),
    11: ('ImageSourceSensor', 'str', None, None),
    12: ('ImageCoordinateSystem', 'str', None, None),
    13: ('SensorLatitude', 'map', (-2147483647, 2147483647), (-90.0, 90.0)),
    14: ('SensorLongitude', 'map', (-2147483647, 2147483647), (-180.0, 180.0)),
    15: ('SensorTrueAltitude', 'map', (0, 65535), (-900.0, 19000.0)),
    16: ('SensorHorizontalFieldOfView', 'map', (0, 65535), (0.0, 180.0)),
    17: ('SensorVerticalFieldOfView', 'map', (0, 65535), (0.0, 180.0)),
    18: ('SensorRelativeAzimuthAngle', 'map', (0, 4294967295), (0.0, 360.0)),
    19: ('SensorRelativeElevationAngle', 'map', (-2147483647, 2147483647), (-180.0, 180.0)),
    20: ('SensorRelativeRollAngle', 'map', (0, 4294967295), (0.0, 360.0)),
    21: ('SlantRange', 'map', (0, 4294967295), (0.0, 5000000.0)),
    22: ('TargetWidth', 'map', (0, 65535), (0.0, 10000.0)),
    23: ('FrameCenterLatitude', 'map', (-2147483647, 2147483647), (-90.0, 90.0)),
    24: ('FrameCenterLongitude', 'map', (-2147483647, 2147483647), (-180.0, 180.0)),
    25: ('FrameCenterElevation', 'map', (0, 65535), (-900.0, 19000.0)),
    26: ('OffsetCornerLatitudePoint1', 'map', (-32767, 32767), (-0.075, 0.075)),
    27: ('OffsetCornerLongitudePoint1', 'map', (-32767, 32767), (-0.075, 0.075)),
    28: ('OffsetCornerLatitudePoint2', 'map', (-32767, 32767), (-0.075, 0.075)),
    29: ('OffsetCornerLongitudePoint2', 'map', (-32767, 32767), (-0.075, 0.075)),
    30: ('OffsetCornerLatitudePoint3', 'map', (-32767, 32767), (-0.075, 0.075)),
    31: ('OffsetCornerLongitudePoint3', 'map', (-32767, 32767), (-0.075, 0.075)),
    32: ('OffsetCornerLatitudePoint4', 'map', (-32767, 32767), (-0.075, 0.075)),
    33: ('OffsetCornerLongitudePoint4', 'map', (-32767, 32767), (-0.075, 0.075)),
    34: ('IcingDetected', 'map', (0, 255), (0.0, 255.0)),
    35: ('WindDirection', 'map', (0, 65535), (0.0, 360.0)),
    36: ('WindSpeed', 'map', (0, 255), (0.0, 100.0)),
    37: ('StaticPressure', 'map', (0, 65535), (0.0, 5000.0)),
    38: ('DensityAltitude', 'map', (0, 65535), (-900.0, 19000.0)),
    39: ('OutsideAirTemperature', 'map', (0, 255), (0.0, 255.0)),
    40: ('TargetLocationLatitude', 'map', (-2147483647, 2147483647), (-90.0, 90.0)),
    41: ('TargetLocationLongitude', 'map', (-2147483647, 2147483647), (-180.0, 180.0)),
    42: ('TargetLocationElevation', 'map', (0, 65535), (-900.0, 19000.0)),
    43: ('TargetTrackGateWidth', 'map', (0, 255), (0.0, 510.0)),  # x2 pixeli (exemplul ST 0601.11; klvdata: 512)
    44: ('TargetTrackGateHeight', 'map', (0, 255), (0.0, 510.0)),  # x2 pixeli
    45: ('TargetErrorEstimateCE90', 'map', (0, 65535), (0.0, 4095.0)),
    46: ('TargetErrorEstimateLE90', 'map', (0, 65535), (0.0, 4095.0)),
    47: ('GenericFlagData01', 'map', (0, 255), (0.0, 255.0)),
    49: ('DifferentialPressure', 'map', (0, 65535), (0.0, 5000.0)),
    50: ('PlatformAngleOfAttack', 'map', (-32767, 32767), (-20.0, 20.0)),
    51: ('PlatformVerticalSpeed', 'map', (-32767, 32767), (-180.0, 180.0)),
    52: ('PlatformSideslipAngle', 'map', (-32767, 32767), (-20.0, 20.0)),
    53: ('AirfieldBarometricPressure', 'map', (0, 65535), (0.0, 5000.0)),
    54: ('AirfieldElevation', 'map', (0, 65535), (-900.0, 19000.0)),
    55: ('RelativeHumidity', 'map', (0, 255), (0.0, 100.0)),
    56: ('PlatformGroundSpeed', 'map', (0, 255), (0.0, 255.0)),
    57: ('GroundRange', 'map', (0, 4294967295), (0.0, 5000000.0)),
    58: ('PlatformFuelRemaining', 'map', (0, 65535), (0.0, 10000.0)),
    59: ('PlatformCallSign', 'str', None, None),
    60: ('WeaponLoad', 'map', (0, 65535), (0.0, 65535.0)),
    61: ('WeaponFired', 'map', (0, 255), (0.0, 255.0)),
    62: ('LaserPRFCode', 'map', (0, 65535), (0.0, 65535.0)),
    63: ('SensorFieldOfViewName', 'map', (0, 255), (0.0, 255.0)),
    64: ('PlatformMagneticHeading', 'map', (0, 65535), (0.0, 360.0)),
    65: ('UASLSVersionNumber', 'map', (0, 255), (0.0, 255.0)),
    67: ('AlternatePlatformLatitude', 'map', (-2147483647, 2147483647), (-90.0, 90.0)),
    68: ('AlternatePlatformLongitude', 'map', (-2147483647, 2147483647), (-180.0, 180.0)),
    69: ('AlternatePlatformAltitude', 'map', (0, 65535), (-900.0, 19000.0)),
    70: ('AlternatePlatformName', 'str', None, None),
    71: ('AlternatePlatformHeading', 'map', (0, 65535), (0.0, 360.0)),
    72: ('EventStartTime', 'time', None, None),
    73: ('RVTLocalSet', 'raw', None, None),
    74: ('VMTILocalSet', 'raw', None, None),
    75: ('SensorEllipsoidHeightConversion', 'map', (0, 65535), (-900.0, 19000.0)),
    76: ('AlternatePlatformEllipsoidHeight', 'map', (0, 65535), (-900.0, 19000.0)),
    77: ('OperationalMode', 'str', None, None),
    78: ('FrameCenterHeightAboveEllipsoid', 'map', (0, 65535), (-900.0, 19000.0)),
    79: ('SensorNorthVelocity', 'map', (-32767, 32767), (-327.0, 327.0)),
    80: ('SensorEastVelocity', 'map', (-32767, 32767), (-327.0, 327.0)),
    82: ('CornerLatitudePoint1Full', 'map', (-2147483647, 2147483647), (-90.0, 90.0)),
    83: ('CornerLongitudePoint1Full', 'map', (-2147483647, 2147483647), (-180.0, 180.0)),
    84: ('CornerLatitudePoint2Full', 'map', (-2147483647, 2147483647), (-90.0, 90.0)),
    85: ('CornerLongitudePoint2Full', 'map', (-2147483647, 2147483647), (-180.0, 180.0)),
    86: ('CornerLatitudePoint3Full', 'map', (-2147483647, 2147483647), (-90.0, 90.0)),
    87: ('CornerLongitudePoint3Full', 'map', (-2147483647, 2147483647), (-180.0, 180.0)),
    88: ('CornerLatitudePoint4Full', 'map', (-2147483647, 2147483647), (-90.0, 90.0)),
    89: ('CornerLongitudePoint4Full', 'map', (-2147483647, 2147483647), (-180.0, 180.0)),
    90: ('PlatformPitchAngleFull', 'map', (-2147483647, 2147483647), (-90.0, 90.0)),
    91: ('PlatformRollAngleFull', 'map', (-2147483647, 2147483647), (-90.0, 90.0)),
    92: ('PlatformAngleOfAttackFull', 'map', (-2147483647, 2147483647), (-90.0, 90.0)),
    93: ('PlatformSideslipAngleFull', 'map', (-2147483647, 2147483647), (-90.0, 90.0)),
    96: ('TargetWidthExtended', 'map', (0, 255), (0.0, 255.0)),
    103: ('DensityAltitudeExtended', 'map', (0, 65535), (-900.0, 40000.0)),
    104: ('SensorEllipsoidHeightExtended', 'map', (0, 65535), (-900.0, 40000.0)),
    105: ('AlternatePlatformEllipsoidHeightExtended', 'map', (0, 65535), (-900.0, 40000.0)),
}


# ------------------------------------------------------------------ BER si sume de control
def ber_length(buf: bytes, i: int) -> tuple[int, int]:
    """(lungime, index dupa campul de lungime) - forma scurta sau lunga."""
    b = buf[i]
    if b < 0x80:
        return b, i + 1
    n = b & 0x7F
    if n == 0 or i + 1 + n > len(buf):
        raise KlvError("lungime BER invalida")
    return int.from_bytes(buf[i + 1:i + 1 + n], "big"), i + 1 + n


def ber_oid(buf: bytes, i: int) -> tuple[int, int]:
    """Tag BER-OID: 7 biti pe octet, bitul cel mai semnificativ = continuare."""
    v = 0
    while True:
        if i >= len(buf):
            raise KlvError("tag BER-OID trunchiat")
        b = buf[i]
        i += 1
        v = (v << 7) | (b & 0x7F)
        if not b & 0x80:
            return v, i


def enc_ber_length(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def enc_ber_oid(v: int) -> bytes:
    out = [v & 0x7F]
    v >>= 7
    while v:
        out.append(0x80 | (v & 0x7F))
        v >>= 7
    return bytes(reversed(out))


def checksum16(data: bytes) -> int:
    s = 0
    for k in range(0, len(data) - 1, 2):
        s += (data[k] << 8) | data[k + 1]
    if len(data) % 2:
        s += data[-1] << 8
    return s & 0xFFFF


def local_set(buf: bytes) -> list[tuple[int, bytes]]:
    """Campurile unui local set (tag BER-OID, lungime BER, valoare)."""
    out, i = [], 0
    while i < len(buf):
        tag, i = ber_oid(buf, i)
        n, i = ber_length(buf, i)
        if i + n > len(buf):
            raise KlvError(f"campul {tag} depaseste setul")
        out.append((tag, buf[i:i + n]))
        i += n
    return out


# ------------------------------------------------------------------ ST 0601
def _mapped(value: bytes, dom: tuple, rng: tuple) -> float | None:
    signed = dom[0] < 0
    raw = int.from_bytes(value, "big", signed=signed)
    if signed and raw == -(1 << (8 * len(value) - 1)):
        return None  # indicator de eroare / in afara domeniului (ST 0601)
    return (raw - dom[0]) * (rng[1] - rng[0]) / (dom[1] - dom[0]) + rng[0]


def decode_0601_value(tag: int, value: bytes):
    spec = ST0601.get(tag)
    if spec is None:
        return value
    name, kind, dom, rng = spec
    if kind == "map":
        return _mapped(value, dom, rng)
    if kind == "str":
        return value.decode("utf-8", "replace").rstrip("\x00 ")
    if kind == "time":
        return int.from_bytes(value, "big") / 1e6   # microsecunde de la epoca Unix
    return value


@dataclass
class UasPacket:
    fields: dict                    # nume ST 0601 -> valoare
    checksum_ok: bool | None
    vmti: "VmtiPacket | None" = None
    raw_tags: list = field(default_factory=list)

    @property
    def time(self) -> float | None:
        return self.fields.get("PrecisionTimeStamp")


def decode_0601(packet: bytes) -> UasPacket:
    """Un pachet complet (cheia UL + lungime BER + local set)."""
    if packet[:16] != UL_0601:
        raise KlvError("nu e un pachet ST 0601 (cheia UL)")
    n, i = ber_length(packet, 16)
    body = packet[i:i + n]
    if len(body) != n:
        raise KlvError("pachet ST 0601 trunchiat")
    fields, tags, ok, vmti = {}, [], None, None
    for tag, value in local_set(body):
        tags.append(tag)
        if tag == 1:
            ok = checksum16(packet[:i + n - 2]) == int.from_bytes(value, "big")
        elif tag == 74:
            vmti = decode_vmti_set(value)
        elif tag in ST0601:
            fields[ST0601[tag][0]] = decode_0601_value(tag, value)
    return UasPacket(fields, ok, vmti, tags)


# ------------------------------------------------------------------ ST 1201 (IMAPB)
class Imapb:
    def __init__(self, a: float, b: float, length: int):
        self.a, self.b, self.length = a, b, length
        bpow = math.ceil(math.log2(b - a))
        dpow = 8 * length - 1
        self.sF = 2.0 ** (dpow - bpow)
        self.sR = 2.0 ** (bpow - dpow)
        self.z = self.sF * a - math.floor(self.sF * a) if (a < 0 < b) else 0.0

    def decode(self, d: bytes) -> float | None:
        if d[0] & 0x80:
            return None  # valori speciale ST 1201 (infinit, NaN, rezervate)
        return self.sR * (int.from_bytes(d, "big") - self.z) + self.a

    def encode(self, x: float) -> bytes:
        return int(math.floor(self.sF * (x - self.a) + self.z)).to_bytes(self.length, "big")


# ------------------------------------------------------------------ ST 0903 (VMTI)
_LAT4, _LON4, _HAE2 = Imapb(-90, 90, 4), Imapb(-180, 180, 4), Imapb(-900, 19000, 2)
_SIG2, _RHO2 = Imapb(0, 650, 2), Imapb(-1, 1, 2)
_OFF3 = Imapb(-19.2, 19.2, 3)
_FOV2 = Imapb(0, 180, 2)


def _legacy(d: bytes, lo: float, hi: float) -> float:
    return lo + int.from_bytes(d, "big") * (hi - lo) / ((1 << (8 * len(d))) - 1)


def location_pack(d: bytes, legacy: bool = False) -> dict:
    """Pachetul de locatie ST 0903 (tag 17): 10, 16 sau 22 de octeti."""
    if len(d) not in (10, 16, 22):
        raise KlvError("pachetul de locatie are 10, 16 sau 22 de octeti")
    if legacy:
        out = {"lat": _legacy(d[0:4], -90, 90), "lon": _legacy(d[4:8], -180, 180), "hae": _legacy(d[8:10], -900, 19000)}
        if len(d) >= 16:
            out.update(sig_e=_legacy(d[10:12], 0, 650), sig_n=_legacy(d[12:14], 0, 650), sig_u=_legacy(d[14:16], 0, 650))
        if len(d) == 22:
            out.update(rho_en=_legacy(d[16:18], -1, 1), rho_eu=_legacy(d[18:20], -1, 1), rho_nu=_legacy(d[20:22], -1, 1))
        return out
    out = {"lat": _LAT4.decode(d[0:4]), "lon": _LON4.decode(d[4:8]), "hae": _HAE2.decode(d[8:10])}
    if len(d) >= 16:
        out.update(sig_e=_SIG2.decode(d[10:12]), sig_n=_SIG2.decode(d[12:14]), sig_u=_SIG2.decode(d[14:16]))
    if len(d) == 22:
        out.update(rho_en=_RHO2.decode(d[16:18]), rho_eu=_RHO2.decode(d[18:20]), rho_nu=_RHO2.decode(d[20:22]))
    return out


def encode_location_pack(lat, lon, hae, sig=None, rho=None) -> bytes:
    out = _LAT4.encode(lat) + _LON4.encode(lon) + _HAE2.encode(hae)
    if sig is not None:
        out += b"".join(_SIG2.encode(s) for s in sig)
        if rho is not None:
            out += b"".join(_RHO2.encode(r) for r in rho)
    return out


@dataclass
class VTarget:
    target_id: int
    confidence: float | None = None       # 0-100 %
    location: dict | None = None          # lat, lon, hae (+ sigma, rho)
    offset_lat: float | None = None       # fata de centrul cadrului (ST 0601 tag 23/24)
    offset_lon: float | None = None
    hae: float | None = None
    centroid_pixel: int | None = None


@dataclass
class VmtiPacket:
    version: int | None
    time: float | None
    system: str | None
    frame_width: int | None
    frame_height: int | None
    source_sensor: str | None
    targets: list
    total_targets: int | None = None


def decode_vmti_set(body: bytes) -> VmtiPacket:
    """Local set VMTI (fara cheia UL): tagurile 2-13 si seria VTarget (101)."""
    flds = local_set(body)
    vals = {t: v for t, v in flds}
    version = int.from_bytes(vals[4], "big") if 4 in vals else None
    legacy = version is not None and version < 4
    targets = []
    if 101 in vals:
        series = vals[101]
        i = 0
        while i < len(series):
            n, i = ber_length(series, i)
            pack = series[i:i + n]
            i += n
            tid, j = ber_oid(pack, 0)
            vt = VTarget(tid)
            for tag, v in local_set(pack[j:]):
                if tag == 1:
                    vt.centroid_pixel = int.from_bytes(v, "big")
                elif tag == 5:
                    vt.confidence = float(v[0]) if v else None
                elif tag == 10:
                    vt.offset_lat = _legacy(v, -19.2, 19.2) if legacy else _OFF3.decode(v)
                elif tag == 11:
                    vt.offset_lon = _legacy(v, -19.2, 19.2) if legacy else _OFF3.decode(v)
                elif tag == 12:
                    vt.hae = _legacy(v, -900, 19000) if legacy else _HAE2.decode(v)
                elif tag == 17:
                    vt.location = location_pack(v, legacy)
            targets.append(vt)
    return VmtiPacket(version, int.from_bytes(vals[2], "big") / 1e6 if 2 in vals else None,
                      vals[3].decode("utf-8", "replace") if 3 in vals else None,
                      int.from_bytes(vals[8], "big") if 8 in vals else None,
                      int.from_bytes(vals[9], "big") if 9 in vals else None,
                      vals[10].decode("utf-8", "replace") if 10 in vals else None,
                      targets, int.from_bytes(vals[5], "big") if 5 in vals else None)


def decode_0903(packet: bytes) -> VmtiPacket:
    if packet[:16] != UL_0903:
        raise KlvError("nu e un pachet ST 0903 (cheia UL)")
    n, i = ber_length(packet, 16)
    return decode_vmti_set(packet[i:i + n])


# ------------------------------------------------------------------ fluxuri
def iter_klv(buf: bytes):
    """Pachetele KLV (cheie UL de 16 octeti + lungime BER + valoare) dintr-un buffer."""
    i = 0
    while True:
        j = buf.find(UL_PREFIX, i)
        if j < 0 or j + 17 > len(buf):
            return
        try:
            n, k = ber_length(buf, j + 16)
        except (KlvError, IndexError):
            i = j + 1
            continue
        if k + n > len(buf):
            return
        yield buf[j:j + 16], buf[j:k + n]
        i = k + n


def looks_like_klv(buf) -> bool:
    return isinstance(buf, (bytes, bytearray)) and buf[:16] in (UL_0601, UL_0903)


def looks_like_mpegts(buf) -> bool:
    return (isinstance(buf, (bytes, bytearray)) and len(buf) >= 188 * 2 and buf[0] == 0x47
            and buf[188] == 0x47 and len(buf) % 188 == 0)


def klv_from_mpegts(ts: bytes) -> list[bytes]:
    """Extrage pachetele KLV din MPEG-TS (STANAG 4609): reasambleaza PES pe PID si cauta cheile UL.

    Acopera atat metadatele asincrone (PES privat 0xBD) cat si cele sincrone (stream_type 0x15,
    cu antetul de unitate de acces), fara a interpreta PAT/PMT.
    """
    pes: dict = {}
    out = []

    def flush(pid):
        data = bytes(pes.pop(pid, b""))
        if data[:3] != b"\x00\x00\x01" or len(data) < 9:
            return
        payload = data[9 + data[8]:]
        out.extend(p for _, p in iter_klv(payload) if p[:16] in (UL_0601, UL_0903))

    for k in range(0, len(ts) - 187, 188):
        p = ts[k:k + 188]
        if p[0] != 0x47:
            raise KlvError("sincronizare MPEG-TS pierduta")
        pusi = bool(p[1] & 0x40)
        pid = ((p[1] & 0x1F) << 8) | p[2]
        afc = (p[3] >> 4) & 0x3
        i = 4
        if afc in (2, 3):
            i += 1 + p[4]
        if afc in (1, 3) and i < 188:
            if pusi:
                flush(pid)
                pes[pid] = bytearray(p[i:])
            elif pid in pes:
                pes[pid] += p[i:]
    for pid in list(pes):
        flush(pid)
    return out


def encode_0601(fields: dict[int, bytes], vmti_body: bytes | None = None) -> bytes:
    """Pachet ST 0601 de test: {tag: valoare bruta}; adauga suma de control (doar teste/simulare)."""
    body = b"".join(enc_ber_oid(t) + enc_ber_length(len(v)) + v for t, v in sorted(fields.items()))
    if vmti_body is not None:
        body += enc_ber_oid(74) + enc_ber_length(len(vmti_body)) + vmti_body
    body += b"\x01\x02"
    pkt = UL_0601 + enc_ber_length(len(body) + 2) + body
    return pkt + checksum16(pkt).to_bytes(2, "big")


def encode_mapped(tag: int, x: float) -> bytes:
    name, kind, dom, rng = ST0601[tag]
    nbytes = max(1, ((dom[1] - dom[0]).bit_length() + 7) // 8)
    raw = round((x - rng[0]) * (dom[1] - dom[0]) / (rng[1] - rng[0]) + dom[0])
    return raw.to_bytes(nbytes, "big", signed=dom[0] < 0)


def encode_vmti_body(targets: list[tuple[int, bytes]], version: int = 5, time_us: int | None = None) -> bytes:
    """Local set VMTI de test: [(id tinta, pachet de locatie)]."""
    series = b""
    for tid, loc in targets:
        pack = enc_ber_oid(tid) + enc_ber_oid(17) + enc_ber_length(len(loc)) + loc
        series += enc_ber_length(len(pack)) + pack
    body = b""
    if time_us is not None:
        body += b"\x02\x08" + time_us.to_bytes(8, "big")
    body += b"\x04\x01" + bytes([version]) + b"\x05\x01" + bytes([len(targets)])
    body += enc_ber_oid(101) + enc_ber_length(len(series)) + series
    return body
