"""NFFI - NATO Friendly Force Information (STANAG 5527): pozitiile fortelor proprii.

Cititor tolerant: cauta in documentul XML fiecare element care contine, direct sau
mai adanc, ``latitude`` si ``longitude`` (indiferent de spatiul de nume) si citeste
de langa ele ora (``dateTimeGroup``/``dateTime``) si identitatea (``systemId``,
``unitName``, ``trackId``, ``symbolCode``). Verificati pe schema versiunii NFFI
folosite (1.3 / 2.x) inainte de utilizare.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class FriendlyPosition:
    unit_id: str
    tod: float | None
    lat: float
    lon: float
    alt: float | None = None
    name: str | None = None
    symbol: str | None = None
    source: str = "NFFI"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


_DTG = re.compile(r"^(\d{2})(\d{2})(\d{2})Z([A-Z]{3})(\d{2})$")
_MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def parse_dtg(v: str) -> float | None:
    """Ora zilei din ISO-8601 sau din grupul data-ora militar (ex. 121000ZNOV26)."""
    v = (v or "").strip()
    m = _DTG.match(v.upper())
    if m:
        return int(m.group(2)) * 3600 + int(m.group(3)) * 60
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.hour * 3600 + dt.minute * 60 + dt.second + dt.microsecond * 1e-6


def variant(text: str) -> str:
    """'nffi-1.3' | 'nffi-1.4' | 'nffi' (XML, versiune nedeclarata) | 'ffi-mtf' (APP-11, text) | ''."""
    t = text.lstrip()
    if re.match(r"^(EXER|OPER)/", t) or re.search(r"^MSGID/FFI", t, re.M):
        return "ffi-mtf"
    m = re.search(r'(?:nffi[:/]|version=")(\d\.\d)', t, re.I)
    if m and m.group(1) in ("1.3", "1.4"):
        return f"nffi-{m.group(1)}"
    return "nffi" if looks_like_nffi(t) else ""


def looks_like_nffi(text: str) -> bool:
    return bool(re.search(r"<(\w+:)?(NFFI|FFI|FriendlyForce)", text)) and "atitude" in text


def parse(text: str, source: str = "NFFI") -> list[FriendlyPosition]:
    root = ET.fromstring(text.strip())
    out = []
    seen = set()
    for el in root.iter():
        names = {_local(c.tag): c for c in el.iter()}
        if "latitude" not in names or "longitude" not in names:
            continue
        # cel mai mic element care contine o singura pozitie
        lats = [c for c in el.iter() if _local(c.tag) == "latitude"]
        if len(lats) != 1 or id(lats[0]) in seen:
            continue
        # urcam la nivelul care contine si identitatea, daca exista
        seen.add(id(lats[0]))
        get = lambda *ks: next((names[k].text for k in ks if k in names and names[k].text), None)  # noqa: E731
        try:
            lat, lon = float(get("latitude")), float(get("longitude"))
        except (TypeError, ValueError):
            continue
        alt = get("altitude", "height")
        out.append(FriendlyPosition(
            unit_id=get("systemId", "trackId", "unitId", "unitName") or f"{source}-{len(out)}",
            tod=parse_dtg(get("dateTimeGroup", "dateTime", "timeOfPosition") or ""),
            lat=lat, lon=lon, alt=float(alt) if alt else None,
            name=get("unitName"), symbol=get("symbolCode", "symbol"), source=source))
    # pastram doar cea mai completa intrare pe unitate (elementele parinte repeta pozitia)
    best = {}
    for p in out:
        cur = best.get(p.unit_id)
        if cur is None or (p.name and not cur.name) or (p.symbol and not cur.symbol):
            best[p.unit_id] = p
    return list(best.values())
