"""Faza 2 - harta.

* elipsa de 95% contine tinta simulata in 90-98% din cazuri;
* trecerea in mod grup la scenariul cu 12 tinte paralele.
"""
import json

import numpy as np

from santinela import Tracker, sim
from santinela.config import DEFAULT
from santinela.export import to_geojson
from santinela.measurements import Measurement
from santinela.sim import simulate_imm_truth
from santinela.track import Track


def test_ellipse95_coverage():
    rng = np.random.default_rng(7)
    inside = total = 0
    R = np.eye(2) * 300.0 ** 2
    for run in range(150):
        x0 = np.array([0.0, 0.0, *rng.normal(0, DEFAULT.init_speed_sigma, 2)])
        X = simulate_imm_truth(rng, DEFAULT, 40, 10.0, x0)
        tr = Track(f"T{run}", DEFAULT)
        for k, x in enumerate(X):
            z = x[:2] + rng.multivariate_normal([0, 0], R)
            tr.add(Measurement(f"m{k:03d}", "s", 10.0 * k, z, R, "sensor"))
            if k >= 5:
                d = x[:2] - tr.state.x[:2]
                inside += d @ np.linalg.solve(tr.state.P[:2, :2], d) <= 5.991
                total += 1
    cov = inside / total
    assert 0.90 <= cov <= 0.98, cov


def test_group_mode_with_12_parallel_targets():
    scn = sim.parallel_wave(12)
    tr = Tracker(frame=scn.frame)
    sim.run(scn, tr, sim.generate_reports(scn, seed=1))
    assert len(tr.groups) >= 1
    g = tr.groups["G001"]
    s = g.summary()
    assert s["est_count"] >= 6
    # curs comun spre vest (270 grd) si viteza de ordinul Geran-2 (51 m/s)
    assert 240 <= s["course_deg"] <= 300
    assert 30 <= s["speed_mps"] <= 80
    ev = [j for j in tr.journal if j["event"] == "mod_grup"]
    assert ev and ev[0]["author"] == "automat" and "ambigue" in ev[0]["rule"]


def test_geojson_export_layers():
    scn = sim.dunare()
    tr = sim.make_tracker(scn)
    # instantaneu la mijlocul scenariului, cand mai multe trasee sunt active
    items = [i for i in sim.generate_reports(scn, seed=1) if i.received_at <= 1800.0]
    sim.run(scn, tr, items)
    gj = to_geojson(tr, max(i.received_at for i in items), scn.destinations, scn.observers, radars=scn.radars)
    json.dumps(gj)  # serializabil
    layers = {f["properties"]["layer"] for f in gj["features"]}
    assert {"traseu_netezit", "pozitie", "elipsa95", "con", "destinatii_umbra", "observator",
            "radar", "acoperire_radar"} <= layers
    horizons = {f["properties"]["horizon_min"] for f in gj["features"] if f["properties"]["layer"] == "con"}
    assert horizons <= {5, 10, 15}
