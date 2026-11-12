"""Integrarea radarelor.

* detectii polare (distanta, azimut, optional viteza radiala Doppler si inaltime);
* trasee calculate de radar, fuzionate prin intersectia covariantelor (erorile
  succesive ale aceluiasi traseu extern sunt corelate in mod necunoscut);
* acoperire dependenta de altitudine: tacerea radarului conteaza ca informatie
  negativa doar daca tinta e cunoscuta ca fiind deasupra altitudinii minime de
  detectie si in interiorul orizontului radar. Fara altitudine raportata se
  presupune "foarte jos", deci radarul nu scade scorul (cazul Luncavita);
* calibrarea erorilor sistematice (azimut, distanta, decalaj de ceas) prin
  cele mai mici patrate fata de trasee confirmate din alte surse.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .config import Params, DEFAULT
from .geo import LocalFrame
from .measurements import Measurement, polar_gauss_moments
from .observers import ObserverModel

K_HORIZON = 4120.0  # m / sqrt(m): orizont radar cu raza efectiva 4/3 a Pamantului


def radar_horizon(antenna_h: float, target_h: float) -> float:
    """Distanta maxima de vizibilitate directa (m), Pamant neted, refractie standard."""
    return K_HORIZON * (math.sqrt(max(antenna_h, 0.0)) + math.sqrt(max(target_h, 0.0)))


@dataclass(eq=False)
class RadarSite:
    sensor_id: str
    lat: float
    lon: float
    antenna_height: float = 15.0      # m deasupra terenului
    max_range: float = 60000.0        # m
    min_alt: float = 300.0            # m, altitudinea minima de detectie fiabila (diagrama de acoperire)
    pd: float = 0.85
    sigma_range: float = 50.0         # m
    sigma_az_deg: float = 0.5
    sigma_rr: float = 1.0             # m/s
    lambda_fa: float = 1e-10          # plot-uri false / m^2 (pasari, clutter)
    sac: int | None = None            # identificator ASTERIX
    sic: int | None = None
    center: np.ndarray | None = None  # se completeaza la inregistrare in tracker
    active: bool = True

    @property
    def post_id(self) -> str:
        return self.sensor_id

    def attach(self, frame: LocalFrame) -> "RadarSite":
        self.center = frame.to_xy(self.lat, self.lon)
        return self

    def coverage_radius(self, altitude_floor: float | None) -> float | None:
        """Raza in care radarul ar fi trebuit sa vada tinta, sau None daca nu se stie."""
        if altitude_floor is None or altitude_floor < self.min_alt:
            return None
        return min(self.max_range, radar_horizon(self.antenna_height, altitude_floor))

    def detects(self, pos: np.ndarray, target_alt: float) -> bool:
        """Folosit de simulator: ar detecta radarul o tinta aici?"""
        if target_alt < self.min_alt:
            return False
        r = float(np.linalg.norm(pos[:2] - self.center))
        return r <= min(self.max_range, radar_horizon(self.antenna_height, target_alt))


@dataclass
class RadarPlot:
    """Detectie radar in coordonate polare (ca in ASTERIX CAT048, I048/040)."""
    report_id: str
    sensor_id: str
    t: float
    range_m: float                    # distanta oblica
    azimuth_deg: float
    rr: float | None = None           # viteza radiala, m/s, pozitiva la departare
    height_m: float | None = None     # inaltime (radar 3D) sau din nivel de zbor
    drone_class: str | None = None
    received_at: float | None = None


@dataclass
class RadarTrackReport:
    """Traseu calculat de radar (ex. I048/161 + I048/042 + I048/200 + I048/210)."""
    report_id: str
    sensor_id: str
    track_number: int
    t: float
    x: float                          # m, est, fata de radar
    y: float                          # m, nord, fata de radar
    vx: float | None = None
    vy: float | None = None
    cov: np.ndarray | None = None     # 2x2 sau 4x4, m si m/s
    height_m: float | None = None
    confirmed: bool = True
    drone_class: str | None = None
    received_at: float | None = None


@dataclass
class RadarRegistration:
    """Erori sistematice estimate ale unui radar: azimut (rad), distanta (m), ceas (s).

    Cele mai mici patrate ponderate: fiecare rand are covarianta (referinta + plot).
    Erorile referintei sunt corelate intre plot-urile succesive, deci covarianta
    estimarii se scaleaza cu (plot-uri / masuratori independente de referinta).
    Estimarea se aplica doar daca toate erorile standard sunt sub prag.
    """
    bias_az: float = 0.0
    bias_range: float = 0.0
    clock_offset: float = 0.0        # t_real = t_marcat + clock_offset
    n: int = 0
    n_ref: int = 0
    _AtWA: np.ndarray = field(default_factory=lambda: np.zeros((3, 3)), repr=False)
    _AtWb: np.ndarray = field(default_factory=lambda: np.zeros(3), repr=False)
    estimated: bool = False
    sigma: np.ndarray | None = None  # erori standard (rad, m, s) ale ultimei solutii

    def accumulate(self, rows: list[tuple[np.ndarray, np.ndarray, np.ndarray]], n_ref: int) -> None:
        """Fiecare rand: (A 2x3, reziduu 2, covarianta 2x2) cu reziduu ~ A @ [b_az, b_r, c]."""
        for A, b, C in rows:
            W = np.linalg.inv(C)
            self._AtWA += A.T @ W @ A
            self._AtWb += A.T @ W @ b
            self.n += 1
        self.n_ref += n_ref

    def solve(self, min_n: int, max_sigma: tuple = (math.radians(0.25), 40.0, 0.5),
              max_cond: float = 1e10) -> bool:
        if self.n < min_n or self.n_ref == 0:
            return False
        d = np.sqrt(np.maximum(np.diag(self._AtWA), 1e-30))
        M = self._AtWA / np.outer(d, d)
        if np.linalg.cond(M) > max_cond:
            return False
        Minv = np.linalg.inv(M)
        sol = (Minv @ (self._AtWb / d)) / d
        cov = Minv / np.outer(d, d) * max(1.0, self.n / self.n_ref)
        self.sigma = np.sqrt(np.diag(cov))
        if np.any(self.sigma > np.asarray(max_sigma)):
            return False
        self.bias_az, self.bias_range, self.clock_offset = (float(v) for v in sol)
        self.estimated = True
        return True


def _site_model(site: RadarSite, model: ObserverModel | None, params: Params):
    if model is None:
        return site.lambda_fa, None
    return site.lambda_fa * model.lambda_fa() / params.lambda_fa_base, model.trust


def convert_plot(plot: RadarPlot, site: RadarSite, reg: RadarRegistration | None = None,
                 model: ObserverModel | None = None, params: Params = DEFAULT) -> Measurement:
    reg = reg or RadarRegistration()
    az = math.radians(plot.azimuth_deg) - reg.bias_az
    r = plot.range_m - reg.bias_range
    if plot.height_m is not None:
        dh = plot.height_m - site.antenna_height
        r = math.sqrt(max(r * r - dh * dh, 0.0))  # distanta la sol
    z, R = polar_gauss_moments(site.center, az, r, math.radians(site.sigma_az_deg), site.sigma_range)
    lam, trust = _site_model(site, model, params)
    return Measurement(
        mid=plot.report_id, source_id=site.sensor_id, t=plot.t + reg.clock_offset, z=z, R=R,
        source_kind="sensor", drone_class=plot.drone_class, pd=site.pd, lambda_fa=lam, trust=trust,
        altitude_m=plot.height_m, rr=plot.rr, rr_sigma=site.sigma_rr if plot.rr is not None else None,
        sensor_xy=site.center.copy(), raw=plot,
    )


def convert_track_report(rep: RadarTrackReport, site: RadarSite, model: ObserverModel | None = None,
                         params: Params = DEFAULT) -> Measurement:
    pos = site.center + np.array([rep.x, rep.y])
    lam, trust = _site_model(site, model, params)
    has_v = rep.vx is not None and rep.vy is not None
    if rep.cov is not None:
        C = np.asarray(rep.cov, float)
    else:
        s = site.sigma_range * 2
        C = np.diag([s * s, s * s, 10.0 ** 2, 10.0 ** 2]) if has_v else np.eye(2) * s * s
    m = Measurement(
        mid=rep.report_id, source_id=site.sensor_id, t=rep.t, z=pos, R=C[:2, :2].copy(),
        source_kind="sensor", drone_class=rep.drone_class, pd=site.pd, lambda_fa=lam, trust=trust,
        altitude_m=rep.height_m, ci=True, ext_track=(site.sensor_id, rep.track_number), raw=rep,
    )
    if has_v and C.shape == (4, 4):
        m.z_full = np.array([pos[0], pos[1], rep.vx, rep.vy])
        m.R_full = C
        m.H_full = np.eye(4)
    return m


def registration_rows(site: RadarSite, plots_and_refs: list[tuple]) -> list[tuple]:
    """Randuri pentru cele mai mici patrate: reziduu cartezian fata de starea de referinta.

    Fiecare element: (plot, ref, P_ref) cu ``ref`` = [x, y, vx, vy] la ora marcata a
    plot-ului si ``P_ref`` covarianta de pozitie (2x2) a referintei.
    Modelul liniarizat: p_masurat - p_ref = J_az * b_az + J_r * b_r + v * c.
    """
    rows = []
    s_az = math.radians(site.sigma_az_deg)
    for plot, ref, P_ref in plots_and_refs:
        az = math.radians(plot.azimuth_deg)
        r = plot.range_m
        if plot.height_m is not None:
            dh = plot.height_m - site.antenna_height
            r = math.sqrt(max(r * r - dh * dh, 0.0))
        u = np.array([math.sin(az), math.cos(az)])
        J_az = r * np.array([math.cos(az), -math.sin(az)])
        p_meas = site.center + r * u
        A = np.column_stack([J_az, u, ref[2:4]])
        R_plot = np.outer(J_az, J_az) * s_az ** 2 + np.outer(u, u) * site.sigma_range ** 2
        rows.append((A, p_meas - ref[:2], R_plot + np.asarray(P_ref)[:2, :2]))
    return rows
