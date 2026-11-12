"""Faza 0: conversia raport -> masuratoare verificata numeric; corectia de sunet; calibrare."""
import math

import numpy as np

from santinela.calibration import estimate_population
from santinela.config import DEFAULT
from santinela.measurements import Report, convert_report, sound_corrected_time
from santinela.observers import ObserverModel


def test_ut_conversion_matches_monte_carlo(frame):
    obs = ObserverModel("o1", DEFAULT)
    lat, lon = frame.to_latlon(0.0, 0.0)
    rep = Report("r1", "o1", lat, lon, 0.0, bearing_deg=60.0, distance_m=2500.0)
    m = convert_report(rep, frame, obs)

    rng = np.random.default_rng(0)
    n = 200_000
    th = math.radians(60.0) + rng.normal(0, obs.sigma_theta(), n)
    d = np.exp(math.log(2500.0) - obs.bias_ln() + rng.normal(0, obs.sigma_ln(), n))
    pts = np.stack([d * np.sin(th), d * np.cos(th)], axis=1)
    pts += rng.normal(0, DEFAULT.observer_pos_sigma, pts.shape)
    mc_mean = pts.mean(axis=0)
    mc_cov = np.cov(pts.T)

    assert np.linalg.norm(m.z - mc_mean) < 25.0  # m, pe o distanta de 2,5 km
    # covariante apropiate (eroare relativa a deviatiilor standard principale < 5%)
    ev_ut = np.sqrt(np.linalg.eigvalsh(m.R))
    ev_mc = np.sqrt(np.linalg.eigvalsh(mc_cov))
    assert np.all(np.abs(ev_ut / ev_mc - 1) < 0.05)


def test_compass_accuracy_is_lower_bound(frame):
    obs = ObserverModel("o1", DEFAULT)
    assert obs.sigma_theta(25.0) == math.radians(25.0)
    assert obs.sigma_theta(2.0) == DEFAULT.sigma_theta


def test_sound_correction_values():
    # Exemplul din plan: 3 km -> ~9 s; la 125 m/s inseamna peste 1 km de traseu
    dt = 3000.0 - sound_corrected_time(3000.0, 3000.0)
    assert abs(dt - 3000 / 343) < 1e-9
    assert 8.5 < dt < 9.0
    assert dt * 125 > 1000


def test_audio_report_time_is_corrected_visual_not(frame):
    obs = ObserverModel("o1", DEFAULT)
    lat, lon = frame.to_latlon(0.0, 0.0)
    a = convert_report(Report("a", "o1", lat, lon, 1000.0, 0.0, 3000.0, kind="audio"), frame, obs)
    v = convert_report(Report("v", "o1", lat, lon, 1000.0, 0.0, 3000.0, kind="visual"), frame, obs)
    assert v.t == 1000.0
    assert 990.0 < a.t < 992.0


def test_population_params_ci_covers_truth(frame):
    rng = np.random.default_rng(3)
    true_sigma_deg, true_bias, true_sln = 12.0, 0.15, 0.3
    rows = []
    for _ in range(400):
        o = rng.uniform(-5000, 5000, 2)
        t = o + rng.uniform(500, 4000) * np.array([math.sin(a := rng.uniform(0, 2 * math.pi)), math.cos(a)])
        d = t - o
        b = math.degrees(math.atan2(d[0], d[1])) + rng.normal(0, true_sigma_deg)
        dist = np.linalg.norm(d) * math.exp(true_bias + rng.normal(0, true_sln))
        olat, olon = frame.to_latlon(*o)
        tlat, tlon = frame.to_latlon(*t)
        rows.append({"obs_lat": olat, "obs_lon": olon, "bearing_deg": b % 360, "distance_m": dist,
                     "true_lat": tlat, "true_lon": tlon})
    pp = estimate_population(rows, frame)
    assert pp.n == 400
    assert pp.sigma_theta_deg.lo <= true_sigma_deg <= pp.sigma_theta_deg.hi
    assert pp.dist_bias_ln.lo <= true_bias <= pp.dist_bias_ln.hi
    assert pp.sigma_ln.lo <= true_sln <= pp.sigma_ln.hi
