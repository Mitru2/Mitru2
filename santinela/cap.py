"""CAP 1.2 (OASIS Common Alerting Protocol): alerte pentru autoritatile de protectie civila.

Un traseu confirmat devine o alerta CAP cu zona afectata = elipsa de 95 % a pozitiei curente plus
elipsele conului de predictie (T+5/10/15 min), ca poligoane WGS-84 („lat,lon” separate prin spatii,
inchise, minim 4 perechi; reuniunea lor e zona). Regulile urmeaza CAP-v1.2-os (OASIS, 2010):
ora cu fus orar numeric (UTC = „-00:00”, nu „Z”), identificatorii fara spatii, virgule, < sau &.
Mesajul se valideaza pe schema oficiala CAP-v1.2.xsd (testele, cand schema e descarcata).

Decizia de a avertiza populatia si textul instructiunilor apartin autoritatii: ``status`` trebuie
ales explicit (Actual / Exercise / Test / ...), iar ``instruction`` e textul autoritatii, nu al
sistemului. Alerta implicita are ``scope=Restricted`` (pentru autoritati), nu ``Public``.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

import numpy as np

from .geo import ellipse_points
from .track import CONFIRMED
from .trackmodel import from_track

NS = "urn:oasis:names:tc:emergency:cap:1.2"
STATUSES = ("Actual", "Exercise", "System", "Test", "Draft")
MSG_TYPES = ("Alert", "Update", "Cancel", "Ack", "Error")
SCOPES = ("Public", "Restricted", "Private")
_BAD_ID = re.compile(r"[\s,<&]")


class CapError(ValueError):
    pass


def cap_time(dt: datetime) -> str:
    """DateTime CAP: fara fractiuni de secunda, cu fus orar numeric; UTC se scrie -00:00."""
    if dt.tzinfo is None:
        raise CapError("ora trebuie sa aiba fus orar")
    off = dt.utcoffset()
    if off == timedelta(0):
        return dt.strftime("%Y-%m-%dT%H:%M:%S") + "-00:00"
    m = int(off.total_seconds() // 60)
    sign = "+" if m >= 0 else "-"
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f"{sign}{abs(m) // 60:02d}:{abs(m) % 60:02d}"


def _ring(frame, mean, cov, chi2: float = 5.991) -> str:
    pts = ellipse_points(np.asarray(mean[:2]), np.asarray(cov)[:2, :2], chi2)
    pairs = [frame.to_latlon(float(p[0]), float(p[1])) for p in pts]
    pairs.append(pairs[0])
    return " ".join(f"{lat:.5f},{lon:.5f}" for lat, lon in pairs)


def _check_id(name: str, value: str) -> str:
    if not value or _BAD_ID.search(value):
        raise CapError(f"{name} nu poate contine spatii, virgule, < sau &: {value!r}")
    return value


def track_alert(track, frame, sent: datetime, sender: str, status: str, now: float | None = None, *,
                msg_type: str = "Alert", scope: str = "Restricted",
                restriction: str = "Autoritati de protectie civila si structuri de aparare",
                references: list | None = None, identifier: str | None = None, language: str = "ro-RO",
                instruction: str | None = None, expires_s: float = 900.0, context: str = "realitate") -> str:
    """Alerta CAP pentru un traseu (de obicei confirmat). ``now`` = ora trackerului pentru con."""
    if status not in STATUSES:
        raise CapError(f"status: unul din {STATUSES}")
    if msg_type not in MSG_TYPES or scope not in SCOPES:
        raise CapError("msgType sau scope invalid")
    if msg_type in ("Update", "Cancel") and not references:
        raise CapError("Update/Cancel cer references (sender,identifier,sent ale alertei anterioare)")
    _check_id("sender", sender)
    ct = from_track(track, frame, context="exercitiu" if status == "Exercise" else context)
    identifier = _check_id("identifier", identifier or f"{ct.uid}-{sent.strftime('%Y%m%dT%H%M%S')}")
    ident = ct.identity
    event = {"ostil": "Drona ostila", "suspect": "Drona suspecta"}.get(ident, "Drona neidentificata")
    severity = {"ostil": "Severe", "suspect": "Moderate"}.get(ident, "Minor")
    if track.status != CONFIRMED:
        certainty = "Possible"
    elif getattr(track, "human_only", False):
        certainty = "Likely"
    else:
        certainty = "Observed"
    response = "Shelter" if ident == "ostil" else "Monitor"
    speed_kmh = round(ct.speed * 3.6)
    headline = f"{event} ({ct.drone_class or 'clasa necunoscuta'}): curs {ct.course:.0f} grd, {speed_kmh} km/h"
    desc = (f"Traseu {ct.track_id} ({ct.status}, scor {ct.score:.1f}), {len(ct.sources)} surse. "
            f"Pozitie {ct.lat:.4f}, {ct.lon:.4f}; elipsa 95 %: {ct.ellipse[0]:.0f} x {ct.ellipse[1]:.0f} m. "
            "Zona alertei = pozitia curenta si conul de predictie la 5, 10 si 15 minute.")
    if ct.cooperative_id:
        desc += f" Identitate cooperativa: {ct.cooperative_id}."
    polys = [_ring(frame, track.state.x, track.state.P)]
    if now is not None and track.status == CONFIRMED:
        polys += [_ring(frame, x, P) for _, x, P in track.forecast(now)]
    e = escape
    x = [f'<?xml version="1.0" encoding="UTF-8"?>', f'<alert xmlns="{NS}">',
         f"  <identifier>{e(identifier)}</identifier>", f"  <sender>{e(sender)}</sender>",
         f"  <sent>{cap_time(sent)}</sent>", f"  <status>{status}</status>", f"  <msgType>{msg_type}</msgType>",
         "  <source>SANTINELA TCDE</source>", f"  <scope>{scope}</scope>"]
    if scope == "Restricted":
        x.append(f"  <restriction>{e(restriction)}</restriction>")
    if references:
        x.append(f"  <references>{e(' '.join(references))}</references>")
    x += ["  <info>", f"    <language>{language}</language>", "    <category>Security</category>",
          f"    <event>{e(event)}</event>", f"    <responseType>{response}</responseType>",
          "    <urgency>Immediate</urgency>", f"    <severity>{severity}</severity>",
          f"    <certainty>{certainty}</certainty>",
          f"    <expires>{cap_time(sent + timedelta(seconds=expires_s))}</expires>",
          "    <senderName>SANTINELA TCDE</senderName>", f"    <headline>{e(headline)}</headline>",
          f"    <description>{e(desc)}</description>"]
    if instruction:
        x.append(f"    <instruction>{e(instruction)}</instruction>")
    for name, val in (("SANTINELA_traseu", ct.track_id), ("SIDC", ct.sidc), ("identitate", ident),
                      ("curs_grd", f"{ct.course:.0f}"), ("viteza_kmh", str(speed_kmh)),
                      ("clasa", ct.drone_class or "necunoscuta")):
        x.append(f"    <parameter><valueName>{name}</valueName><value>{e(str(val))}</value></parameter>")
    x += ["    <area>", "      <areaDesc>Pozitia curenta (elipsa 95 %) si conul de predictie T+5/10/15 min</areaDesc>"]
    x += [f"      <polygon>{p}</polygon>" for p in polys]
    x += ["    </area>", "  </info>", "</alert>"]
    return "\n".join(x) + "\n"


def cancel_alert(previous_identifier: str, previous_sent: datetime, sender: str, sent: datetime,
                 status: str, reason: str = "Traseul nu mai e activ") -> str:
    """Anularea unei alerte anterioare (msgType Cancel, cu references)."""
    if status not in STATUSES:
        raise CapError(f"status: unul din {STATUSES}")
    _check_id("sender", sender)
    ident = _check_id("identifier", f"{previous_identifier}-anulare")
    ref = f"{sender},{previous_identifier},{cap_time(previous_sent)}"
    return "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>', f'<alert xmlns="{NS}">',
        f"  <identifier>{escape(ident)}</identifier>", f"  <sender>{escape(sender)}</sender>",
        f"  <sent>{cap_time(sent)}</sent>", f"  <status>{status}</status>", "  <msgType>Cancel</msgType>",
        "  <scope>Restricted</scope>", "  <restriction>Autoritati de protectie civila</restriction>",
        f"  <note>{escape(reason)}</note>", f"  <references>{escape(ref)}</references>", "</alert>"]) + "\n"


def check(xml: str) -> list[str]:
    """Regulile CAP 1.2 pe care schema nu le exprima (poligon, ora, identificatori)."""
    problems = []
    for p in re.findall(r"<polygon>(.*?)</polygon>", xml):
        pairs = p.split()
        if len(pairs) < 4 or pairs[0] != pairs[-1]:
            problems.append("poligon cu mai putin de 4 perechi sau neinchis")
        for pair in pairs:
            try:
                lat, lon = (float(v) for v in pair.split(","))
            except ValueError:
                problems.append(f"pereche invalida: {pair}")
                continue
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                problems.append(f"coordonate in afara WGS-84: {pair}")
    for tag in ("sent", "expires", "effective", "onset"):
        for v in re.findall(rf"<{tag}>(.*?)</{tag}>", xml):
            if v.endswith("Z") or v.endswith("+00:00"):
                problems.append(f"{tag}: UTC se scrie -00:00")
    for tag in ("identifier", "sender"):
        for v in re.findall(rf"<{tag}>(.*?)</{tag}>", xml):
            if _BAD_ID.search(v.replace("&amp;", "&")):
                problems.append(f"{tag} cu spatii, virgule, < sau &")
    return problems


def alerts_for(tracker, now: float, sent: datetime, sender: str, status: str, **kw) -> dict[str, str]:
    """Alerte pentru toate traseele confirmate (in afara grupurilor)."""
    return {tid: track_alert(t, tracker.frame, sent, sender, status, now, **kw)
            for tid, t in sorted(tracker.tracks.items()) if t.status == CONFIRMED and t.group_id is None}


def utc(dt: datetime | None = None) -> datetime:
    return (dt or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)


__all__ = ["track_alert", "cancel_alert", "check", "alerts_for", "cap_time", "CapError", "utc"]
