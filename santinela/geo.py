"""Proiectie locala (est, nord) in metri si utilitare de elipse."""
from __future__ import annotations

import math
import numpy as np

EARTH_R = 6371008.8

# Referinta implicita: nordul judetului Tulcea (zona Isaccea).
DEFAULT_REF = (45.27, 28.46)


class LocalFrame:
    """Proiectie echirectangulara in jurul unui punct de referinta.

    Eroarea sub 0,1% pe ~100 km, suficient pentru urmarire.
    """

    def __init__(self, lat0: float = DEFAULT_REF[0], lon0: float = DEFAULT_REF[1]):
        self.lat0 = lat0
        self.lon0 = lon0
        self._k = math.cos(math.radians(lat0))

    def to_xy(self, lat: float, lon: float) -> np.ndarray:
        x = math.radians(lon - self.lon0) * EARTH_R * self._k
        y = math.radians(lat - self.lat0) * EARTH_R
        return np.array([x, y])

    def to_latlon(self, x: float, y: float) -> tuple[float, float]:
        lat = self.lat0 + math.degrees(y / EARTH_R)
        lon = self.lon0 + math.degrees(x / (EARTH_R * self._k))
        return lat, lon


def bearing_unit(theta: float) -> np.ndarray:
    """Vector unitar pentru un azimut (rad, de la nord, in sens orar)."""
    return np.array([math.sin(theta), math.cos(theta)])


def ellipse_points(mean: np.ndarray, cov: np.ndarray, chi2: float = 5.991, n: int = 48) -> np.ndarray:
    """Puncte pe elipsa de incredere (implicit 95%, 2 grade de libertate)."""
    vals, vecs = np.linalg.eigh(cov[:2, :2])
    vals = np.clip(vals, 0.0, None)
    a = np.linspace(0, 2 * math.pi, n, endpoint=False)
    circle = np.stack([np.cos(a), np.sin(a)])
    pts = vecs @ (np.sqrt(vals * chi2)[:, None] * circle)
    return (pts.T + mean[:2])


def mahalanobis2(d: np.ndarray, S: np.ndarray) -> float:
    return float(d @ np.linalg.solve(S, d))
