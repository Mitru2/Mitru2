"""Model comun de traseu pentru iesiri (CoT/TAK, SAPIENT, GeoJSON): identitate APP-6, sursa, calitate.

Identitatea standard (APP-6D / MIL-STD-2525D) e separata de starea de urmarire:

* traseu tentativ                      -> in asteptare (pending)
* confirmat automat (SPRT)             -> suspect   (realitatea e confirmata, intentia nu)
* potrivit cu o identitate cooperativa -> neutru    (Remote ID / ADS-B)
* decizia operatorului (jurnalizata)   -> orice identitate, are prioritate

"Ostil" nu se atribuie niciodata automat.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

# identitate -> (cifra SIDC 2525D/APP-6D, litera de afiliere CoT)
IDENTITIES = {
    "in_asteptare": ("0", "p"),
    "necunoscut": ("1", "u"),
    "presupus_prieten": ("2", "a"),
    "prieten": ("3", "f"),
    "neutru": ("4", "n"),
    "suspect": ("5", "s"),
    "ostil": ("6", "h"),
}
CONTEXT = {"realitate": "0", "exercitiu": "1", "simulare": "2"}
UAV_ENTITY = "110300"   # set 01 (aer), militar, aeronava fara pilot (conventia 'app6.sidc', de confirmat)
COT_UAV = "A-M-F-Q"      # aer, militar, aripa fixa, UAV (tip CoT derivat din 2525)


def sidc(identity: str, context: str = "realitate") -> str:
    """SIDC de 20 de cifre: versiune 10, context, identitate, set 01, stare 0, HQ 0, amplificator 00,
    entitate 110300, modificatori 0000."""
    return "10" + CONTEXT[context] + IDENTITIES[identity][0] + "01" + "0" + "0" + "00" + UAV_ENTITY + "00" + "00"


def cot_type(identity: str) -> str:
    return f"a-{IDENTITIES[identity][1]}-{COT_UAV}"


@dataclass
class CommonTrack:
    uid: str
    track_id: str
    identity: str
    sidc: str
    cot_type: str
    t: float                     # ora traseului (s, scala trackerului)
    lat: float
    lon: float
    hae: float | None
    ce: float                    # m, ~1 sigma (raza erorii circulare)
    le: float | None
    ellipse: tuple               # (semiaxa mare, semiaxa mica, unghi de la nord, grade) la 95%
    course: float
    speed: float
    status: str                  # tentativ | confirmat | sters
    score: float
    sources: list = field(default_factory=list)
    drone_class: str | None = None
    cooperative_id: str | None = None
    operator_in_loop: bool = False
    identity_author: str | None = None


def identity_of(track) -> tuple[str, str | None]:
    if getattr(track, "identity_override", None):
        return track.identity_override, getattr(track, "identity_author", None)
    if track.cooperative_id:
        auth = getattr(track, "cooperative_auth", None)
        if auth in ("Unverified", "Questionable", "Conflicting"):
            return "suspect", f"automat (Remote ID cu autentificare invalida: {auth})"
        if auth == "Trusted":
            return "neutru", "automat (Remote ID autentificat DRIP, cheie de incredere)"
        return "neutru", "automat (identitate cooperativa)"
    if track.status == "confirmat":
        return "suspect", "automat (SPRT)"
    return "in_asteptare", None


def from_track(track, frame, instance: str = "SANTINELA", context: str = "realitate") -> CommonTrack:
    s = track.state
    lat, lon = frame.to_latlon(float(s.x[0]), float(s.x[1]))
    P = s.P[:2, :2]
    vals, vecs = np.linalg.eigh(P)
    vals = np.clip(vals, 0.0, None)
    k95 = math.sqrt(5.991)
    major, minor = k95 * math.sqrt(vals[1]), k95 * math.sqrt(vals[0])
    angle = math.degrees(math.atan2(vecs[0, 1], vecs[1, 1])) % 180.0  # axa mare, de la nord spre est
    ident, author = identity_of(track)
    return CommonTrack(
        uid=f"{instance}-{track.track_id}", track_id=track.track_id, identity=ident, sidc=sidc(ident, context),
        cot_type=cot_type(ident), t=s.t, lat=lat, lon=lon,
        hae=float(track.altitude_floor) if track.altitude_floor is not None else None,
        ce=math.sqrt(vals[1]), le=None, ellipse=(major, minor, angle),
        course=math.degrees(math.atan2(s.x[2], s.x[3])) % 360.0, speed=float(math.hypot(s.x[2], s.x[3])),
        status=track.status, score=float(track.score), sources=sorted(track.sources),
        drone_class=track.drone_class, cooperative_id=track.cooperative_id,
        operator_in_loop=track.operator_in_loop, identity_author=author)
