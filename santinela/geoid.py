"""Ondulatia geoidului EGM96: conversia intre inaltimea elipsoidala (HAE, WGS-84) si nivelul marii (MSL).

santinela/data/egm96_ro_5min.npy: subgrila 41-50 N, 18-33 E, pas 5' (centimetri, int16), extrasa din
grila globala EGM96 de 5' a pachetului egm96 0.3.0 (MIT). Grila globala reproduce valorile de test
NGA pentru EGM96 la mai putin de 5 mm (vezi conventia 'geoid.egm96'). Interpolare biliniara
(eroare < 0,06 m fata de modelul complet, sub incertitudinea EGM96 insusi).

Inaltimile din tracker sunt fata de nivelul marii: H_MSL = h_HAE - N.
"""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import numpy as np

GRID = Path(__file__).parent / "data" / "egm96_ro_5min.npy"
LAT0, LAT1, LON0, LON1, STEP = 41.0, 50.0, 18.0, 33.0, 5.0 / 60.0


@lru_cache(maxsize=1)
def _grid() -> np.ndarray:
    return np.load(GRID, allow_pickle=False).astype(np.float64) / 100.0


def undulation(lat: float, lon: float) -> float | None:
    """N (m) = inaltimea geoidului deasupra elipsoidului; None in afara grilei regionale."""
    if not (LAT0 <= lat <= LAT1 and LON0 <= lon <= LON1):
        return None
    U = _grid()
    i, j = (lat - LAT0) / STEP, (lon - LON0) / STEP
    i0, j0 = min(int(math.floor(i)), U.shape[0] - 2), min(int(math.floor(j)), U.shape[1] - 2)
    di, dj = i - i0, j - j0
    return float(U[i0, j0] * (1 - di) * (1 - dj) + U[i0 + 1, j0] * di * (1 - dj)
                 + U[i0, j0 + 1] * (1 - di) * dj + U[i0 + 1, j0 + 1] * di * dj)


def hae_to_msl(lat: float, lon: float, h: float) -> float | None:
    n = undulation(lat, lon)
    return None if n is None else h - n


def msl_to_hae(lat: float, lon: float, h: float) -> float | None:
    n = undulation(lat, lon)
    return None if n is None else h + n
