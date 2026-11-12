"""Decodor ASTERIX: CAT048 (radar), CAT062 (trasee de sistem), CAT021 (ADS-B).

Implementat dupa specificatiile publice Eurocontrol (UAP standard: CAT048 ed. 1.2x,
CAT062 ed. 1.1x, CAT021 ed. 2.x). Decodorul cunoaste lungimea TUTUROR articolelor
din UAP, ca sa poata sari peste cele pe care nu le foloseste. Alte categorii
(ex. CAT034 mesaje de serviciu, CAT240 video) sunt recunoscute si ignorate.

ATENTIE: tabelele au fost scrise dupa specificatie, nu verificate pe trafic real.
Inainte de utilizare se valideaza pe inregistrari de la sursa tinta (editia UAP
folosita de echipament, conventia de semn Doppler, referinta inaltimii, punctul
de referinta al sistemului pentru I062/100). Encoderul de aici serveste doar
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
KNOWN_CATEGORIES = {1, 2, 8, 9, 10, 11, 17, 18, 19, 20, 21, 23, 25, 30, 31, 32, 34, 48, 62, 63, 65, 150, 240, 247, 252}


class AsterixError(ValueError):
    pass


# ------------------------------------------------------------------ tabele UAP
# Un articol: (nume, tip, parametru)
#   fixed n | var | var3 (extensii de 3 octeti) | rep n | explicit | compound [subcampuri] | spare
# Subcamp compus: int (fix) | ("rep", n) | "var"
UAP048 = {
    1: ("010", "fixed", 2), 2: ("140", "fixed", 3), 3: ("020", "var", None), 4: ("040", "fixed", 4),
    5: ("070", "fixed", 2), 6: ("090", "fixed", 2), 7: ("130", "compound", [1] * 7),
    8: ("220", "fixed", 3), 9: ("240", "fixed", 6), 10: ("250", "rep", 8), 11: ("161", "fixed", 2),
    12: ("042", "fixed", 4), 13: ("200", "fixed", 4), 14: ("170", "var", None),
    15: ("210", "fixed", 4), 16: ("030", "var", None), 17: ("080", "fixed", 2), 18: ("100", "fixed", 4),
    19: ("110", "fixed", 2), 20: ("120", "compound", [2, ("rep", 6)]), 21: ("230", "fixed", 2),
    22: ("260", "fixed", 7), 23: ("055", "fixed", 1), 24: ("050", "fixed", 2), 25: ("065", "fixed", 1),
    26: ("060", "fixed", 2), 27: ("SP", "explicit", None), 28: ("RE", "explicit", None),
}

UAP062 = {
    1: ("010", "fixed", 2), 2: ("-", "spare", None), 3: ("015", "fixed", 1), 4: ("070", "fixed", 3),
    5: ("105", "fixed", 8), 6: ("100", "fixed", 6), 7: ("185", "fixed", 4),
    8: ("210", "fixed", 2), 9: ("060", "fixed", 2), 10: ("245", "fixed", 7),
    11: ("380", "compound", [3, 6, 2, 2, 2, 2, 2, 1, ("rep", 15), 2, 2, 7, 2, 2,
                             2, 2, 2, 2, 1, 8, 1, 6, 2, 1, ("rep", 8), 2, 2, 2]),
    12: ("040", "fixed", 2), 13: ("080", "var", None), 14: ("290", "compound", [1, 1, 1, 1, 2, 1, 1, 1, 1, 1]),
    15: ("200", "fixed", 1), 16: ("295", "compound", [1] * 31), 17: ("136", "fixed", 2),
    18: ("130", "fixed", 2), 19: ("135", "fixed", 2), 20: ("220", "fixed", 2),
    21: ("390", "compound", [2, 7, 4, 1, 4, 1, 4, 4, 3, 2, 2, ("rep", 4), 6, 1, 7, 7, 2, 7]),
    22: ("270", "var", None), 23: ("300", "fixed", 1), 24: ("110", "compound", [1, 4, 6, 2, 2, 1, 1]),
    25: ("120", "fixed", 2), 26: ("510", "var3", None), 27: ("500", "compound", [4, 2, 4, 1, 1, 2, 2, 1]),
    28: ("340", "compound", [2, 4, 2, 2, 2, 1]),
    29: ("-", "spare", None), 30: ("-", "spare", None), 31: ("-", "spare", None), 32: ("-", "spare", None),
    33: ("-", "spare", None), 34: ("RE", "explicit", None), 35: ("SP", "explicit", None),
}

UAP021 = {
    1: ("010", "fixed", 2), 2: ("040", "var", None), 3: ("161", "fixed", 2), 4: ("015", "fixed", 1),
    5: ("071", "fixed", 3), 6: ("130", "fixed", 6), 7: ("131", "fixed", 8),
    8: ("072", "fixed", 3), 9: ("150", "fixed", 2), 10: ("151", "fixed", 2), 11: ("080", "fixed", 3),
    12: ("073", "fixed", 3), 13: ("074", "fixed", 4), 14: ("075", "fixed", 3),
    15: ("076", "fixed", 4), 16: ("140", "fixed", 2), 17: ("090", "var", None), 18: ("210", "fixed", 1),
    19: ("070", "fixed", 2), 20: ("230", "fixed", 2), 21: ("145", "fixed", 2),
    22: ("152", "fixed", 2), 23: ("200", "fixed", 1), 24: ("155", "fixed", 2), 25: ("157", "fixed", 2),
    26: ("160", "fixed", 4), 27: ("165", "fixed", 2), 28: ("077", "fixed", 3),
    29: ("170", "fixed", 6), 30: ("020", "fixed", 1), 31: ("220", "compound", [2, 2, 2, 1]),
    32: ("146", "fixed", 2), 33: ("148", "fixed", 2), 34: ("110", "compound", ["var", ("rep", 15)]),
    35: ("016", "fixed", 1),
    36: ("008", "fixed", 1), 37: ("271", "var", None), 38: ("132", "fixed", 1), 39: ("250", "rep", 8),
    40: ("260", "fixed", 7), 41: ("400", "fixed", 1), 42: ("295", "compound", [1] * 23),
    43: ("-", "spare", None), 44: ("-", "spare", None), 45: ("-", "spare", None), 46: ("-", "spare", None),
    47: ("-", "spare", None), 48: ("RE", "explicit", None), 49: ("SP", "explicit", None),
}

# CAT001 (radar monoradar, format vechi inca folosit de radare militare): doua UAP-uri,
# alese dupa bitul TYP din I001/020 (0 = plot, 1 = traseu).
UAP001_PLOT = {
    1: ("010", "fixed", 2), 2: ("020", "var", None), 3: ("040", "fixed", 4), 4: ("070", "fixed", 2),
    5: ("090", "fixed", 2), 6: ("130", "var", None), 7: ("141", "fixed", 2),
    8: ("050", "fixed", 2), 9: ("120", "fixed", 1), 10: ("131", "fixed", 1), 11: ("080", "fixed", 2),
    12: ("100", "fixed", 4), 13: ("060", "fixed", 2), 14: ("030", "var", None),
    15: ("150", "fixed", 1), 16: ("-", "spare", None), 17: ("-", "spare", None), 18: ("-", "spare", None),
    19: ("-", "spare", None), 20: ("SP", "explicit", None), 21: ("RFS", "rfs", None),
}
UAP001_TRACK = {
    1: ("010", "fixed", 2), 2: ("020", "var", None), 3: ("161", "fixed", 2), 4: ("040", "fixed", 4),
    5: ("042", "fixed", 4), 6: ("200", "fixed", 4), 7: ("070", "fixed", 2),
    8: ("090", "fixed", 2), 9: ("141", "fixed", 2), 10: ("130", "var", None), 11: ("131", "fixed", 1),
    12: ("120", "fixed", 1), 13: ("170", "var", None), 14: ("210", "var", None),
    15: ("050", "fixed", 2), 16: ("080", "fixed", 2), 17: ("100", "fixed", 4), 18: ("060", "fixed", 2),
    19: ("030", "var", None), 20: ("SP", "explicit", None), 21: ("RFS", "rfs", None),
}
UAP002 = {
    1: ("010", "fixed", 2), 2: ("000", "fixed", 1), 3: ("020", "fixed", 1), 4: ("030", "fixed", 3),
    5: ("041", "fixed", 2), 6: ("050", "var", None), 7: ("060", "var", None),
    8: ("070", "rep", 2), 9: ("100", "fixed", 8), 10: ("090", "fixed", 2), 11: ("080", "var", None),
    12: ("-", "spare", None), 13: ("SP", "explicit", None), 14: ("RFS", "rfs", None),
}
UAP034 = {
    1: ("010", "fixed", 2), 2: ("000", "fixed", 1), 3: ("030", "fixed", 3), 4: ("020", "fixed", 1),
    5: ("041", "fixed", 2), 6: ("050", "compound", [1, 0, 0, 1, 1, 2]), 7: ("060", "compound", [1, 0, 0, 1, 1, 1]),
    8: ("070", "rep", 2), 9: ("100", "fixed", 8), 10: ("110", "fixed", 1), 11: ("120", "fixed", 8),
    12: ("090", "fixed", 2), 13: ("RE", "explicit", None), 14: ("SP", "explicit", None),
}

UAPS = {48: UAP048, 62: UAP062, 21: UAP021, 1: UAP001_PLOT, 2: UAP002, 34: UAP034}
UAP = UAP048  # compatibilitate
ITEM_FRN = {48: {v[0]: k for k, v in UAP048.items()},
            62: {v[0]: k for k, v in UAP062.items() if v[0] != "-"},
            21: {v[0]: k for k, v in UAP021.items() if v[0] != "-"}}


def _signed(v: int, bits: int) -> int:
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


def _sbytes(d: bytes) -> int:
    return _signed(int.from_bytes(d, "big"), 8 * len(d))


# ------------------------------------------------------------------ lungimi
def _read_var(buf: bytes, i: int, unit: int = 1) -> int:
    j = i
    while True:
        if j + unit > len(buf):
            raise AsterixError("articol variabil trunchiat")
        last = buf[j + unit - 1]
        j += unit
        if not last & 1:
            return j


def _sub_length(buf: bytes, i: int, spec) -> int:
    if isinstance(spec, int):
        return spec
    if spec == "var":
        return _read_var(buf, i) - i
    if isinstance(spec, tuple) and spec[0] == "rep":
        if i >= len(buf):
            raise AsterixError("subcamp repetitiv trunchiat")
        return 1 + buf[i] * spec[1]
    raise AsterixError(f"subcamp necunoscut {spec}")


def _item_length(buf: bytes, i: int, kind: str, n) -> int:
    if kind == "fixed":
        return n
    if kind == "var":
        return _read_var(buf, i) - i
    if kind == "var3":
        return _read_var(buf, i, 3) - i
    if kind == "rep":
        if i >= len(buf):
            raise AsterixError("articol repetitiv trunchiat")
        return 1 + buf[i] * n
    if kind == "explicit":
        if i >= len(buf) or buf[i] < 1:
            raise AsterixError("lungime explicita invalida")
        return buf[i]
    if kind == "compound":
        j = _read_var(buf, i)
        k = j
        for byte_idx in range(j - i):
            b = buf[i + byte_idx]
            for bit in range(7):
                if b & (0x80 >> bit):
                    idx = byte_idx * 7 + bit
                    if idx >= len(n):
                        raise AsterixError("subcamp compus in afara tabelului")
                    k += _sub_length(buf, k, n[idx])
        return k - i
    if kind == "spare":
        raise AsterixError("FRN de rezerva prezent in FSPEC")
    if kind == "rfs":
        raise AsterixError("secventierea aleatoare a campurilor (RFS) nu e suportata")
    raise AsterixError(kind)


def _compound_parts(d: bytes, specs) -> dict:
    """Subcampurile prezente ale unui articol compus: {index (de la 1): octeti}."""
    j = _read_var(d, 0)
    k = j
    out = {}
    for byte_idx in range(j):
        for bit in range(7):
            if d[byte_idx] & (0x80 >> bit):
                idx = byte_idx * 7 + bit
                ln = _sub_length(d, k, specs[idx])
                out[idx + 1] = d[k:k + ln]
                k += ln
    return out


# ------------------------------------------------------------------ interpretare
def _i048(item: str, d: bytes) -> dict:
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
        parts = _compound_parts(d, UAP048[20][2])
        if 1 in parts:
            v = struct.unpack(">H", parts[1])[0]
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
    if item == "220":
        return {"icao": d.hex().upper()}
    return {}


def _i062(item: str, d: bytes) -> dict:
    if item == "010":
        return {"sac": d[0], "sic": d[1]}
    if item == "070":
        return {"tod": int.from_bytes(d, "big") / 128.0}
    if item == "105":
        return {"lat": _sbytes(d[:4]) * 180.0 / 2 ** 25, "lon": _sbytes(d[4:]) * 180.0 / 2 ** 25}
    if item == "100":
        return {"x_m": _sbytes(d[:3]) * 0.5, "y_m": _sbytes(d[3:]) * 0.5}
    if item == "185":
        return {"vx_mps": _sbytes(d[:2]) * 0.25, "vy_mps": _sbytes(d[2:]) * 0.25}
    if item == "040":
        return {"track_number": struct.unpack(">H", d)[0]}
    if item == "080":
        out = {"track_confirmed": not (d[0] & 0x02), "src": (d[0] >> 2) & 0x07}
        if len(d) > 1:
            out["sim"] = bool(d[1] & 0x80)
            out["track_terminated"] = bool(d[1] & 0x40)
        return out
    if item == "130":
        return {"height_m": _sbytes(d) * 6.25 * FT}
    if item == "136":
        return {"fl": _sbytes(d) / 4.0, "fl_alt_m": _sbytes(d) / 4.0 * 100 * FT}
    if item == "500":
        parts = _compound_parts(d, UAP062[27][2])
        out = {}
        if 1 in parts:
            out["sigma_x_m"] = struct.unpack(">H", parts[1][:2])[0] * 0.5
            out["sigma_y_m"] = struct.unpack(">H", parts[1][2:])[0] * 0.5
        if 6 in parts:
            out["sigma_vx_mps"] = parts[6][0] * 0.25
            out["sigma_vy_mps"] = parts[6][1] * 0.25
        return out
    if item == "380":
        parts = _compound_parts(d, UAP062[11][2])
        return {"icao": parts[1].hex().upper()} if 1 in parts else {}
    if item == "060":
        return {"mode3a": f"{struct.unpack('>H', d)[0] & 0x0FFF:04o}"}
    return {}


_ICAO_CHARS = "#ABCDEFGHIJKLMNOPQRSTUVWXYZ##### ###############0123456789######"


def _callsign(d: bytes) -> str:
    v = int.from_bytes(d, "big")
    return "".join(_ICAO_CHARS[(v >> (42 - 6 * k)) & 0x3F] for k in range(8)).strip(" #")


def _i021(item: str, d: bytes) -> dict:
    if item == "010":
        return {"sac": d[0], "sic": d[1]}
    if item == "080":
        return {"icao": d.hex().upper()}
    if item == "130":
        return {"lat": _sbytes(d[:3]) * 180.0 / 2 ** 23, "lon": _sbytes(d[3:]) * 180.0 / 2 ** 23}
    if item == "131":
        return {"lat_hr": _sbytes(d[:4]) * 180.0 / 2 ** 30, "lon_hr": _sbytes(d[4:]) * 180.0 / 2 ** 30}
    if item in ("071", "073", "077"):
        return {f"tod_{item}": int.from_bytes(d, "big") / 128.0}
    if item == "140":
        return {"height_m": _sbytes(d) * 6.25 * FT}
    if item == "145":
        return {"fl": _sbytes(d) / 4.0}
    if item == "160":
        v = struct.unpack(">HH", d)
        return {"groundspeed_mps": (v[0] & 0x7FFF) * 2 ** -14 * NM, "heading_deg": v[1] * 360.0 / 65536.0}
    if item == "170":
        return {"callsign": _callsign(d)}
    if item == "040":
        return {"atp": d[0] >> 5}
    return {}


def _i001(item: str, d: bytes) -> dict:
    if item == "010":
        return {"sac": d[0], "sic": d[1]}
    if item == "020":
        det = (d[0] >> 4) & 0x03  # 01 PSR, 10 SSR, 11 PSR+SSR
        return {"is_track": bool(d[0] & 0x80), "sim": bool(d[0] & 0x40),
                "typ": {0: 0, 1: 1, 2: 2, 3: 3}[det]}
    if item == "040":
        rho, theta = struct.unpack(">HH", d)
        return {"range_m": rho / 128.0 * NM, "azimuth_deg": theta * 360.0 / 65536.0}
    if item == "042":
        x, y = struct.unpack(">hh", d)
        return {"x_m": x / 64.0 * NM, "y_m": y / 64.0 * NM}
    if item == "200":
        gs, hdg = struct.unpack(">HH", d)
        return {"groundspeed_mps": gs * 2 ** -14 * NM, "heading_deg": hdg * 360.0 / 65536.0}
    if item == "161":
        return {"track_number": struct.unpack(">H", d)[0] & 0x0FFF}
    if item == "141":
        return {"tod_trunc": struct.unpack(">H", d)[0] / 128.0}
    if item == "120":
        return {"doppler_mps": _signed(d[0], 8) * 2 ** -14 * NM, "doppler_valid": True}
    if item == "090":
        v = struct.unpack(">H", d)[0]
        fl = _signed(v & 0x3FFF, 14) / 4.0
        return {"fl": fl, "fl_alt_m": fl * 100.0 * FT, "fl_valid": not (v & 0x8000)}
    if item == "170":
        return {"track_confirmed": not (d[0] & 0x80)}
    return {}


def _i_service(item: str, d: bytes) -> dict:
    """CAT002 / CAT034: mesaje de serviciu (marcaj nord, sector, pozitia sursei)."""
    if item == "010":
        return {"sac": d[0], "sic": d[1]}
    if item == "000":
        return {"msg_type": d[0]}
    if item == "030":
        return {"tod": int.from_bytes(d, "big") / 128.0}
    if item == "041":
        return {"rotation_s": struct.unpack(">H", d)[0] / 128.0}
    if item == "120":  # I034/120: pozitia 3D a sursei
        return {"site_height_m": _sbytes(d[:2]), "site_lat": _sbytes(d[2:5]) * 180.0 / 2 ** 23,
                "site_lon": _sbytes(d[5:8]) * 180.0 / 2 ** 23}
    return {}


INTERPRETERS = {48: _i048, 62: _i062, 21: _i021, 1: _i001, 2: _i_service, 34: _i_service}


# ------------------------------------------------------------------ decodare
def iter_blocks(buf: bytes):
    """Imparte un buffer in blocuri (cat, octeti); ridica AsterixError daca nu e ASTERIX valid."""
    pos = 0
    while pos < len(buf):
        if len(buf) - pos < 3:
            raise AsterixError("antet de bloc trunchiat")
        cat = buf[pos]
        length = struct.unpack(">H", buf[pos + 1:pos + 3])[0]
        if length < 3 or pos + length > len(buf):
            raise AsterixError("lungime de bloc invalida")
        yield cat, buf[pos:pos + length]
        pos += length


def looks_like_asterix(buf: bytes) -> bool:
    """Bufferul e o succesiune exacta de blocuri ASTERIX din categorii cunoscute?"""
    if not isinstance(buf, (bytes, bytearray)) or len(buf) < 4:
        return False
    try:
        blocks = list(iter_blocks(bytes(buf)))
    except AsterixError:
        return False
    return bool(blocks) and all(cat in KNOWN_CATEGORIES for cat, _ in blocks)


def decode_block(block: bytes) -> tuple[int, list[dict]]:
    """Decodeaza un bloc; intoarce (categorie, inregistrari). Categoriile fara UAP dau []."""
    cat = block[0]
    if cat not in UAPS:
        return cat, []
    interp = INTERPRETERS[cat]
    i, end = 3, len(block)
    out = []
    while i < end:
        uap = UAPS[cat]
        fs_end = _read_var(block, i)
        fspec = block[i:fs_end]
        i = fs_end
        rec: dict = {"cat": cat, "items": []}
        if cat == 1 and fspec[0] & 0x40:
            # CAT001: UAP-ul depinde de I001/020 (FRN 2); il citim inainte
            j = i + (2 if fspec[0] & 0x80 else 0)
            uap = UAP001_TRACK if block[j] & 0x80 else UAP001_PLOT
        for byte_idx, b in enumerate(fspec):
            for bit in range(7):
                if b & (0x80 >> bit):
                    frn = byte_idx * 7 + bit + 1
                    if frn not in uap:
                        raise AsterixError(f"CAT{cat:03d}: FRN necunoscut {frn}")
                    item, kind, n = uap[frn]
                    ln = _item_length(block, i, kind, n)
                    if i + ln > end:
                        raise AsterixError(f"articol I{cat:03d}/{item} trunchiat")
                    rec["items"].append(item)
                    rec.update(interp(item, block[i:i + ln]))
                    i += ln
        out.append(rec)
    return cat, out


def decode_records(block: bytes, categories: tuple = (48,)) -> list[dict]:
    """Decodeaza blocuri concatenate; pastreaza doar categoriile cerute."""
    out = []
    for cat, blk in iter_blocks(block):
        if cat in categories:
            out.extend(decode_block(blk)[1])
    return out


# ------------------------------------------------------------------ codare (simulare/teste)
def _e048(item: str, rec: dict) -> bytes:
    if item == "010":
        return bytes([rec["sac"], rec["sic"]])
    if item == "140":
        return int(round(rec["tod"] * 128)).to_bytes(3, "big")
    if item == "020":
        return bytes([(rec.get("typ", 1) << 5) | (0x10 if rec.get("sim") else 0)])
    if item == "040":
        rho = int(round(rec["range_m"] / NM * 256))
        theta = int(round(rec["azimuth_deg"] % 360.0 * 65536 / 360.0)) % 65536
        return struct.pack(">HH", rho, theta)
    if item == "110":
        return struct.pack(">H", int(round(rec["height_m"] / FT / 25.0)) & 0x3FFF)
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
    if item == "220":
        return bytes.fromhex(rec["icao"])
    raise AsterixError(f"codare neimplementata pentru I048/{item}")


def _sb(v: float, n: int) -> bytes:
    return int(round(v)).to_bytes(n, "big", signed=True)


def _e062(item: str, rec: dict) -> bytes:
    if item == "010":
        return bytes([rec["sac"], rec["sic"]])
    if item == "070":
        return int(round(rec["tod"] * 128)).to_bytes(3, "big")
    if item == "105":
        return _sb(rec["lat"] * 2 ** 25 / 180.0, 4) + _sb(rec["lon"] * 2 ** 25 / 180.0, 4)
    if item == "100":
        return _sb(rec["x_m"] / 0.5, 3) + _sb(rec["y_m"] / 0.5, 3)
    if item == "185":
        return _sb(rec["vx_mps"] / 0.25, 2) + _sb(rec["vy_mps"] / 0.25, 2)
    if item == "040":
        return struct.pack(">H", rec["track_number"])
    if item == "080":
        return bytes([0x00 if rec.get("track_confirmed", True) else 0x02])
    if item == "130":
        return _sb(rec["height_m"] / FT / 6.25, 2)
    if item == "500":
        return (bytes([0x80 | 0x04]) + struct.pack(">HH", int(rec["sigma_x_m"] / 0.5), int(rec["sigma_y_m"] / 0.5))
                + bytes([int(rec["sigma_vx_mps"] / 0.25), int(rec["sigma_vy_mps"] / 0.25)]))
    raise AsterixError(f"codare neimplementata pentru I062/{item}")


def _e021(item: str, rec: dict) -> bytes:
    if item == "010":
        return bytes([rec["sac"], rec["sic"]])
    if item == "080":
        return bytes.fromhex(rec["icao"])
    if item == "131":
        return _sb(rec["lat_hr"] * 2 ** 30 / 180.0, 4) + _sb(rec["lon_hr"] * 2 ** 30 / 180.0, 4)
    if item == "073":
        return int(round(rec["tod_073"] * 128)).to_bytes(3, "big")
    if item == "140":
        return _sb(rec["height_m"] / FT / 6.25, 2)
    if item == "160":
        return struct.pack(">HH", int(round(rec["groundspeed_mps"] / NM * 2 ** 14)) & 0x7FFF,
                           int(round(rec["heading_deg"] % 360 * 65536 / 360.0)) % 65536)
    if item == "170":
        v = 0
        for ch in rec["callsign"].ljust(8)[:8]:
            v = (v << 6) | _ICAO_CHARS.index(ch)
        return v.to_bytes(6, "big")
    raise AsterixError(f"codare neimplementata pentru I021/{item}")


_ENC_KEYS = {
    48: {"010": "sac", "140": "tod", "020": "typ", "040": "range_m", "110": "height_m", "120": "doppler_mps",
         "161": "track_number", "042": "x_m", "200": "groundspeed_mps", "170": "track_confirmed",
         "210": "sigma_x_m", "220": "icao"},
    62: {"010": "sac", "070": "tod", "105": "lat", "100": "x_m", "185": "vx_mps", "040": "track_number",
         "080": "track_confirmed", "130": "height_m", "500": "sigma_x_m"},
    21: {"010": "sac", "080": "icao", "131": "lat_hr", "073": "tod_073", "140": "height_m",
         "160": "groundspeed_mps", "170": "callsign"},
}
_ENCODERS = {48: _e048, 62: _e062, 21: _e021}


def encode_record(rec: dict, cat: int = 48) -> bytes:
    keys = _ENC_KEYS[cat]
    present = sorted(ITEM_FRN[cat][it] for it, key in keys.items() if key in rec)
    nbytes = (max(present) + 6) // 7
    fspec = bytearray(nbytes)
    for frn in present:
        fspec[(frn - 1) // 7] |= 0x80 >> ((frn - 1) % 7)
    for k in range(nbytes - 1):
        fspec[k] |= 1
    body = b"".join(_ENCODERS[cat](UAPS[cat][frn][0], rec) for frn in present)
    return bytes(fspec) + body


def encode_block(records: list[dict], cat: int = 48) -> bytes:
    body = b"".join(encode_record(r, cat) for r in records)
    return bytes([cat]) + struct.pack(">H", len(body) + 3) + body


# ------------------------------------------------------------------ adaptor CAT048
@dataclass
class Cat048Adapter:
    """Transforma blocuri CAT048 in :class:`RadarPlot` / :class:`RadarTrackReport`.

    ``tod_ref``: ora zilei (UTC, s) care corespunde lui t = 0 in tracker.
    ``mode``: "plots" (implicit, foloseste I048/040) sau "tracks" (I048/161 + I048/042).
    Plot-urile cu raspuns SSR/Mode S (aeronave cu transponder) nu merg in tracker:
    sunt pastrate in ``cooperative_plots`` pentru inregistrarea radarului.
    """
    sites: list
    tod_ref: float = 0.0
    mode: str = "plots"
    skipped: int = 0
    drop_cooperative: bool = True
    cooperative_plots: list = field(default_factory=list, repr=False)
    _by_id: dict = field(default_factory=dict, repr=False)

    def __post_init__(self):
        for s in self.sites:
            self.add_site(s)

    def add_site(self, s: RadarSite) -> None:
        if s.sac is None or s.sic is None:
            raise ValueError(f"radarul {s.sensor_id} nu are SAC/SIC")
        self._by_id[(s.sac, s.sic)] = s

    def _t(self, tod: float) -> float:
        return tod_to_t(tod, self.tod_ref)

    def decode(self, block: bytes, received_at: float | None = None) -> list:
        return self.convert(decode_records(block), received_at)

    def convert(self, records: list[dict], received_at: float | None = None) -> list:
        out = []
        for k, rec in enumerate(records):
            site: RadarSite | None = self._by_id.get((rec.get("sac"), rec.get("sic")))
            if site is None or "tod" not in rec or rec.get("sim"):
                self.skipped += 1
                continue
            t = round(self._t(rec["tod"]), 4)
            rid = f"{site.sensor_id}:{rec['tod']:.4f}:{rec.get('track_number', k)}"
            height = rec.get("height_m")
            if height is None and rec.get("fl_valid"):
                height = rec.get("fl_alt_m")
            cooperative = rec.get("typ", 1) not in (0, 1) or "icao" in rec
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
                        cov = np.zeros((4, 4))
                        cov[:2, :2] = np.diag([sx ** 2, sy ** 2])
                        cov[2:, 2:] = J @ np.diag([sv ** 2, sh ** 2]) @ J.T
                    else:
                        cov = np.diag([sx ** 2, sy ** 2])
                if cooperative and self.drop_cooperative:
                    self.skipped += 1
                    continue
                out.append(RadarTrackReport(rid, site.sensor_id, rec["track_number"], t, rec["x_m"], rec["y_m"],
                                            vx, vy, cov, height, rec.get("track_confirmed", True),
                                            received_at=received_at))
            elif "range_m" in rec:
                rr = rec.get("doppler_mps") if rec.get("doppler_valid") else None
                plot = RadarPlot(rid, site.sensor_id, t, rec["range_m"], rec["azimuth_deg"], rr, height,
                                 received_at=received_at, icao=rec.get("icao"), cooperative=cooperative)
                if cooperative and self.drop_cooperative:
                    self.cooperative_plots.append(plot)
                else:
                    out.append(plot)
            else:
                self.skipped += 1
        return out


def tod_to_t(tod: float, tod_ref: float) -> float:
    t = tod - tod_ref
    if t < -43200:
        t += 86400.0  # trecere peste miezul noptii
    elif t > 43200:
        t -= 86400.0
    return t
