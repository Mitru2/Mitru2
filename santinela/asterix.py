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


# ------------------------------------------------------------------ structura (din asterix-specs)
# UAP-urile si structura articolelor vin din santinela/asterix_spec.py, generat din proiectul
# asterix-specs (transcrierea structurata a specificatiilor EUROCONTROL); nu mai sunt scrise de mana.
from .asterix_spec import CATEGORIES as SPEC

UAPS = {cat: v["uaps"] for cat, v in SPEC.items()}       # {cat: {caz: [nume articol pe FRN]}}
SPEC_EDITION = {cat: v["edition"] for cat, v in SPEC.items()}
REFERENCE_EDITION = {c: SPEC_EDITION[c] for c in (48, 62, 21, 34, 1, 2, 15, 16, 205, 247)}
TABLE_MAJOR = {c: int(e.split(".")[0]) for c, e in SPEC_EDITION.items()}
ITEM_FRN = {cat: {name: k for k, name in enumerate(v["uaps"].get("default", v["uaps"].get("plot")), 1)
                  if name not in ("-", "RFS")} for cat, v in SPEC.items()}
KNOWN_CATEGORIES |= {15, 16, 129, 205}


def item_spec(cat: int, name: str):
    return SPEC[cat]["items"][name][1]


def _signed(v: int, bits: int) -> int:
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


def _sbytes(d: bytes) -> int:
    return _signed(int.from_bytes(d, "big"), 8 * len(d))


def _read_var(buf: bytes, i: int, unit: int = 1) -> int:
    j = i
    while True:
        if j + unit > len(buf):
            raise AsterixError("articol variabil trunchiat")
        last = buf[j + unit - 1]
        j += unit
        if not last & 1:
            return j


def _ebits(els) -> int:
    return sum(e[1] for e in els)


