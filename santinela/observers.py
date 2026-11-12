"""Model de eroare si scor de incredere pe observator (imbunatatirea 4)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .config import Params, DEFAULT


@dataclass
class ObserverModel:
    observer_id: str
    params: Params = field(default=DEFAULT, repr=False)
    # Parametri proprii (None = foloseste populatia)
    own_sigma_theta: float | None = None
    own_bias_ln: float | None = None
    own_sigma_ln: float | None = None
    # Reziduuri acumulate din trasee confirmate
    bearing_residuals: list = field(default_factory=list)
    logdist_residuals: list = field(default_factory=list)
    # Incredere: Beta(a + confirmed, b + false)
    confirmed: int = 0
    false: int = 0
    # Sursa civila sau post cu acoperire cunoscuta?
    civilian: bool = True

    # ---------- parametri de eroare ----------
    def sigma_theta(self, compass_accuracy_deg: float | None = None) -> float:
        s = self.own_sigma_theta if self.own_sigma_theta is not None else self.params.sigma_theta
        if compass_accuracy_deg is not None:
            # Eroarea raportata de sistem e limita inferioara pentru sigma_theta
            s = max(s, math.radians(compass_accuracy_deg))
        return s

    def bias_ln(self) -> float:
        return self.own_bias_ln if self.own_bias_ln is not None else self.params.dist_bias_ln

    def sigma_ln(self) -> float:
        return self.own_sigma_ln if self.own_sigma_ln is not None else self.params.sigma_ln

    # ---------- incredere ----------
    @property
    def beta_ab(self) -> tuple[float, float]:
        a0, b0 = self.params.trust_prior
        return a0 + self.confirmed, b0 + self.false

    @property
    def trust(self) -> float:
        a, b = self.beta_ab
        return a / (a + b)

    def lambda_fa(self) -> float:
        """Densitatea de rapoarte false a sursei, scalata cu increderea."""
        a0, b0 = self.params.trust_prior
        p_false_prior = b0 / (a0 + b0)
        p_false = 1.0 - self.trust
        return self.params.lambda_fa_base * p_false / p_false_prior

    # ---------- invatare ----------
    def add_residuals(self, bearing_res: list[float], logdist_res: list[float]) -> None:
        self.bearing_residuals.extend(bearing_res)
        self.logdist_residuals.extend(logdist_res)
        if len(self.bearing_residuals) >= self.params.min_reports_own_params:
            b = np.asarray(self.bearing_residuals)
            self.own_sigma_theta = float(max(np.sqrt(np.mean(b ** 2)), math.radians(1.0)))
        if len(self.logdist_residuals) >= self.params.min_reports_own_params:
            d = np.asarray(self.logdist_residuals)
            self.own_bias_ln = float(np.mean(d))
            self.own_sigma_ln = float(max(np.std(d, ddof=1), 0.05))

    def record_verdict(self, true_track: bool) -> None:
        if true_track:
            self.confirmed += 1
        else:
            self.false += 1


class ObserverRegistry:
    def __init__(self, params: Params = DEFAULT):
        self.params = params
        self._obs: dict[str, ObserverModel] = {}

    def get(self, observer_id: str) -> ObserverModel:
        if observer_id not in self._obs:
            self._obs[observer_id] = ObserverModel(observer_id, self.params)
        return self._obs[observer_id]

    def __contains__(self, item: str) -> bool:
        return item in self._obs

    def all(self) -> list[ObserverModel]:
        return [self._obs[k] for k in sorted(self._obs)]
