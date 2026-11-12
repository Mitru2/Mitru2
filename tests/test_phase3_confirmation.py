"""Faza 3 - confirmare si pre-alerta.

* fiecare confirmare are regula si autorul in jurnal;
* un observator cu rapoarte false repetate nu mai poate confirma singur un traseu;
* informatia negativa de la posturi fixe scade scorul unui traseu tentativ;
* modelul de eroare al observatorului se adapteaza dupa trasee confirmate.
"""
import math

import numpy as np

from santinela import Tracker, CONFIRMED, TENTATIVE, SensorDetection
from santinela.sensors import FixedPost

from conftest import make_report, straight_target


def _single_observer_reports(frame, obs_id="O1", n=8, seed=0, t0=0.0, prefix="R"):
    rng = np.random.default_rng(seed)
    return [make_report(frame, f"{prefix}{k:03d}", obs_id, (16000.0, 1500.0), straight_target(t0 + 20.0 * k),
                        t0 + 20.0 * k, rng, 3.0, 0.1) for k in range(n)]


def test_confirmation_logged_with_rule_and_author(frame):
    tr = Tracker(frame=frame)
    rng = np.random.default_rng(1)
    for k in range(10):
        t = 30.0 * k
        o = ("O1", (15000.0, 2000.0)) if k % 2 == 0 else ("O2", (6000.0, -2000.0))
        tr.ingest([make_report(frame, f"R{k}", o[0], o[1], straight_target(t), t, rng, 4.0, 0.15)], now=t)
    t1 = tr.tracks["T0001"]
    assert t1.status == CONFIRMED
    conf = [j for j in tr.journal if j["event"] == "confirmare"]
    assert len(conf) == 1
    assert conf[0]["author"] == "automat (SPRT)"
    assert "SPRT" in conf[0]["rule"] and "surse independente" in conf[0]["rule"]


def test_trusted_single_observer_can_confirm(frame):
    tr = Tracker(frame=frame)
    for r in _single_observer_reports(frame):
        tr.ingest([r], now=r.t)
    t1 = tr.tracks["T0001"]
    assert t1.status == CONFIRMED
    assert "sursa unica" in t1.confirm_rule


def test_observer_with_repeated_false_reports_cannot_confirm_alone(frame):
    tr = Tracker(frame=frame)
    bad = tr.observers.get("O1")
    for _ in range(6):
        bad.record_verdict(False)
    assert bad.trust < tr.params.single_source_min_trust
    for r in _single_observer_reports(frame, n=15):
        tr.ingest([r], now=r.t)
    assert all(t.status != CONFIRMED for t in tr.tracks.values())
    assert not [j for j in tr.journal if j["event"] == "confirmare"]


def test_verdicts_update_trust(frame):
    tr = Tracker(frame=frame)
    for r in _single_observer_reports(frame):
        tr.ingest([r], now=r.t)
    o = tr.observers.get("O1")
    t0 = o.trust
    tr.resolve("T0001", False, author="op1")
    assert o.trust < t0
    v = [j for j in tr.journal if j["event"] == "verdict"][0]
    assert v["author"] == "op1" and v["observers"] == ["O1"]


def test_negative_information_lowers_tentative_score(frame):
    post_xy = straight_target(60.0)  # tinta trece prin acoperirea postului la t=60 s
    def build(with_post: bool):
        tr = Tracker(frame=frame, posts=[FixedPost("P1", post_xy, 2500.0, True, 0.7)] if with_post else [])
        reps = _single_observer_reports(frame, n=2)
        for r in reps:
            tr.ingest([r], now=r.t)
        tr.apply_negative_info(200.0)
        return tr
    a, b = build(False), build(True)
    ta, tb = a.tracks["T0001"], b.tracks["T0001"]
    assert ta.status == TENTATIVE
    assert math.isclose(tb.score, ta.score + math.log(1 - 0.7))
    assert any(j["event"] == "informatie_negativa" for j in b.journal)
    # o singura data pe post si traseu
    b.apply_negative_info(400.0)
    assert math.isclose(b.tracks["T0001"].score, tb.score)


def test_negative_information_never_from_civilian_observers(frame):
    # Doar FixedPost produce informatie negativa; un observator civil tacut nu conteaza.
    tr = Tracker(frame=frame, posts=[])
    for r in _single_observer_reports(frame, n=2):
        tr.ingest([r], now=r.t)
    s = tr.tracks["T0001"].score
    tr.observers.get("O-civil")  # observator inregistrat, fara rapoarte
    assert tr.apply_negative_info(500.0) == []
    assert tr.tracks["T0001"].score == s


def test_observer_specific_error_model_after_confirmed_tracks(frame):
    tr = Tracker(frame=frame)
    rng = np.random.default_rng(4)
    # observator foarte precis la azimut (1 grd), dar care supraestimeaza distanta cu 40%
    for trk in range(2):
        base = 100000.0 * trk
        for k in range(8):
            t = base + 30.0 * k
            truth = straight_target(30.0 * k, x0=(20000.0, 4000.0 * trk))
            good = make_report(frame, f"G{trk}{k}", "OG", (15000.0, 2500.0), truth, t, rng, 1.0, 0.02)
            good.distance_m *= 1.4
            # referinta precisa (senzor, 100 m), ca reziduurile sa reflecte observatorul, nu traseul
            lat, lon = frame.to_latlon(*(truth + rng.normal(0, 100.0, 2)))
            other = SensorDetection(f"S{trk}{k}", "RADAR", t, lat, lon, np.eye(2) * 100.0 ** 2)
            tr.ingest([good, other], now=t)
        tid = sorted(tr.tracks)[-1]
        tr.resolve(tid, True, author="op1")
    og = tr.observers.get("OG")
    assert og.own_sigma_theta is not None and og.own_sigma_theta < tr.params.sigma_theta
    assert og.own_bias_ln is not None and 0.2 < og.own_bias_ln < 0.45
    assert og.confirmed == 2
