"""Cititor minimal pentru capturi pcap si pcapng: datagramele UDP (si segmentele TCP) cu sarcina utila.

Legaturi suportate: Ethernet (1, cu VLAN 802.1Q), IP brut (12, 14, 101), Linux SLL (113),
Linux SLL2 (276), BSD loopback (0). IPv4 si IPv6; fragmentele IP nu sunt reasamblate.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path


class PcapError(ValueError):
    pass


@dataclass
class Datagram:
    t: float                 # secunde de la epoca Unix
    proto: str               # "udp" | "tcp"
    src: str
    dst: str
    sport: int
    dport: int
    payload: bytes


def _ip(data: bytes, t: float) -> Datagram | None:
    if not data:
        return None
    v = data[0] >> 4
    if v == 4:
        ihl = (data[0] & 0x0F) * 4
        proto = data[9]
        frag = struct.unpack(">H", data[6:8])[0] & 0x3FFF
        if frag & 0x1FFF:
            return None  # fragment ulterior
        total = struct.unpack(">H", data[2:4])[0]
        src = ".".join(map(str, data[12:16]))
        dst = ".".join(map(str, data[16:20]))
        body = data[ihl:total if total else None]
    elif v == 6:
        proto = data[6]
        src = data[8:24].hex()
        dst = data[24:40].hex()
        body = data[40:40 + struct.unpack(">H", data[4:6])[0]]
    else:
        return None
    if proto == 17 and len(body) >= 8:
        sp, dp, ln = struct.unpack(">HHH", body[:6])
        return Datagram(t, "udp", src, dst, sp, dp, body[8:ln if ln >= 8 else None])
    if proto == 6 and len(body) >= 20:
        sp, dp = struct.unpack(">HH", body[:4])
        off = (body[12] >> 4) * 4
        payload = body[off:]
        return Datagram(t, "tcp", src, dst, sp, dp, payload) if payload else None
    return None


def _link(linktype: int, frame: bytes, t: float) -> Datagram | None:
    if linktype == 1:  # Ethernet
        et = struct.unpack(">H", frame[12:14])[0]
        off = 14
        while et in (0x8100, 0x88A8):  # VLAN
            et = struct.unpack(">H", frame[off + 2:off + 4])[0]
            off += 4
        return _ip(frame[off:], t) if et in (0x0800, 0x86DD) else None
    if linktype in (12, 14, 101):
        return _ip(frame, t)
    if linktype == 113:  # Linux cooked
        return _ip(frame[16:], t)
    if linktype == 276:  # Linux cooked v2
        return _ip(frame[20:], t)
    if linktype == 0:  # BSD loopback
        return _ip(frame[4:], t)
    return None


def read(path_or_bytes) -> list[Datagram]:
    data = Path(path_or_bytes).read_bytes() if not isinstance(path_or_bytes, (bytes, bytearray)) else bytes(path_or_bytes)
    if len(data) < 24:
        raise PcapError("fisier prea scurt")
    magic = data[:4]
    if magic == b"\x0a\x0d\x0d\x0a":
        return _pcapng(data)
    table = {b"\xd4\xc3\xb2\xa1": ("<", 1e-6), b"\xa1\xb2\xc3\xd4": (">", 1e-6),
             b"\x4d\x3c\xb2\xa1": ("<", 1e-9), b"\xa1\xb2\x3c\x4d": (">", 1e-9)}
    if magic not in table:
        raise PcapError("nu e pcap/pcapng")
    e, frac = table[magic]
    linktype = struct.unpack(e + "I", data[20:24])[0] & 0x0FFFFFFF
    out, i = [], 24
    while i + 16 <= len(data):
        sec, sub, incl, _orig = struct.unpack(e + "IIII", data[i:i + 16])
        frame = data[i + 16:i + 16 + incl]
        i += 16 + incl
        d = _link(linktype, frame, sec + sub * frac)
        if d is not None:
            out.append(d)
    return out


def _pcapng(data: bytes) -> list[Datagram]:
    out, i = [], 0
    e = "<"
    ifaces = []  # (linktype, rezolutie)
    while i + 12 <= len(data):
        btype = struct.unpack(e + "I", data[i:i + 4])[0]
        if btype == 0x0A0D0D0A:  # Section Header: ordinea octetilor
            e = "<" if data[i + 8:i + 12] == b"\x4d\x3c\x2b\x1a" else ">"
            ifaces = []
        blen = struct.unpack(e + "I", data[i + 4:i + 8])[0]
        if blen < 12 or i + blen > len(data):
            raise PcapError("bloc pcapng invalid")
        body = data[i + 8:i + blen - 4]
        if btype == 1:  # Interface Description
            linktype = struct.unpack(e + "H", body[0:2])[0]
            res = 1e-6
            j = 8
            while j + 4 <= len(body):
                code, ln = struct.unpack(e + "HH", body[j:j + 4])
                if code == 0:
                    break
                if code == 9 and ln >= 1:  # if_tsresol
                    r = body[j + 4]
                    res = 2.0 ** -(r & 0x7F) if r & 0x80 else 10.0 ** -r
                j += 4 + ((ln + 3) // 4) * 4
            ifaces.append((linktype, res))
        elif btype == 6:  # Enhanced Packet
            iface, hi, lo, cap, _orig = struct.unpack(e + "IIIII", body[:20])
            linktype, res = ifaces[iface] if iface < len(ifaces) else (1, 1e-6)
            d = _link(linktype, body[20:20 + cap], ((hi << 32) | lo) * res)
            if d is not None:
                out.append(d)
        elif btype == 3:  # Simple Packet
            linktype, res = ifaces[0] if ifaces else (1, 1e-6)
            d = _link(linktype, body[4:], 0.0)
            if d is not None:
                out.append(d)
        i += blen
    return out


def looks_like_pcap(data: bytes) -> bool:
    return isinstance(data, (bytes, bytearray)) and data[:4] in (
        b"\xd4\xc3\xb2\xa1", b"\xa1\xb2\xc3\xd4", b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d", b"\x0a\x0d\x0d\x0a")


# ------------------------------------------------------------------ capturi publice de test
def fetch_asterix_captures(directory, timeout: float = 30.0) -> Path:
    """Descarca capturile ASTERIX reale si iesirile de referinta (Croatia Control, GPL-2: nu se
    redistribuie) la commitul fixat si le verifica amprentele."""
    import hashlib
    import json
    import urllib.request
    man = json.loads((Path(__file__).parent / "data" / "asterix_test_captures.json").read_text(encoding="utf-8"))
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    for name, info in man["files"].items():
        data = urllib.request.urlopen(man["source"] + man["commit"] + "/" + info["path"], timeout=timeout).read()
        if hashlib.sha256(data).hexdigest() != info["sha256"]:
            raise PcapError(f"amprenta diferita: {name}")
        (d / name).write_bytes(data)
    return d


def fetch_reference(directory, timeout: float = 30.0) -> Path:
    """Descarca datele de referinta din santinela/data/reference_data.json (schema CAP 1.2,
    capturile DIS Wireshark) si le verifica amprentele. Nu se redistribuie."""
    import hashlib
    import json
    import urllib.request
    man = json.loads((Path(__file__).parent / "data" / "reference_data.json").read_text(encoding="utf-8"))
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    for name, info in man["files"].items():
        data = urllib.request.urlopen(info["url"], timeout=timeout).read()
        if hashlib.sha256(data).hexdigest() != info["sha256"]:
            raise PcapError(f"amprenta diferita: {name}")
        (d / name).write_bytes(data)
    return d


def build_udp_pcap(payloads: list[bytes], port: int = 8600, nanosecond: bool = False) -> bytes:
    """Captura pcap Ethernet/IPv4/UDP (pentru teste si simulare)."""
    out = (b"\x4d\x3c\xb2\xa1" if nanosecond else b"\xd4\xc3\xb2\xa1")[::-1][::-1]
    out += struct.pack("<HHiIII", 2, 4, 0, 0, 65535, 1)
    for k, p in enumerate(payloads):
        udp = struct.pack(">HHHH", 40000, port, 8 + len(p), 0) + p
        ip = struct.pack(">BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), k, 0, 64, 17, 0,
                         bytes([10, 0, 0, 1]), bytes([239, 1, 1, 1])) + udp
        eth = b"\x01\x00\x5e\x01\x01\x01" + b"\x02\x00\x00\x00\x00\x01" + b"\x08\x00" + ip
        out += struct.pack("<IIII", 1794477600 + k, 500, len(eth), len(eth)) + eth
    return out
