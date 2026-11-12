"""Rapoarte si conversia raport -> masuratoare.

Un raport uman (azimut + distanta estimata) devine o masuratoare de pozitie
carteziana cu covarianta, prin cuadratura Gauss-Hermite pe (azimut, ln distanta).
Rapoartele "am auzit" se corecteaza cu timpul de propagare a sunetului.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .config import Params, DEFAULT
from .geo import LocalFrame
from .observers import ObserverModel


@dataclass
class Report:
    """Raport de la un observator uman (fisa de raportare)."""
    report_id: str
    observer_id: str
    obs_lat: float
    obs_lon: float
    t: float                      # ora observarii (s); pentru "audio" = ora la care s-a auzit
    bearing_deg: float            # azimut de la nord, sens orar
    distance_m: float | None = None
    kind: str = "visual"          # "visual" | "audio"
    drone_class: str | None = None
    altitude_cat: str | None = None  # None -> "foarte jos" la calculul de vizibilitate
    compass_accuracy_deg: float | None = None
    emission: str | None = None   # "mesh" | "4g" daca s-a captat
    received_at: float | None = None


@dataclass
class SensorDetection:
    """Detectie de pozitie de la un senzor (radar, RF, acustic) cu covarianta data."""
    report_id: str
    sensor_id: str
    t: float
    lat: float
    lon: float
    cov: np.ndarray               # 2x2, m^2
    drone_class: str | None = None
    emission: str | None = None
    pd: float = 0.9
    lambda_fa: float = 1e-10
    received_at: float | None = None


@dataclass
class Measurement:
    """Masuratoare de pozitie in cadrul local, gata pentru filtru."""
    mid: str
    source_id: str
    t: float                      # ora la care drona era in pozitia z
    z: np.ndarray                 # (2,)
    R: np.ndarray                 # (2,2)
    source_kind: str              # "observer" | "sensor" | "track"
    drone_class: str | None = None
    emission: str | None = None
    pd: float = 0.8
    lambda_fa: float = 2.5e-9
    trust: float | None = None
    # pentru invatarea modelului de observator
    obs_xy: np.ndarray | None = None
    bearing: float | None = None
    distance: float | None = None
    raw: object = field(default=None, repr=False)

    @property
    def sort_key(self) -> tuple:
        return (round(self.t, 6), self.mid)


_GH_X, _GH_W = np.polynomial.hermite_e.hermegauss(7)
_GH_W = _GH_W / _GH_W.sum()


def _polar_moments(obs_xy: np.ndarray, theta: float, ln_d: float, s_theta: float,
                   s_ln: float) -> tuple[np.ndarray, np.ndarray]:
    """Media si covarianta lui (x, y) pentru theta ~ N, ln d ~ N, prin cuadratura Gauss-Hermite 7x7.

    Distanta log-normala face ca transformata unscented simpla sa subestimeze
    imprastierea transversala (~12% la sigma_ln = 0,35); cuadratura e exacta
    pana la ordin inalt si ramane ieftina (49 de puncte).
    """
    th = theta + s_theta * _GH_X
    d = np.exp(ln_d + s_ln * _GH_X)
    W = np.outer(_GH_W, _GH_W)               # [i_theta, j_d]
    X = np.outer(np.sin(th), d)
    Y = np.outer(np.cos(th), d)
    mx, my = float((W * X).sum()), float((W * Y).sum())
    dx, dy = X - mx, Y - my
    P = np.array([[(W * dx * dx).sum(), (W * dx * dy).sum()],
                  [(W * dx * dy).sum(), (W * dy * dy).sum()]])
    return obs_xy + np.array([mx, my]), P


def convert_report(rep: Report, frame: LocalFrame, observer: ObserverModel,
                   params: Params = DEFAULT) -> Measurement:
    obs_xy = frame.to_xy(rep.obs_lat, rep.obs_lon)
    theta = math.radians(rep.bearing_deg)
    s_theta = observer.sigma_theta(rep.compass_accuracy_deg)
    if rep.distance_m is not None and rep.distance_m > 0:
        d = rep.distance_m
        s_ln = observer.sigma_ln()
        bias = observer.bias_ln()
    else:
        d = params.default_distance_audio if rep.kind == "audio" else params.default_distance_visual
        s_ln = params.sigma_ln_no_distance
        bias = 0.0
    ln_d = math.log(d) - bias
    z, R = _polar_moments(obs_xy, theta, ln_d, s_theta, s_ln)
    R = R + np.eye(2) * params.observer_pos_sigma ** 2

    t = rep.t
    if rep.kind == "audio":
        t = sound_corrected_time(rep.t, math.exp(ln_d), params.speed_of_sound)

    return Measurement(
        mid=rep.report_id, source_id=rep.observer_id, t=t, z=z, R=R,
        source_kind="observer", drone_class=rep.drone_class, emission=rep.emission,
        pd=params.pd_observer, lambda_fa=observer.lambda_fa(), trust=observer.trust,
        obs_xy=obs_xy, bearing=theta, distance=rep.distance_m, raw=rep,
    )


def convert_sensor(det: SensorDetection, frame: LocalFrame) -> Measurement:
    return Measurement(
        mid=det.report_id, source_id=det.sensor_id, t=det.t,
        z=frame.to_xy(det.lat, det.lon), R=np.asarray(det.cov, float),
        source_kind="sensor", drone_class=det.drone_class, emission=det.emission,
        pd=det.pd, lambda_fa=det.lambda_fa, raw=det,
    )


def sound_corrected_time(t_heard: float, distance_m: float, c: float = DEFAULT.speed_of_sound) -> float:
    """Ora emisiei sunetului: t_auzit - distanta / c."""
    return t_heard - distance_m / c
