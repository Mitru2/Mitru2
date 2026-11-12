"""Mod "grup" pentru valuri dense (imbunatatirea 7).

Cand numarul de asocieri ambigue dintr-o zona depaseste un prag, zona trece pe
urmarire de grup: centroid, intindere, numar estimat de tinte si curs comun.
Rapoartele se ataseaza grupului, nu unei drone anume.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from .config import Params, DEFAULT
from .imm import H
from .measurements import Measurement
from .track import Track


@dataclass
class Group:
    group_id: str
    params: Params = field(default=DEFAULT, repr=False)
    created_at: float = 0.0
    members: list = field(default_factory=list)        # id-uri de trasee absorbite
    reports: list = field(default_factory=list)        # Measurement atasate
    centroid: Track | None = None
    extent_cov: np.ndarray = field(default_factory=lambda: np.eye(2) * 2000.0 ** 2)
    active: bool = True
    _last_time: float | None = field(default=None, repr=False)
    MAX_CENTROID = 40  # rapoarte pastrate in tamponul centroidului

    def _inflated(self, m: Measurement) -> Measurement:
        return Measurement(mid=m.mid, source_id=m.source_id, t=m.t, z=m.z, R=m.R + self.extent_cov,
                           source_kind=m.source_kind, drone_class=m.drone_class,
                           emission=m.emission, pd=m.pd, lambda_fa=m.lambda_fa, trust=m.trust)

    def seed(self, measurements: list[Measurement]) -> None:
        ms = sorted(measurements, key=lambda m: m.sort_key)
        if ms:
            Z = np.array([m.z for m in ms])
            if len(ms) >= 3:
                self.extent_cov = np.cov(Z.T) + np.eye(2) * 500.0 ** 2
        self.centroid = Track(f"{self.group_id}-c", self.params, speed_sigma=self.params.init_speed_sigma)
        for m in ms:
            self.reports.append(m)
            self.centroid.entries.append(self._inflated(m))
        self.centroid.entries.sort(key=lambda e: e.sort_key)
        self.centroid.refilter()

    def position_at(self, t: float) -> np.ndarray:
        return H @ self.centroid.state_at(t).x

    def contains(self, m: Measurement) -> bool:
        return self.active and np.linalg.norm(m.z - self.position_at(m.t)) <= self.params.group_radius

    def add(self, m: Measurement) -> None:
        self.reports.append(m)
        self._last_time = max(self.last_time, m.t)
        self.centroid.add(self._inflated(m))
        # Centroidul se calculeaza pe o fereastra glisanta: tamponul ramane mic,
        # deci re-filtrarea pentru rapoarte intarziate ramane ieftina.
        if len(self.centroid.entries) > 2 * self.MAX_CENTROID:
            self.centroid.entries = self.centroid.entries[-self.MAX_CENTROID:]
            self.centroid.refilter()
        if len(self.reports) % 10 == 0:
            self._update_extent()

    def _update_extent(self) -> None:
        lt = self.last_time - self.params.group_window
        recent = [m for m in self.reports[-100:] if m.t >= lt]
        if len(recent) >= 3:
            res = np.array([m.z - self.position_at(m.t) for m in recent])
            C = (res.T @ res) / len(res)
            self.extent_cov = C + np.eye(2) * 500.0 ** 2

    @property
    def last_time(self) -> float:
        if self._last_time is None:
            self._last_time = max(m.t for m in self.reports) if self.reports else self.created_at
        return self._last_time

    @property
    def est_count(self) -> int:
        """max(trasee absorbite, cele mai multe rapoarte ale unui observator in 60 s)."""
        per_obs = defaultdict(list)
        for m in self.reports:
            if m.source_kind == "observer":  # senzorii raporteaza periodic, nu pe tinta
                per_obs[m.source_id].append(m.t)
        best = 0
        for ts in per_obs.values():
            ts.sort()
            j = 0
            for i in range(len(ts)):
                while ts[i] - ts[j] > 60.0:
                    j += 1
                best = max(best, i - j + 1)
        return max(len(self.members), best)

    @property
    def course_deg(self) -> float:
        x = self.centroid.state.x
        return math.degrees(math.atan2(x[2], x[3])) % 360.0

    @property
    def speed(self) -> float:
        x = self.centroid.state.x
        return float(math.hypot(x[2], x[3]))

    def summary(self) -> dict:
        x = self.centroid.state.x
        return {
            "group_id": self.group_id,
            "centroid": [float(x[0]), float(x[1])],
            "extent_cov": self.extent_cov.tolist(),
            "est_count": self.est_count,
            "course_deg": round(self.course_deg, 1),
            "speed_mps": round(self.speed, 1),
            "members": list(self.members),
            "reports": len(self.reports),
        }
