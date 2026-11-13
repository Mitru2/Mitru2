"""Remote ID pentru drone (ASTM F3411 / ASD-STAN EN 4709-002, formatul Open Drone ID).

Mesaje de 25 de octeti: 0 Basic ID, 1 Location/Vector, 2 Authentication,
3 Self-ID, 4 System (pozitia operatorului), 5 Operator ID, 0xF pachet de mesaje.
Dronele care transmit Remote ID sunt cooperative: le folosim ca sa marcam traseele
identificate, nu ca tinte ostile. O drona ostila de obicei nu transmite.

Purtatori radio (dupa ASTM F3411 / biblioteca opendroneid, Apache-2.0):
* Bluetooth 4 legacy si Bluetooth 5 Long Range: structura AD "Service Data" (0x16),
  UUID 0xFFFA, cod de aplicatie 0x0D, contor, apoi un mesaj sau un pachet de mesaje;
* Wi-Fi Beacon: element "vendor specific" (0xDD), OUI FA:0B:BC, tip 0x0D, contor, pachet;
* Wi-Fi NAN: atributul Service Descriptor cu Service ID 88 69 19 9D 92 09
  (primii 6 octeti din SHA-256("org.opendroneid.remoteid")), apoi contor si pachet.
Autentificarea (mesajul 2) e semnalata, dar NU e verificata criptografic.
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
    operator_lat: float | None = None
    operator_lon: float | None = None
    operator_alt: float | None = None
    operator_id: str | None = None
    self_id: str | None = None
    auth_present: bool = False


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
    extra: dict = {}
    for m in _messages(bytes(buf)):
        mtype = m[0] >> 4
        if mtype == 0:
            uas_id = m[2:22].split(b"\x00")[0].decode("ascii", "replace").strip() or uas_id
        elif mtype == 2:
            extra["auth_present"] = True
        elif mtype == 3:
            extra["self_id"] = m[2:25].split(b"\x00")[0].decode("ascii", "replace").strip() or None
        elif mtype == 4:
            olat = struct.unpack("<i", m[2:6])[0] * 1e-7
            olon = struct.unpack("<i", m[6:10])[0] * 1e-7
            if olat or olon:
                extra["operator_lat"], extra["operator_lon"] = olat, olon
                alt = struct.unpack("<H", m[18:20])[0]
                extra["operator_alt"] = alt * 0.5 - 1000.0 if alt else None
        elif mtype == 5:
            extra["operator_id"] = m[2:22].split(b"\x00")[0].decode("ascii", "replace").strip() or None
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
        for k, v in extra.items():
            setattr(loc, k, v)
    return locs


NAN_SERVICE_ID = bytes([0x88, 0x69, 0x19, 0x9D, 0x92, 0x09])
_BT_PREFIX = bytes([0x16, 0xFA, 0xFF, 0x0D])
_WIFI_PREFIX = bytes([0xFA, 0x0B, 0xBC, 0x0D])


def _odid_payload(b: bytes) -> bytes | None:
    """Un mesaj (25 o.) sau un pachet (0xF.) la inceputul lui b; None daca nu e valid."""
    if len(b) >= 3 and b[0] >> 4 == 0xF and b[1] == MSG and 0 < b[2] <= 9 and len(b) >= 3 + MSG * b[2]:
        return b[:3 + MSG * b[2]]
    if len(b) >= MSG and (b[0] >> 4) <= 5:
        return b[:MSG]
    return None


def extract(frame: bytes) -> list[tuple[str, bytes]]:
    """Mesajele Open Drone ID dintr-un cadru radio: [(purtator, mesaj sau pachet), ...]."""
    out = []
    frame = bytes(frame)
    i = 0
    while True:  # Bluetooth: structura AD [len][0x16][FA FF][0D][contor][date]
        j = frame.find(_BT_PREFIX, i)
        if j < 1:
            break
        ad_len = frame[j - 1]
        payload = _odid_payload(frame[j + 5:j - 1 + 1 + ad_len])
        if payload:
            out.append(("bluetooth", payload))
        i = j + 1
    i = 0
    while True:  # Wi-Fi Beacon: [DD][len][FA 0B BC][0D][contor][pachet]
        j = frame.find(_WIFI_PREFIX, i)
        if j < 2:
            break
        if frame[j - 2] == 0xDD:
            payload = _odid_payload(frame[j + 5:j + frame[j - 1]])
            if payload:
                out.append(("wifi-beacon", payload))
        i = j + 1
    j = frame.find(NAN_SERVICE_ID)
    if j >= 0:  # NAN: [service id 6][instance][requestor][control][len info][contor][pachet]
        info_len = frame[j + 9] if len(frame) > j + 9 else 0
        payload = _odid_payload(frame[j + 11:j + 10 + info_len])
        if payload:
            out.append(("wifi-nan", payload))
    return out


def looks_like_radio_frame(buf) -> bool:
    return isinstance(buf, (bytes, bytearray)) and bool(extract(bytes(buf)))


def encode_system(operator_lat: float, operator_lon: float, operator_alt: float = 0.0) -> bytes:
    """Mesaj System de test (pozitia operatorului)."""
    m = (bytes([0x42, 0x01]) + struct.pack("<i", int(round(operator_lat * 1e7)))
         + struct.pack("<i", int(round(operator_lon * 1e7))) + struct.pack("<H", 1) + bytes([0])
         + struct.pack("<H", 0) + struct.pack("<H", 0) + bytes([0]) + struct.pack("<H", int((operator_alt + 1000) / 0.5))
         + struct.pack("<I", 0) + bytes([0]))
    return m[:MSG].ljust(MSG, b"\x00")


def wrap_bluetooth(payload: bytes, counter: int = 1) -> bytes:
    data = _BT_PREFIX + bytes([counter]) + payload
    return bytes([0x02, 0x01, 0x06]) + bytes([len(data)]) + data   # flags + AD Service Data


def wrap_wifi_beacon(payload: bytes, counter: int = 1) -> bytes:
    body = _WIFI_PREFIX + bytes([counter]) + payload
    return bytes(36) + bytes([0xDD, len(body)]) + body                # antet 802.11 simplificat + IE


def wrap_nan(payload: bytes, counter: int = 1) -> bytes:
    info = bytes([counter]) + payload
    return bytes(24) + bytes([0x03, 0, 0]) + NAN_SERVICE_ID + bytes([1, 0, 0x10, len(info)]) + info


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


def pack(*messages: bytes) -> bytes:
    return bytes([0xF2, MSG, len(messages)]) + b"".join(messages)
