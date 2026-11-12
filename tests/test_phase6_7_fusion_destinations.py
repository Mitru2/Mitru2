"""Faza 6 (fuziune de trasee) si Faza 7 (destinatii probabile)."""
import math

import numpy as np

from santinela import Tracker, sim
from santinela.config import DEFAULT
from santinela.destinations import Destination, destination_probabilities
from santinela.fusion import covariance_intersection
from santinela.measurements import Measurement
from santinela.track import Track


def test_covariance_intersection_is_consistent_for_any_correlation():
    rng = np.random.default_rng(0)
    Pa = np.array([[400.0, 100.0], [100.0, 200.0]]) ** 1
    Pb = np.array([[150.0, -60.0], [-60.0, 500.0]])
    x, P, w = covariance_intersection(np.zeros(2), Pa, np.zeros(2), Pb)
    assert 0.0 <= w <= 1.0
    for rho in (-0.9, -0.5, 0.0, 0.5, 0.9, 0.99):
        # eroare comuna corelata: covarianta incrucisata Pab
        La, Lb = np.linalg.cholesky(Pa), np.linalg.cholesky(Pb)
        Pab = rho * La @ Lb.T
        Ia, Ib = np.linalg.inv(Pa), np.linalg.inv(Pb)
        Pinv = w * Ia + (1 - w) * Ib
        Kf = np.linalg.inv(Pinv)
        Ka, Kb = Kf @ (w * Ia), Kf @ ((1 - w) * Ib)
        true_P = Ka @ Pa @ Ka.T + Kb @ Pb @ Kb.T + Ka @ Pab @ Kb.T + Kb @ Pab.T @ Ka.T
        # P_CI - P_real trebuie sa fie pozitiv semidefinita
        assert np.linalg.eigvalsh(P - true_P).min() > -1e-6
    assert np.trace(P) <= min(np.trace(Pa), np.trace(Pb)) + 1e-9


def test_destination_correct_before_halfway_in_70pct():
    rng = np.random.default_rng(21)
    dests = [Destination("Vest", np.array([-40000.0, 0.0]), 2500.0),
             Destination("SudVest", np.array([-28000.0, -28000.0]), 2500.0),
             Destination("Sud", np.array([0.0, -40000.0]), 2500.0)]
    R = np.eye(2) * 350.0 ** 2
    ok = total = 0
    for run in range(60):
        d = dests[run % 3]
        start = np.array([0.0, 0.0]) + rng.normal(0, 1000, 2)
        speed = 50.0
        L = np.linalg.norm(d.center - start)
        u = (d.center - start) / L
        tr = Track(f"T{run}", DEFAULT)
        k = 0
        # observam doar pana la 45% din drum
        while 20.0 * k * speed < 0.45 * L:
            lateral = np.array([-u[1], u[0]]) * 600 * math.sin(k / 6.0)  # mica serpuire
            truth = start + u * speed * 20.0 * k + lateral
            tr.add(Measurement(f"m{k:03d}", "o", 20.0 * k, truth + rng.multivariate_normal([0, 0], R), R, "sensor"))
            k += 1
        est = destination_probabilities(tr, dests)
        ok += est[0].name == d.name
        total += 1
        assert abs(sum(e.probability for e in est) - 1) < 1e-9
    assert ok / total >= 0.70, ok / total


def test_dunare_scenario_end_to_end():
    for seed in (1, 2, 3):
        scn = sim.dunare()
        tr = sim.make_tracker(scn)
        sim.run(scn, tr, sim.generate_reports(scn, seed=seed))
        confirmed = [t for t in tr.tracks.values() if t.status == "confirmat" and t.group_id is None]
        assert len(confirmed) == 4, seed  # A, B, C foarte jos + D la altitudine medie
        # tinta B poarta modem mesh: traseul ei e marcat "posibil operator in bucla"
        assert sum(t.operator_in_loop for t in confirmed) == 1
        # observatorul care raporteaza doar fals nu are niciun traseu confirmat numai din rapoartele lui
        for t in confirmed:
            assert t.sources != {"O-Fals"}
