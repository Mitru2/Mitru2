"""Cursor-on-Target (CoT): intrare de la TAK/ATAK si iesire spre TAK.

Tipul CoT urmeaza MIL-STD-2525 / APP-6: ``a-<afiliere>-<dimensiune>-...``
(afiliere: f prieten, a presupus prieten, h ostil, s suspect, u necunoscut,
n neutru, p in asteptare, j/k exercitiu; dimensiune: A aer, G sol, S mare ...).

* aer ostil / suspect / necunoscut / in asteptare -> traseu pentru tracker;
* prieten / presupus prieten (orice dimensiune) -> pozitie de echipa;
* aer neutru -> aeronava cooperativa.

``ce`` (eroare circulara) se ia ca 1 sigma in metri; 9999999 inseamna necunoscut.
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np

UNKNOWN_ERR = 9999999.0


@dataclass
class CotEvent:
    uid: str
    type: str
    how: str
    time: str
    lat: float
    lon: float
    hae: float | None
    ce: float | None
    le: float | None
    course: float | None
    speed: float | None
    callsign: str | None

    @property
    def affiliation(self) -> str:
        parts = self.type.split("-")
        return parts[1] if len(parts) > 1 and parts[0] == "a" else ""

    @property
    def dimension(self) -> str:
        parts = self.type.split("-")
        return parts[2] if len(parts) > 2 and parts[0] == "a" else ""

    @property
    def role(self) -> str:
        """'tinta' | 'prieten' | 'cooperativ' | 'altul'."""
        a, d = self.affiliation, self.dimension
        if a in ("f", "a"):
            return "prieten"
        if d == "A" and a in ("h", "s", "u", "p", "j", "k"):
            return "tinta"
        if d == "A" and a == "n":
            return "cooperativ"
        return "altul"


def parse(text: str) -> list[CotEvent]:
    """Unul sau mai multe evenimente <event> (si radacini care le contin)."""
    text = text.strip()
    if text.count("<event") > 1 or not text.startswith("<event"):
        text = f"<root>{_strip_decl(text)}</root>"
    root = ET.fromstring(text)
    events = [root] if root.tag == "event" else root.iter("event")
    out = []
    for ev in events:
        pt = ev.find("point")
        if pt is None:
            continue
        det = ev.find("detail")
        trk = det.find("track") if det is not None else None
        contact = det.find("contact") if det is not None else None

        def f(el, k):
            v = el.get(k) if el is not None else None
            try:
                return float(v) if v not in (None, "") else None
            except ValueError:
                return None

        out.append(CotEvent(
            uid=ev.get("uid", ""), type=ev.get("type", ""), how=ev.get("how", ""),
            time=ev.get("time") or ev.get("start") or "",
            lat=float(pt.get("lat")), lon=float(pt.get("lon")),
            hae=f(pt, "hae"), ce=f(pt, "ce"), le=f(pt, "le"),
            course=f(trk, "course"), speed=f(trk, "speed"),
            callsign=contact.get("callsign") if contact is not None else None))
    return out


def _strip_decl(t: str) -> str:
    if t.startswith("<?xml"):
        return t[t.index("?>") + 2:]
    return t


def looks_like_cot(text: str) -> bool:
    return "<event" in text and "<point" in text and "type=" in text


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def track_to_cot(track, frame, now_utc: datetime, stale_s: float = 60.0, uid_prefix: str = "SANTINELA") -> str:
    """Traseul ca eveniment CoT pentru TAK: ostil daca e confirmat, necunoscut daca e tentativ.

    ``a-?-A-M-F-Q`` = aer, militar, aripa fixa, UAV (MIL-STD-2525).
    """
    s = track.state
    lat, lon = frame.to_latlon(float(s.x[0]), float(s.x[1]))
    aff = "h" if track.status == "confirmat" else "u"
    ce = math.sqrt(max(np.linalg.eigvalsh(s.P[:2, :2]).max(), 0.0))
    speed = math.hypot(s.x[2], s.x[3])
    course = math.degrees(math.atan2(s.x[2], s.x[3])) % 360.0
    hae = track.altitude_floor if track.altitude_floor is not None else 0.0
    t0 = _iso(now_utc)
    stale = _iso(now_utc + timedelta(seconds=stale_s))
    remarks = (f"{track.status}; surse: {len(track.sources)}; clasa: {track.drone_class or 'necunoscuta'}"
               + ("; posibil operator in bucla" if track.operator_in_loop else ""))
    ev = ET.Element("event", version="2.0", uid=f"{uid_prefix}-{track.track_id}", type=f"a-{aff}-A-M-F-Q",
                    how="m-f", time=t0, start=t0, stale=stale)
    ET.SubElement(ev, "point", lat=f"{lat:.7f}", lon=f"{lon:.7f}", hae=f"{hae:.1f}", ce=f"{ce:.1f}",
                  le=f"{UNKNOWN_ERR:.0f}")
    det = ET.SubElement(ev, "detail")
    ET.SubElement(det, "track", course=f"{course:.1f}", speed=f"{speed:.1f}")
    ET.SubElement(det, "contact", callsign=f"DRONA {track.track_id}")
    ET.SubElement(det, "remarks").text = remarks
    return ET.tostring(ev, encoding="unicode")


def utc_from_tod(tod: float, day: datetime | None = None) -> datetime:
    day = day or datetime.now(timezone.utc)
    base = day.replace(hour=0, minute=0, second=0, microsecond=0)
    return base + timedelta(seconds=tod)
