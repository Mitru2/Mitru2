"""ADS-B direct de la receptoare 1090 MHz (SDR ieftine), fara CAT021.

Formate (de facto, documentate public de proiectele deschise dump1090/readsb):

* **Beast** binar: cadre ``1A <tip> <ceas 6 octeti> <semnal> <mesaj>``, cu 0x1A dublat in date;
  tip '2' = Mode S scurt (7 octeti), '3' = Mode S lung (14 octeti), '1' = Mode A/C;
* **AVR** text: ``*8D4840D6202CC371C32CE0576098;`` (un mesaj pe linie);
* **SBS / BaseStation** (port 30003): linii CSV ``MSG,<tip>,...``;
* **readsb / dump1090** ``aircraft.json``: ``{"now": .., "aircraft": [{"hex", "lat", "lon", ...}]}``.

Decodarea Mode S urmeaza ICAO Doc 9871 / DO-260 (DF17/18: identificare TC 1-4, pozitie in aer
TC 9-18 si 20-22 cu CPR global si local, viteza TC 19 subtipurile 1-2) si verifica CRC-24.
Aeronavele sunt tinte cooperative: nu intra in trackerul de drone, servesc la inregistrarea
radarelor (tinte de oportunitate) si la excluderea ecourilor de avioane.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

KT = 1852.0 / 3600.0
FT = 0.3048
_CHARS = "#ABCDEFGHIJKLMNOPQRSTUVWXYZ##### ###############0123456789######"
_POLY = 0xFFF409


def crc24(msg: bytes) -> int:
    """Restul CRC-24 Mode S peste tot mesajul (0 = mesaj DF17/18 valid)."""
    bits = int.from_bytes(msg, "big")
    n = len(msg) * 8
    poly = (0x1000000 | _POLY) << (n - 25)
    for i in range(n - 24):
        if bits & (1 << (n - 1 - i)):
            bits ^= poly >> i
    return bits & 0xFFFFFF


def nl(lat: float) -> int:
    """Numarul de zone de longitudine (NZ = 15)."""
    if lat == 0:
        return 59
    if abs(lat) == 87:
        return 2
    if abs(lat) > 87:
        return 1
    a = 1 - math.cos(math.pi / 30.0)
    b = math.cos(math.pi / 180.0 * abs(lat)) ** 2
    return int(math.floor(2 * math.pi / math.acos(1 - a / b)))


def cpr_global(even: tuple, odd: tuple, latest_even: bool) -> tuple | None:
    """Pozitia din doua mesaje (lat_cpr, lon_cpr) para/impara, normalizate la [0, 1)."""
    lat_e, lon_e = even
    lat_o, lon_o = odd
    j = math.floor(59 * lat_e - 60 * lat_o + 0.5)
    rlat_e = 360.0 / 60 * ((j % 60) + lat_e)
    rlat_o = 360.0 / 59 * ((j % 59) + lat_o)
    rlat_e -= 360 if rlat_e >= 270 else 0
    rlat_o -= 360 if rlat_o >= 270 else 0
    if nl(rlat_e) != nl(rlat_o):
        return None  # mesajele sunt in zone de latitudine diferite
    if latest_even:
        lat, n = rlat_e, max(nl(rlat_e), 1)
        m = math.floor(lon_e * (nl(rlat_e) - 1) - lon_o * nl(rlat_e) + 0.5)
        lon = 360.0 / n * ((m % n) + lon_e)
    else:
        lat, n = rlat_o, max(nl(rlat_o) - 1, 1)
        m = math.floor(lon_e * (nl(rlat_o) - 1) - lon_o * nl(rlat_o) + 0.5)
        lon = 360.0 / n * ((m % n) + lon_o)
    lon -= 360 if lon >= 180 else 0
    return lat, lon


def cpr_local(cpr: tuple, odd: bool, ref: tuple) -> tuple:
    """Pozitia dintr-un singur mesaj, langa o referinta (receptorul) la mai putin de ~180 NM."""
    lat_c, lon_c = cpr
    dlat = 360.0 / (59 if odd else 60)
    j = math.floor(ref[0] / dlat) + math.floor(0.5 + (ref[0] % dlat) / dlat - lat_c)
    lat = dlat * (j + lat_c)
    n = max(nl(lat) - (1 if odd else 0), 1)
    dlon = 360.0 / n
    m = math.floor(ref[1] / dlon) + math.floor(0.5 + (ref[1] % dlon) / dlon - lon_c)
    return lat, dlon * (m + lon_c)


@dataclass
class AdsbState:
    icao: str
    t: float | None = None
    lat: float | None = None
    lon: float | None = None
    alt_ft: float | None = None
    alt_gnss: bool = False
    speed_kt: float | None = None
    track_deg: float | None = None
    vrate_fpm: float | None = None
    callsign: str | None = None
    _even: tuple | None = field(default=None, repr=False)   # (lat_cpr, lon_cpr, t)
    _odd: tuple | None = field(default=None, repr=False)


class ModeSDecoder:
    """Decodor DF17/18 cu stare pe aeronava (perechi CPR, viteza, indicativ)."""

    def __init__(self, ref_latlon: tuple | None = None, pair_window: float = 10.0):
        self.ref = ref_latlon            # pozitia receptorului, pentru decodarea locala
        self.pair_window = pair_window
        self.state: dict[str, AdsbState] = {}
        self.bad_crc = 0

    def decode(self, msg: bytes, t: float) -> AdsbState | None:
        """Un mesaj Mode S de 14 octeti; intoarce starea aeronavei daca are pozitie noua."""
        if len(msg) != 14 or (msg[0] >> 3) not in (17, 18):
            return None
        if crc24(msg) != 0:
            self.bad_crc += 1
            return None
        icao = msg[1:4].hex().upper()
        me = int.from_bytes(msg[4:11], "big")
        tc = me >> 51
        st = self.state.setdefault(icao, AdsbState(icao))

        def bits(start, n):  # start = indexul bitului in ME (0 = cel mai semnificativ)
            return (me >> (56 - start - n)) & ((1 << n) - 1)

        if 1 <= tc <= 4:
            st.callsign = "".join(_CHARS[bits(8 + 6 * k, 6)] for k in range(8)).strip("# ")
            return None
        if tc == 19:
            sub = bits(5, 3)
            if sub in (1, 2):
                mult = 4 if sub == 2 else 1
                vew, vns = bits(14, 10), bits(25, 10)
                if vew and vns:
                    we = (vew - 1) * mult * (-1 if bits(13, 1) else 1)
                    sn = (vns - 1) * mult * (-1 if bits(24, 1) else 1)
                    st.speed_kt = math.hypot(we, sn)
                    st.track_deg = math.degrees(math.atan2(we, sn)) % 360.0
                vr = bits(37, 9)
                if vr:
                    st.vrate_fpm = (vr - 1) * 64 * (-1 if bits(36, 1) else 1)
            return None
        if 9 <= tc <= 18 or 20 <= tc <= 22:
            alt = bits(8, 12)
            if 9 <= tc <= 18 and alt:
                if alt & 0x010:  # bitul Q: pasi de 25 ft
                    n = ((alt & 0xFE0) >> 1) | (alt & 0x00F)
                    st.alt_ft, st.alt_gnss = n * 25 - 1000, False
            elif alt:
                st.alt_ft, st.alt_gnss = alt / FT, True  # TC 20-22: inaltime GNSS in metri
            odd = bool(bits(21, 1))
            cpr = (bits(22, 17) / 131072.0, bits(39, 17) / 131072.0)
            if odd:
                st._odd = (*cpr, t)
            else:
                st._even = (*cpr, t)
            pos = None
            if st._even and st._odd and abs(st._even[2] - st._odd[2]) <= self.pair_window:
                pos = cpr_global(st._even[:2], st._odd[:2], latest_even=not odd)
            if pos is None and self.ref is not None:
                pos = cpr_local(cpr, odd, self.ref)
            if pos is None:
                return None
            st.lat, st.lon, st.t = pos[0], pos[1], t
            return st
        return None


def iter_beast(buf: bytes):
    """(tip, mesaj) din flux Beast; '2'/'3' = Mode S scurt/lung."""
    i, n = 0, len(buf)
    lengths = {0x31: 2, 0x32: 7, 0x33: 14, 0x34: 14}
    while i < n:
        if buf[i] != 0x1A:
            i += 1
            continue
        if i + 1 >= n or buf[i + 1] not in lengths:
            i += 1
            continue
        typ = buf[i + 1]
        need = 6 + 1 + lengths[typ]
        out = bytearray()
        j = i + 2
        while len(out) < need and j < n:
            b = buf[j]
            if b == 0x1A:
                if j + 1 < n and buf[j + 1] == 0x1A:
                    j += 1
                else:
                    break  # cadru trunchiat
            out.append(b)
            j += 1
        if len(out) == need:
            yield typ, bytes(out[7:])
        i = j


def encode_beast(msg: bytes, typ: int = 0x33, ts: int = 0, signal: int = 100) -> bytes:
    body = ts.to_bytes(6, "big") + bytes([signal]) + msg
    return b"\x1a" + bytes([typ]) + body.replace(b"\x1a", b"\x1a\x1a")


def looks_like_beast(buf) -> bool:
    return isinstance(buf, (bytes, bytearray)) and len(buf) >= 11 and buf[0] == 0x1A and buf[1] in (0x31, 0x32, 0x33, 0x34)


def parse_avr(text: str):
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(("*", "@")) and line.endswith(";"):
            h = line[1:-1]
            if line.startswith("@"):
                h = h[12:]  # AVR cu marca de timp
            try:
                yield bytes.fromhex(h)
            except ValueError:
                continue


def parse_sbs(text: str, tod_ref: float = 0.0) -> list[dict]:
    """Linii SBS: pozitie (MSG,3) si viteza (MSG,4); ora din campul 'time generated'."""
    out = []
    for line in text.splitlines():
        f = line.strip().split(",")
        if len(f) < 16 or f[0] != "MSG":
            continue
        rec = {"icao": f[4].upper(), "type": f[1]}
        try:
            hh, mm, ss = f[7].split(":")
            rec["tod"] = int(hh) * 3600 + int(mm) * 60 + float(ss)
        except ValueError:
            pass
        if f[10].strip():
            rec["callsign"] = f[10].strip()
        for k, idx, conv in (("alt_ft", 11, float), ("speed_kt", 12, float), ("track_deg", 13, float),
                             ("lat", 14, float), ("lon", 15, float)):
            if len(f) > idx and f[idx].strip():
                try:
                    rec[k] = conv(f[idx])
                except ValueError:
                    pass
        out.append(rec)
    return out


def parse_aircraft_json(obj) -> list[dict]:
    """readsb/dump1090 aircraft.json -> inregistrari cu pozitie (ora = now - seen_pos)."""
    if isinstance(obj, (str, bytes)):
        obj = json.loads(obj)
    now = float(obj.get("now", 0.0))
    out = []
    for a in obj.get("aircraft", []):
        if "lat" not in a or "lon" not in a:
            continue
        alt = a.get("alt_geom", a.get("alt_baro"))
        out.append({"icao": str(a.get("hex", "")).upper().lstrip("~"), "epoch": now - float(a.get("seen_pos", 0.0)),
                    "lat": a["lat"], "lon": a["lon"], "alt_ft": alt if isinstance(alt, (int, float)) else None,
                    "speed_kt": a.get("gs"), "track_deg": a.get("track"),
                    "callsign": (a.get("flight") or "").strip() or None})
    return out
