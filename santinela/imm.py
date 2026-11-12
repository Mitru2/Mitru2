"""Filtru IMM cu doua modele de viteza constanta si netezitor RTS.

Starea: [x, y, vx, vy] in cadrul local (m, m/s).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .config import Params, DEFAULT

H = np.array([[1.0, 0, 0, 0], [0, 1.0, 0, 0]])
LOG2PI = math.log(2 * math.pi)


class _PosOnly:
    def __init__(self, z, R):
        self.z, self.R = z, R


def observation(m, x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Modelul de observatie pentru o masuratoare: (z, h(x), jacobian, R).

    * implicit: pozitie (x, y);
    * ``H_full``: masuratoare liniara generala (ex. traseu radar cu viteza);
    * ``rr``: se adauga viteza radiala fata de ``sensor_xy`` (EKF).
    """
    Hf = getattr(m, "H_full", None)
    if Hf is not None:
        zs, hs, Js, Rs = [m.z_full], [Hf @ x], [Hf], [m.R_full]
    else:
        zs, hs, Js, Rs = [m.z], [H @ x], [H], [m.R]
    rr = getattr(m, "rr", None)
    if rr is not None:
        d = x[:2] - m.sensor_xy
        rng = max(float(np.linalg.norm(d)), 1.0)
        v = x[2:]
        dv = float(d @ v)
        zs.append(np.array([rr]))
        hs.append(np.array([dv / rng]))
        Js.append(np.concatenate([v / rng - dv * d / rng ** 3, d / rng])[None, :])
        Rs.append(np.array([[m.rr_sigma ** 2]]))
    z = np.concatenate(zs)
    h = np.concatenate(hs)
    J = np.vstack(Js)
    n = len(z)
    R = np.zeros((n, n))
    i = 0
    for B in Rs:
        k = B.shape[0]
        R[i:i + k, i:i + k] = B
        i += k
    return z, h, J, R


def F_cv(dt: float) -> np.ndarray:
    F = np.eye(4)
    F[0, 2] = F[1, 3] = dt
    return F


def Q_cv(dt: float, q: float) -> np.ndarray:
    a = abs(dt)
    q3, q2 = a ** 3 / 3, a ** 2 / 2
    return q * np.array([[q3, 0, q2, 0], [0, q3, 0, q2], [q2, 0, a, 0], [0, q2, 0, a]])


@dataclass
class IMMState:
    xs: np.ndarray   # (M, 4)
    Ps: np.ndarray   # (M, 4, 4)
    mu: np.ndarray   # (M,)
    t: float

    def copy(self) -> "IMMState":
        return IMMState(self.xs.copy(), self.Ps.copy(), self.mu.copy(), self.t)

    @property
    def x(self) -> np.ndarray:
        return self.mu @ self.xs

    @property
    def P(self) -> np.ndarray:
        x = self.x
        P = np.zeros((4, 4))
        for j in range(len(self.mu)):
            d = self.xs[j] - x
            P += self.mu[j] * (self.Ps[j] + np.outer(d, d))
        return P


