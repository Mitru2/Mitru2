"""Remote ID pentru drone (ASTM F3411 / ASD-STAN EN 4709-002, formatul Open Drone ID).

Mesaje de 25 de octeti: 0 Basic ID, 1 Location/Vector, 2 Authentication,
3 Self-ID, 4 System, 5 Operator ID, 0xF pachet de mesaje. Dronele care transmit
Remote ID sunt cooperative: le folosim ca sa marcam traseele identificate, nu ca
tinte ostile. O drona ostila de obicei nu transmite.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

MSG = 25


@dataclass
class RemoteIdState:
    uas_id: str | None
    lat: float
    lon: float
    alt_geo: float | None
    height: float | None
    speed: float | None
    direction: float | None
    vspeed: float | None
    ts_tenths_hour: int | None  # zecimi de secunda de la inceputul orei
    h_acc: int | None


def looks_like_remoteid(buf: bytes) -> bool:
    if not isinstance(buf, (bytes, bytearray)) or len(buf) < MSG:
        return False
    if buf[0] >> 4 == 0xF:
        return len(buf) >= 3 and buf[1] == MSG and len(buf) == 3 + MSG * buf[2] and buf[2] > 0
    return len(buf) % MSG == 0 and all((buf[i] >> 4) <= 5 and (buf[i] & 0x0F) <= 3
                                       for i in range(0, len(buf), MSG))


def _messages(buf: bytes) -> list[bytes]:
    if buf[0] >> 4 == 0xF:
        n = buf[2]
        return [buf[3 + k * MSG:3 + (k + 1) * MSG] for k in range(n)]
    return [buf[i:i + MSG] for i in range(0, len(buf), MSG)]


def decode(buf: bytes) -> list[RemoteIdState]:
    """Pozitii din mesajele Location; ID-ul vine din Basic ID-ul din acelasi pachet/lot."""
    uas_id = None
    locs = []
    for m in _messages(bytes(buf)):
        mtype = m[0] >> 4
        if mtype == 0:
            uas_id = m[2:22].split(b"\x00")[0].decode("ascii", "replace").strip() or uas_id
        elif mtype == 1:
            flags = m[1]
            ew = bool(flags & 0x02)
            mult = bool(flags & 0x01)
            direction = m[2] + (180 if ew else 0)
            spd = m[3] * 0.75 + 255 * 0.25 if mult else m[3] * 0.25
            vs = struct.unpack("<b", m[4:5])[0] * 0.5
            lat = struct.unpack("<i", m[5:9])[0] * 1e-7
            lon = struct.unpack("<i", m[9:13])[0] * 1e-7
            alt_geo = struct.unpack("<H", m[15:17])[0] * 0.5 - 1000.0
            height = struct.unpack("<H", m[17:19])[0] * 0.5 - 1000.0
            ts = struct.unpack("<H", m[21:23])[0]
            locs.append(RemoteIdState(None, lat, lon, alt_geo, height,
                                      None if m[3] == 255 else spd, None if m[2] == 361 else float(direction),
                                      vs, None if ts == 0xFFFF else ts, m[19] & 0x0F))
    for loc in locs:
        loc.uas_id = uas_id
    return locs


def tod_from_tenths(ts: int, approx_tod: float) -> float:
    """Ora zilei din zecimi de secunda de la inceputul orei, aleasa langa ``approx_tod``."""
    hour = int(approx_tod // 3600)
    cands = [(h * 3600 + ts / 10.0) for h in (hour - 1, hour, hour + 1)]
    return min(cands, key=lambda c: abs(c - approx_tod)) % 86400.0


def encode_pack(uas_id: str, lat: float, lon: float, speed: float, direction: float, alt_geo: float,
                height: float, ts_tenths: int) -> bytes:
    """Pachet de test: Basic ID + Location (doar pentru simulare si teste)."""
    basic = bytes([0x02, 0x12]) + uas_id.encode().ljust(20, b"\x00") + b"\x00" * 3
    ew = direction >= 180
    d = int(direction - 180) if ew else int(direction)
    mult = speed > 63.75
    sb = int(round((speed - 63.75) / 0.75)) if mult else int(round(speed / 0.25))
    flags = (0x20 if True else 0) | (0x02 if ew else 0) | (0x01 if mult else 0)
    loc = (bytes([0x12, flags, d, sb]) + struct.pack("<b", 0) + struct.pack("<i", int(round(lat * 1e7)))
           + struct.pack("<i", int(round(lon * 1e7))) + struct.pack("<H", 0)
           + struct.pack("<H", int((alt_geo + 1000) / 0.5)) + struct.pack("<H", int((height + 1000) / 0.5))
           + bytes([0x0B, 0x00]) + struct.pack("<H", ts_tenths) + bytes([0, 0]))
    return bytes([0xF2, MSG, 2]) + basic + loc
