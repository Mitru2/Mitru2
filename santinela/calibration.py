"""Faza 0 - parametri de eroare pe populatie, din exportul exercitiului de coordonate.

Intrare: randuri cu pozitia observatorului, azimutul si distanta raportate si
pozitia reala a tintei. Iesire: sigma_theta, deplasarea si sigma_ln la distanta,
fiecare cu interval de incredere de 95%.

Format CSV acceptat (antet obligatoriu):
    obs_lat,obs_lon,bearing_deg,distance_m,true_lat,true_lon
"""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import stats

from .geo import LocalFrame


@dataclass
class Estimate:
    value: float
    lo: float
    hi: float

    def as_dict(self) -> dict:
        return {"value": self.value, "ci95": [self.lo, self.hi]}


@dataclass
class PopulationParams:
    n: int
    bearing_bias_deg: Estimate
    sigma_theta_deg: Estimate
    dist_bias_ln: Estimate
    sigma_ln: Estimate

    def as_dict(self) -> dict:
        return {k: (v.as_dict() if isinstance(v, Estimate) else v) for k, v in self.__dict__.items()}


def _mean_ci(x: np.ndarray) -> Estimate:
    n = len(x)
    m = float(np.mean(x))
    se = float(np.std(x, ddof=1) / math.sqrt(n))
    q = stats.t.ppf(0.975, n - 1)
    return Estimate(m, m - q * se, m + q * se)


def _sigma_ci(x: np.ndarray, centered: bool = True) -> Estimate:
    n = len(x)
    if centered:
        s2 = float(np.var(x, ddof=1))
        df = n - 1
    else:
        s2 = float(np.mean(x ** 2))
        df = n
    lo = math.sqrt(df * s2 / stats.chi2.ppf(0.975, df))
    hi = math.sqrt(df * s2 / stats.chi2.ppf(0.025, df))
    return Estimate(math.sqrt(s2), lo, hi)


def residuals(rows: list[dict], frame: LocalFrame | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Reziduuri de azimut (grade) si de ln(distanta)."""
    frame = frame or LocalFrame(float(rows[0]["obs_lat"]), float(rows[0]["obs_lon"]))
    b, l = [], []
    for r in rows:
        o = frame.to_xy(float(r["obs_lat"]), float(r["obs_lon"]))
        t = frame.to_xy(float(r["true_lat"]), float(r["true_lon"]))
        d = t - o
        true_b = math.degrees(math.atan2(d[0], d[1]))
        br = (float(r["bearing_deg"]) - true_b + 180.0) % 360.0 - 180.0
        b.append(br)
        dist = r.get("distance_m")
        if dist not in (None, "", "nan"):
            l.append(math.log(float(dist)) - math.log(max(np.linalg.norm(d), 1.0)))
    return np.array(b), np.array(l)


def estimate_population(rows: list[dict], frame: LocalFrame | None = None) -> PopulationParams:
    b, l = residuals(rows, frame)
    return PopulationParams(
        n=len(b),
        bearing_bias_deg=_mean_ci(b),
        sigma_theta_deg=_sigma_ci(b, centered=True),
        dist_bias_ln=_mean_ci(l),
        sigma_ln=_sigma_ci(l, centered=True),
    )


def load_csv(path: str | Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))