class IMM:
    def __init__(self, params: Params = DEFAULT, q_factor: float = 1.0):
        self.params = params
        self.qs = np.array([params.q_low, params.q_high]) * q_factor

    def transition(self, dt: float) -> np.ndarray:
        p = 1.0 - math.exp(-abs(dt) / self.params.model_switch_tau)
        M = len(self.qs)
        T = np.full((M, M), p / (M - 1))
        np.fill_diagonal(T, 1.0 - p)
        return T

    def init(self, z: np.ndarray, R: np.ndarray, t: float, speed_sigma: float) -> IMMState:
        x = np.array([z[0], z[1], 0.0, 0.0])
        P = np.zeros((4, 4))
        P[:2, :2] = R
        P[2, 2] = P[3, 3] = speed_sigma ** 2
        M = len(self.qs)
        mu = np.array(self.params.init_mu, float)[:M]
        return IMMState(np.tile(x, (M, 1)), np.tile(P, (M, 1, 1)), mu / mu.sum(), t)

    def predict(self, s: IMMState, t: float) -> IMMState:
        dt = t - s.t
        if dt == 0:
            return s.copy()
        M = len(self.qs)
        T = self.transition(dt)
        cbar = T.T @ s.mu
        cbar = np.maximum(cbar, 1e-300)
        mix = (T * s.mu[:, None]) / cbar[None, :]  # mix[i, j] = P(i | j)
        F = F_cv(dt)
        xs = np.zeros_like(s.xs)
        Ps = np.zeros_like(s.Ps)
        for j in range(M):
            x0 = mix[:, j] @ s.xs
            P0 = np.zeros((4, 4))
            for i in range(M):
                d = s.xs[i] - x0
                P0 += mix[i, j] * (s.Ps[i] + np.outer(d, d))
            xs[j] = F @ x0
            Ps[j] = F @ P0 @ F.T + Q_cv(dt, self.qs[j])
        return IMMState(xs, Ps, cbar / cbar.sum(), t)

    def update(self, s: IMMState, z: np.ndarray, R: np.ndarray) -> tuple[IMMState, float, np.ndarray]:
        """Actualizare cu o masuratoare de pozitie (z, R)."""
        return self.update_m(s, _PosOnly(z, R))

    def update_m(self, s: IMMState, m, omega: float | None = None) -> tuple[IMMState, float, np.ndarray]:
        """Actualizare generala (pozitie, Doppler, masuratoare liniara 4D).

        Cu ``omega`` se face fuziune prin intersectia covariantelor: predictia
        intra cu P/omega, masuratoarea cu R/(1-omega).
        Intoarce starea, log-verosimilitatea combinata si S-ul de pozitie combinat.
        """
        M = len(self.qs)
        xs = np.zeros_like(s.xs)
        Ps = np.zeros_like(s.Ps)
        loglik = np.zeros(M)
        for j in range(M):
            z, h, J, R = observation(m, s.xs[j])
            P = s.Ps[j]
            if omega is not None:
                P = P / omega
                R = R / (1.0 - omega)
            S = J @ P @ J.T + R
            K = P @ J.T @ np.linalg.inv(S)
            r = z - h
            xs[j] = s.xs[j] + K @ r
            IKH = np.eye(4) - K @ J
            Ps[j] = IKH @ P @ IKH.T + K @ R @ K.T  # forma Joseph
            _, logdet = np.linalg.slogdet(S)
            loglik[j] = -0.5 * (r @ np.linalg.solve(S, r) + logdet) - 0.5 * len(z) * math.log(2 * math.pi)
        lw = np.log(np.maximum(s.mu, 1e-300)) + loglik
        mx = lw.max()
        w = np.exp(lw - mx)
        total = w.sum()
        mu = w / total
        ll = float(mx + math.log(total))
        S_comb = H @ s.P @ H.T + m.R
        return IMMState(xs, Ps, mu, s.t), ll, S_comb

    def predicted_measurement(self, s: IMMState, R: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return H @ s.x, H @ s.P @ H.T + R


def rts_smooth(times: list[float], xf: list[np.ndarray], Pf: list[np.ndarray],
               xp: list[np.ndarray], Pp: list[np.ndarray]) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """Netezitor Rauch-Tung-Striebel pe momentele combinate ale IMM.

    ``xp[k]``/``Pp[k]`` sunt predictia (inainte de actualizare) la pasul k.
    """
    n = len(times)
    xs = [None] * n
    Ps = [None] * n
    xs[-1], Ps[-1] = xf[-1].copy(), Pf[-1].copy()
    for k in range(n - 2, -1, -1):
        F = F_cv(times[k + 1] - times[k])
        C = Pf[k] @ F.T @ np.linalg.pinv(Pp[k + 1])
        xs[k] = xf[k] + C @ (xs[k + 1] - xp[k + 1])
        Ps[k] = Pf[k] + C @ (Ps[k + 1] - Pp[k + 1]) @ C.T
        Ps[k] = 0.5 * (Ps[k] + Ps[k].T)
    return xs, Ps
