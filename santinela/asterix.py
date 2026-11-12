"""Adaptor ASTERIX categoria 048 (detectii si trasee de la radare de supraveghere).

Implementat dupa specificatia publica Eurocontrol (CAT048, UAP standard).
Decodorul parcurge toate articolele din UAP, ca sa poata sari peste cele pe care
nu le foloseste; interpreteaza:

    I048/010 SAC/SIC          I048/140 ora zilei        I048/020 descriptor
    I048/040 pozitie polara   I048/090 nivel de zbor    I048/110 inaltime 3D
    I048/120 viteza Doppler   I048/161 numar traseu     I048/042 pozitie carteziana
    I048/200 viteza polara    I048/170 stare traseu     I048/210 calitate traseu

ATENTIE: trebuie validat pe inregistrari reale de la radarul tinta inainte de
utilizare. Conventia de semn pentru Doppler (pozitiv = departare) si referinta
inaltimii se confirma cu producatorul. Encoderul de aici serveste doar
simularii si testelor.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field

import numpy as np

from .radar import RadarPlot, RadarSite, RadarTrackReport

CAT = 48
NM = 1852.0
FT = 0.3048

# FRN -> (articol, tip, lungime fixa)
UAP = {
    1: ("010", "fixed", 2), 2: ("140", "fixed", 3), 3: ("020", "var", None), 4: ("040", "fixed", 4),
    5: ("070", "fixed", 2), 6: ("090", "fixed", 2), 7: ("130", "compound130", None),
    8: ("220", "fixed", 3), 9: ("240", "fixed", 6), 10: ("250", "rep", 8), 11: ("161", "fixed", 2),
    12: ("042", "fixed", 4), 13: ("200", "fixed", 4), 14: ("170", "var", None),
    15: ("210", "fixed", 4), 16: ("030", "var", None), 17: ("080", "fixed", 2), 18: ("100", "fixed", 4),
    19: ("110", "fixed", 2), 20: ("120", "compound120", None), 21: ("230", "fixed", 2),
    22: ("260", "fixed", 7), 23: ("055", "fixed", 1), 24: ("050", "fixed", 2), 25: ("065", "fixed", 1),
    26: ("060", "fixed", 2), 27: ("SP", "explicit", None), 28: ("RE", "explicit", None),
}
ITEM_FRN = {v[0]: k for k, v in UAP.items()}


class AsterixError(ValueError):
    pass


def _signed(v: int, bits: int) -> int:
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


# ------------------------------------------------------------------ decodare
def _read_var(buf: bytes, i: int) -> int:
    j = i
    while True:
        if j >= len(buf):
            raise AsterixError("articol variabil trunchiat")
        b = buf[j]
        j += 1
        if not b & 1:
            return j


def _item_length(buf: bytes, i: int, kind: str, n: int | None) -> int:
    if kind == "fixed":
        return n
    if kind == "var":
        return _read_var(buf, i) - i
    if kind == "rep":
        return 1 + buf[i] * n
    if kind == "explicit":
        if buf[i] < 1:
            raise AsterixError("lungime explicita invalida")
        return buf[i]
    if kind == "compound130":
        j = _read_var(buf, i)
        prim = buf[i:j]
        count = sum(1 for k in range(7) if prim[0] & (0x80 >> k))
        return (j - i) + count
    if kind == "compound120":
        j = _read_var(buf, i)
        prim = buf[i]
        length = j - i
        if prim & 0x80:  # CAL
            length += 2
        if prim & 0x40:  # RDS
            length += 1 + buf[i + length] * 6
        return length
    raise AsterixError(kind)


def _interpret(item: str, d: bytes) -> dict:
    if item == "010":
        return {"sac": d[0], "sic": d[1]}
    if item == "140":
        return {"tod": int.from_bytes(d, "big") / 128.0}
    if item == "020":
        return {"typ": d[0] >> 5, "sim": bool(d[0] & 0x10), "rdp": bool(d[0] & 0x08),
                "spi": bool(d[0] & 0x04), "rab": bool(d[0] & 0x02)}
    if item == "040":
        rho, theta = struct.unpack(">HH", d)
        return {"range_m": rho / 256.0 * NM, "azimuth_deg": theta * 360.0 / 65536.0}
    if item == "090":
        v = struct.unpack(">H", d)[0]
        fl = _signed(v & 0x3FFF, 14) / 4.0
        return {"fl": fl, "fl_alt_m": fl * 100.0 * FT, "fl_valid": not (v & 0x8000)}
    if item == "110":
        v = struct.unpack(">H", d)[0]
        return {"height_m": _signed(v & 0x3FFF, 14) * 25.0 * FT}
    if item == "120":
        out = {}
        prim = d[0]
        k = 1
        if prim & 0x80:
            v = struct.unpack(">H", d[k:k + 2])[0]
            out["doppler_valid"] = not (v & 0x8000)
            out["doppler_mps"] = float(_signed(v & 0x03FF, 10))
        return out
    if item == "161":
        return {"track_number": struct.unpack(">H", d)[0] & 0x0FFF}
    if item == "042":
        x, y = struct.unpack(">hh", d)
        return {"x_m": x / 128.0 * NM, "y_m": y / 128.0 * NM}
    if item == "200":
        gs, hdg = struct.unpack(">HH", d)
        return {"groundspeed_mps": gs * 2 ** -14 * NM, "heading_deg": hdg * 360.0 / 65536.0}
    if item == "170":
        return {"track_confirmed": not (d[0] & 0x80)}
    if item == "210":
        sx, sy, sv, sh = d
        return {"sigma_x_m": sx / 128.0 * NM, "sigma_y_m": sy / 128.0 * NM,
                "sigma_v_mps": sv * 2 ** -14 * NM, "sigma_h_deg": sh * 360.0 / 4096.0}
    return {}


def decode_records(block: bytes) -> list[dict]:
    """Decodeaza unul sau mai multe blocuri de date CAT048 concatenate."""
    out = []
    pos = 0
    while pos < len(block):
        if len(block) - pos < 3:
            raise AsterixError("antet de bloc trunchiat")
        cat = block[pos]
        length = struct.unpack(">H", block[pos + 1:pos + 3])[0]
        if length < 3 or pos + length > len(block):
            raise AsterixError("lungime de bloc invalida")
        if cat != CAT:
            pos += length  # alte categorii: ignorate
            continue
        i = pos + 3
        end = pos + length
        while i < end:
            fs_end = _read_var(block, i)
            fspec = block[i:fs_end]
            i = fs_end
            rec: dict = {"items": []}
            for byte_idx, b in enumerate(fspec):
                for bit in range(7):
                    if b & (0x80 >> bit):
                        frn = byte_idx * 7 + bit + 1
                        if frn not in UAP:
                            raise AsterixError(f"FRN necunoscut {frn}")
                        item, kind, n = UAP[frn]
                        ln = _item_length(block, i, kind, n)
                        if i + ln > end:
                            raise AsterixError(f"articol I048/{item} trunchiat")
                        rec["items"].append(item)
                        rec.update(_interpret(item, block[i:i + ln]))
                        i += ln
            out.append(rec)
        pos = end
    return out


# ------------------------------------------------------------------ codare (simulare/teste)
def _encode_item(item: str, rec: dict) -> bytes:
    if item == "010":
        return bytes([rec["sac"], rec["sic"]])
    if item == "140":
        return int(round(rec["tod"] * 128)).to_bytes(3, "big")
    if item == "020":
        b = (rec.get("typ", 1) << 5) | (0x10 if rec.get("sim") else 0)
        return bytes([b])
    if item == "040":
        rho = int(round(rec["range_m"] / NM * 256))
        theta = int(round(rec["azimuth_deg"] % 360.0 * 65536 / 360.0)) % 65536
        return struct.pack(">HH", rho, theta)
    if item == "110":
        v = int(round(rec["height_m"] / FT / 25.0)) & 0x3FFF
        return struct.pack(">H", v)
    if item == "120":
        v = int(round(rec["doppler_mps"])) & 0x03FF
        if not rec.get("doppler_valid", True):
            v |= 0x8000
        return bytes([0x80]) + struct.pack(">H", v)
    if item == "161":
        return struct.pack(">H", rec["track_number"] & 0x0FFF)
    if item == "042":
        return struct.pack(">hh", int(round(rec["x_m"] / NM * 128)), int(round(rec["y_m"] / NM * 128)))
    if item == "200":
        gs = int(round(rec["groundspeed_mps"] / NM * 2 ** 14))
        hdg = int(round(rec["heading_deg"] % 360.0 * 65536 / 360.0)) % 65536
        return struct.pack(">HH", gs, hdg)
    if item == "170":
        return bytes([0x00 if rec.get("track_confirmed", True) else 0x80])
    if item == "210":
        def q(v, lsb):
            return max(0, min(255, int(round(v / lsb))))
        return bytes([q(rec["sigma_x_m"], NM / 128), q(rec["sigma_y_m"], NM / 128),
                      q(rec["sigma_v_mps"], NM * 2 ** -14), q(rec["sigma_h_deg"], 360.0 / 4096)])
    raise AsterixError(f"codare neimplementata pentru I048/{item}")


_ENC_KEYS = {"010": "sac", "140": "tod", "020": "typ", "040": "range_m", "110": "height_m",
             "120": "doppler_mps", "161": "track_number", "042": "x_m", "200": "groundspeed_mps",
             "170": "track_confirmed", "210": "sigma_x_m"}


def encode_record(rec: dict) -> bytes:
    present = sorted(ITEM_FRN[it] for it, key in _ENC_KEYS.items() if key in rec)
    nbytes = (max(present) + 6) // 7
    fspec = bytearray(nbytes)
    for frn in present:
        fspec[(frn - 1) // 7] |= 0x80 >> ((frn - 1) % 7)
    for k in range(nbytes - 1):
        fspec[k] |= 1
    body = b"".join(_encode_item(UAP[frn][0], rec) for frn in present)
    return bytes(fspec) + body


def encode_block(records: list[dict]) -> bytes:
    body = b"".join(encode_record(r) for r in records)
    return bytes([CAT]) + struct.pack(">H", len(body) + 3) + body


# ------------------------------------------------------------------ adaptor
@dataclass
class Cat048Adapter:
    """Transforma blocuri CAT048 in :class:`RadarPlot` / :class:`RadarTrackReport`.

    ``tod_ref``: ora zilei (UTC, s) care corespunde lui t = 0 in tracker.
    ``mode``: "plots" (implicit, foloseste I048/040) sau "tracks" (I048/161 + I048/042).
    """
    sites: list
    tod_ref: float = 0.0
    mode: str = "plots"
    skipped: int = 0
    _by_id: dict = field(default_factory=dict, repr=False)

    def __post_init__(self):
        for s in self.sites:
            if s.sac is None or s.sic is None:
                raise ValueError(f"radarul {s.sensor_id} nu are SAC/SIC")
            self._by_id[(s.sac, s.sic)] = s

    def _t(self, tod: float) -> float:
        t = tod - self.tod_ref
        if t < -43200:
            t += 86400.0  # trecere peste miezul noptii
        return t

    def decode(self, block: bytes, received_at: float | None = None) -> list:
        out = []
        for k, rec in enumerate(decode_records(block)):
            site: RadarSite | None = self._by_id.get((rec.get("sac"), rec.get("sic")))
            if site is None or "tod" not in rec or rec.get("sim"):
                self.skipped += 1
                continue
            t = round(self._t(rec["tod"]), 4)
            rid = f"{site.sensor_id}:{rec['tod']:.4f}:{rec.get('track_number', k)}"
            height = rec.get("height_m")
            if height is None and rec.get("fl_valid"):
                height = rec.get("fl_alt_m")
            if self.mode == "tracks" and "track_number" in rec and "x_m" in rec:
                vx = vy = None
                if "groundspeed_mps" in rec:
                    h = math.radians(rec["heading_deg"])
                    vx, vy = rec["groundspeed_mps"] * math.sin(h), rec["groundspeed_mps"] * math.cos(h)
                cov = None
                if "sigma_x_m" in rec:
                    sx, sy = max(rec["sigma_x_m"], 10.0), max(rec["sigma_y_m"], 10.0)
                    if vx is not None:
                        sv = max(rec["sigma_v_mps"], 0.5)
                        sh = math.radians(max(rec["sigma_h_deg"], 0.5))
                        gs = rec["groundspeed_mps"]
                        h = math.radians(rec["heading_deg"])
                        J = np.array([[math.sin(h), gs * math.cos(h)], [math.cos(h), -gs * math.sin(h)]])
                        Cv = J @ np.diag([sv ** 2, sh ** 2]) @ J.T
                        cov = np.zeros((4, 4))
                        cov[:2, :2] = np.diag([sx ** 2, sy ** 2])
                        cov[2:, 2:] = Cv
                    else:
                        cov = np.diag([sx ** 2, sy ** 2])
                out.append(RadarTrackReport(rid, site.sensor_id, rec["track_number"], t, rec["x_m"], rec["y_m"],
                                            vx, vy, cov, height, rec.get("track_confirmed", True),
                                            received_at=received_at))
            elif "range_m" in rec:
                rr = rec.get("doppler_mps") if rec.get("doppler_valid") else None
                out.append(RadarPlot(rid, site.sensor_id, t, rec["range_m"], rec["azimuth_deg"], rr, height,
                                     received_at=received_at))
            else:
                self.skipped += 1
        return out
