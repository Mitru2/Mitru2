"""Matricea senzor-varianta si posturi fixe.

v2: detectoarele RF nu sunt oarbe la toate dronele Geran. Variantele cu modem
4G sau mesh emit; probabilitatea de detectie RF devine parametru pe varianta.
Valorile se stabilesc cu producatorul senzorului - aici sunt doar substituenti
marcati ``None`` (necunoscut) acolo unde nu exista date.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# P_D pe (tip senzor, varianta). None = necunoscut, de stabilit cu producatorul.
SENSOR_VARIANT_PD: dict[tuple[str, str], float | None] = {
    ("rf", "geran-2/preprogramat"): 0.0,
    ("rf", "geran-2/4g"): None,
    ("rf", "geran-2/mesh"): None,
    ("rf", "fpv/radio"): None,
    ("rf", "fpv/fibra"): 0.0,
    ("rf", "orlan-10"): None,
    ("acustic", "geran-2/preprogramat"): None,
    ("acustic", "geran-3"): None,
    ("radar", "foarte-jos"): None,  # incidentul de la Luncavita: sub orizontul radar
}

EMITTING_VARIANTS = {"geran-2/4g", "geran-2/mesh", "fpv/radio", "orlan-10"}


def rf_detectable(variant: str) -> bool:
    """Varianta emite? (nu inseamna ca un senzor anume o detecteaza)."""
    return variant in EMITTING_VARIANTS


@dataclass
class FixedPost:
    """Post fix sau senzor de echipa cu acoperire si stare cunoscute."""
    post_id: str
    center: np.ndarray   # (2,) in cadrul local
    radius: float        # m
    active: bool = True
    pd: float = 0.7

    def covers(self, pos: np.ndarray) -> bool:
        return bool(np.linalg.norm(pos[:2] - self.center) <= self.radius)
