"""Fuziunea traseelor venite de la senzori: intersectia covariantelor (Julier si Uhlmann).

Doua trasee despre aceeasi tinta au erori corelate in mod necunoscut; CI da un
rezultat prudent (consistent) indiferent de corelatie.
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import minimize_scalar


def covariance_intersection(xa: np.ndarray, Pa: np.ndarray, xb: np.ndarray, Pb: np.ndarray,
                            criterion: str = "trace") -> tuple[np.ndarray, np.ndarray, float]:
    Ia, Ib = np.linalg.inv(Pa), np.linalg.inv(Pb)

    def fused(w: float):
        Pinv = w * Ia + (1 - w) * Ib
        P = np.linalg.inv(Pinv)
        x = P @ (w * Ia @ xa + (1 - w) * Ib @ xb)
        return x, P

    def cost(w: float) -> float:
        _, P = fused(w)
        return float(np.trace(P)) if criterion == "trace" else float(np.linalg.det(P))

    res = minimize_scalar(cost, bounds=(0.0, 1.0), method="bounded", options={"xatol": 1e-6})
    w = float(res.x)
    # verifica si capetele (minimul poate fi pe margine)
    for cand in (0.0, 1.0):
        if cost(cand) < cost(w):
            w = cand
    x, P = fused(w)
    return x, 0.5 * (P + P.T), w


def ci_weight(P: np.ndarray, J: np.ndarray, R: np.ndarray, lo: float = 0.05, hi: float = 0.95) -> float:
    """Ponderea omega pentru fuziunea CI a unei stari (P) cu o estimare externa (J, R).

    Minimizeaza urma covariantei fuzionate inv(omega P^-1 + (1-omega) J' R^-1 J).
    """
    Pi = np.linalg.inv(P)
    I_ext = J.T @ np.linalg.inv(R) @ J

    def cost(w: float) -> float:
        return float(np.trace(np.linalg.inv(w * Pi + (1 - w) * I_ext)))

    res = minimize_scalar(cost, bounds=(lo, hi), method="bounded", options={"xatol": 1e-5})
    return float(res.x)