def _length(buf: bytes, i: int, sp) -> int:
    """Lungimea in octeti a unui articol cu structura ``sp`` care incepe la ``i``."""
    t = sp[0]
    if t == "F":
        return _ebits(sp[1]) // 8
    if t == "X":
        j = i
        parts = sp[1]
        k = 0
        while True:
            n = (_ebits(parts[min(k, len(parts) - 1)]) + 1) // 8
            j += n
            if j > len(buf):
                raise AsterixError("articol extins trunchiat")
            if not buf[j - 1] & 1:
                return j - i
            k += 1
    if t == "R":
        nb = sp[1]
        if i + nb > len(buf):
            raise AsterixError("articol repetitiv trunchiat")
        return nb + int.from_bytes(buf[i:i + nb], "big") * (_ebits(sp[2]) // 8)
    if t == "RF":
        n = (_ebits(sp[1]) + 1) // 8
        j = i
        while True:
            j += n
            if j > len(buf):
                raise AsterixError("articol repetitiv (FX) trunchiat")
            if not buf[j - 1] & 1:
                return j - i
    if t == "EXP":
        if i >= len(buf) or buf[i] < 1:
            raise AsterixError("lungime explicita invalida")
        return buf[i]
    if t == "C":
        j = _read_var(buf, i)
        k = j
        for byte_idx in range(j - i):
            b = buf[i + byte_idx]
            for bit in range(7):
                if b & (0x80 >> bit):
                    idx = byte_idx * 7 + bit
                    if idx >= len(sp[1]) or sp[1][idx] is None:
                        raise AsterixError("subcamp compus in afara structurii")
                    k += _length(buf, k, sp[1][idx][1])
        return k - i
    raise AsterixError(t)


def _compound_parts(d: bytes, sp) -> dict:
    """Subcampurile prezente ale unui articol compus: {index (de la 1): octeti}."""
    j = _read_var(d, 0)
    k = j
    out = {}
    for byte_idx in range(j):
        for bit in range(7):
            if d[byte_idx] & (0x80 >> bit):
                idx = byte_idx * 7 + bit
                ln = _length(d, k, sp[1][idx][1])
                out[idx + 1] = d[k:k + ln]
                k += ln
    return out


_ICAO6 = "#ABCDEFGHIJKLMNOPQRSTUVWXYZ##### ###############0123456789######"


def _value(raw: int, bits: int, content):
    kind = content[0]
    if kind == "q":
        v = _signed(raw, bits) if content[1] else raw
        return v * content[2]
    if kind == "int":
        return _signed(raw, bits) if content[1] else raw
    if kind == "str":
        if content[1] == "StringICAO":
            return "".join(_ICAO6[(raw >> (bits - 6 * (k + 1))) & 0x3F] for k in range(bits // 6)).strip(" #")
        if content[1] == "StringOctal":
            return "".join(str((raw >> (bits - 3 * (k + 1))) & 7) for k in range(bits // 3))
        return raw.to_bytes(bits // 8, "big").decode("latin-1").strip()
    return raw


def _fields(d: bytes, els, total_bits: int | None = None) -> dict:
    total = total_bits if total_bits is not None else len(d) * 8
    v = int.from_bytes(d, "big")
    out = {}
    pos = 0
    for name, bits, content in els:
        raw = (v >> (total - pos - bits)) & ((1 << bits) - 1)
        pos += bits
        if content[0] != "spare":
            out[name or "val"] = _value(raw, bits, content)
    return out


def decode_item(sp, d: bytes):
    """Decodare generica, dupa structura din asterix-specs: valori scalate in unitatile specificatiei."""
    t = sp[0]
    if t == "F":
        f = _fields(d, sp[1])
        return f["val"] if list(f) == ["val"] else f
    if t == "X":
        out, j = {}, 0
        for k in range(len(d)):
            part = sp[1][min(k, len(sp[1]) - 1)]
            n = (_ebits(part) + 1) // 8
            if j >= len(d):
                break
            out.update(_fields(d[j:j + n], part, n * 8))
            last = d[j + n - 1]
            j += n
            if not last & 1:
                break
        return out
    if t == "R":
        nb, els = sp[1], sp[2]
        n = int.from_bytes(d[:nb], "big")
        size = _ebits(els) // 8
        items = [_fields(d[nb + k * size:nb + (k + 1) * size], els) for k in range(n)]
        return [x["val"] if list(x) == ["val"] else x for x in items]
    if t == "RF":
        size = (_ebits(sp[1]) + 1) // 8
        return [_fields(d[k:k + size], sp[1], size * 8) for k in range(0, len(d), size)]
    if t == "EXP":
        return bytes(d[1:])
    if t == "C":
        return {sp[1][idx - 1][0]: decode_item(sp[1][idx - 1][1], part) for idx, part in _compound_parts(d, sp).items()}
    raise AsterixError(t)


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
        parts = _compound_parts(d, item_spec(48, "120"))
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
        parts = _compound_parts(d, item_spec(62, "500"))
        out = {}
        if 1 in parts:
            out["sigma_x_m"] = struct.unpack(">H", parts[1][:2])[0] * 0.5
            out["sigma_y_m"] = struct.unpack(">H", parts[1][2:])[0] * 0.5
        if 6 in parts:
            out["sigma_vx_mps"] = parts[6][0] * 0.25
            out["sigma_vy_mps"] = parts[6][1] * 0.25
        return out
    if item == "380":
        parts = _compound_parts(d, item_spec(62, "380"))
        return {"icao": parts[1].hex().upper()} if 1 in parts else {}
    if item == "060":
        return {"mode3a": f"{struct.unpack('>H', d)[0] & 0x0FFF:04o}"}
    return {}


_ICAO_CHARS = _ICAO6


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
        return {"doppler_mps": _signed(d[0], 8) * 2 ** -8 * NM, "doppler_valid": True}  # LSB 2^-8 NM/s
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


def _i247(item: str, d: bytes) -> dict:
    if item == "010":
        return {"sac": d[0], "sic": d[1]}
    if item == "140":
        return {"tod": int.from_bytes(d, "big") / 128.0}
    if item == "550":
        n = d[0]
        return {"versions": {d[1 + 3 * k]: f"{d[2 + 3 * k]}.{d[3 + 3 * k]}" for k in range(n)}}
    return {}


INTERPRETERS = {48: _i048, 62: _i062, 21: _i021, 1: _i001, 2: _i_service, 34: _i_service, 247: _i247}


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


def decode_block(block: bytes, full: bool = False) -> tuple[int, list[dict]]:
    """Decodeaza un bloc; intoarce (categorie, inregistrari). Categoriile fara structura dau [].

    Cu ``full=True`` fiecare inregistrare primeste si ``fields``: toate articolele decodate generic.
    """
    cat = block[0]
    if cat not in SPEC:
        return cat, []
    interp = INTERPRETERS.get(cat)
    uaps = UAPS[cat]
    i, end = 3, len(block)
    out = []
    while i < end:
        uap = uaps.get("default")
        fs_end = _read_var(block, i)
        fspec = block[i:fs_end]
        i = fs_end
        rec: dict = {"cat": cat, "items": []}
        if full:
            rec["fields"] = {}
        if uap is None:
            # CAT001: UAP-ul se alege dupa I001/020 TYP (primul bit), FRN 2; il citim inainte
            j = i + (2 if fspec[0] & 0x80 else 0)
            if not fspec[0] & 0x40 or j >= end:
                raise AsterixError(f"CAT{cat:03d}: lipseste articolul care alege UAP-ul")
            uap = uaps["track" if block[j] & 0x80 else "plot"]
        for byte_idx, b in enumerate(fspec):
            for bit in range(7):
                if b & (0x80 >> bit):
                    frn = byte_idx * 7 + bit + 1
                    if frn > len(uap):
                        raise AsterixError(f"CAT{cat:03d}: FRN necunoscut {frn}")
                    item = uap[frn - 1]
                    if item == "-":
                        raise AsterixError(f"CAT{cat:03d}: FRN de rezerva {frn} prezent in FSPEC")
                    if item == "RFS":
                        raise AsterixError("secventierea aleatoare a campurilor (RFS) nu e suportata")
                    sp = item_spec(cat, item)
                    ln = _length(block, i, sp)
                    if i + ln > end:
                        raise AsterixError(f"articol I{cat:03d}/{item} trunchiat")
                    data = block[i:i + ln]
                    rec["items"].append(item)
                    if interp is not None:
                        rec.update(interp(item, data))
                    if full:
                        rec["fields"][item] = decode_item(sp, data)
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


# ------------------------------------------------------------------ codare generica (dupa asterix-specs)
def _pack_fields(values, els, total_bits: int) -> int:
    if not isinstance(values, dict):
        values = {"val": values}
    v, pos = 0, 0
    for name, bits, content in els:
        x = values.get(name or "val", 0) if content[0] != "spare" else 0
        if content[0] == "q":
            raw = int(round(x / content[2]))
        elif content[0] == "str":
            if content[1] == "StringICAO":
                raw = 0
                for ch in str(x).ljust(bits // 6)[:bits // 6]:
                    raw = (raw << 6) | _ICAO6.index(ch)
            elif content[1] == "StringOctal":
                raw = int(str(x), 8)
            else:
                raw = int.from_bytes(str(x).encode("latin-1").ljust(bits // 8)[:bits // 8], "big")
        else:
            raw = int(x)
        raw &= (1 << bits) - 1
        v |= raw << (total_bits - pos - bits)
        pos += bits
    return v


def encode_item(sp, value) -> bytes:
    """Inversul lui decode_item (pentru simulare si teste)."""
    t = sp[0]
    if t == "F":
        n = _ebits(sp[1])
        return _pack_fields(value, sp[1], n).to_bytes(n // 8, "big")
    if t == "X":
        parts = sp[1]
        names = [{e[0] for e in part} for part in parts]
        last = max([k for k, nm in enumerate(names) if any(f in value for f in nm)] or [0])
        out = b""
        for k in range(last + 1):
            n = _ebits(parts[k]) + 1
            v = (_pack_fields(value, parts[k], n) | (1 if k < last else 0))
            out += v.to_bytes(n // 8, "big")
        return out
    if t == "R":
        size = _ebits(sp[2])
        return len(value).to_bytes(sp[1], "big") + b"".join(
            _pack_fields(x, sp[2], size).to_bytes(size // 8, "big") for x in value)
    if t == "RF":
        n = _ebits(sp[1]) + 1
        return b"".join((_pack_fields(x, sp[1], n) | (1 if k < len(value) - 1 else 0)).to_bytes(n // 8, "big")
                        for k, x in enumerate(value))
    if t == "EXP":
        return bytes([len(value) + 1]) + bytes(value)
    if t == "C":
        idx = {sub[0]: k for k, sub in enumerate(sp[1]) if sub is not None}
        present = sorted(idx[name] for name in value)
        nb = present[-1] // 7 + 1
        prim = bytearray(nb)
        for k in present:
            prim[k // 7] |= 0x80 >> (k % 7)
        for q in range(nb - 1):
            prim[q] |= 1
        return bytes(prim) + b"".join(encode_item(sp[1][k][1], value[sp[1][k][0]]) for k in present)
    raise AsterixError(t)


def encode_generic(cat: int, records: list[dict], case: str = "default") -> bytes:
    """Bloc ASTERIX din inregistrari {articol: valoare in unitatile specificatiei}."""
    uap = UAPS[cat][case]
    body = b""
    for rec in records:
        frns = sorted(uap.index(it) + 1 for it in rec)
        nbytes = (max(frns) + 6) // 7
        fspec = bytearray(nbytes)
        for frn in frns:
            fspec[(frn - 1) // 7] |= 0x80 >> ((frn - 1) % 7)
        for k in range(nbytes - 1):
            fspec[k] |= 1
        body += bytes(fspec) + b"".join(encode_item(item_spec(cat, uap[f - 1]), rec[uap[f - 1]]) for f in frns)
    return bytes([cat]) + struct.pack(">H", len(body) + 3) + body


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
    uap = UAPS[cat].get("default")
    body = b"".join(_ENCODERS[cat](uap[frn - 1], rec) for frn in present)
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
