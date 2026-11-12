"""Faza 1 - criterii de acceptare:

* un raport inserat cu intarziere da acelasi traseu ca si cum ar fi sosit la timp;
* anularea unei uniri da acelasi traseu ca si cum unirea nu ar fi existat;
* rezultate identice la reluarea acelorasi intrari.
"""
import numpy as np

from santinela import Tracker, sim
from santinela.config import DEFAULT
from santinela.imm import H
from santinela.measurements import Measurement
from santinela.track import Track

from conftest import make_report, straight_target


def _reports(frame, seed=0, n=12, observers=((15000.0, 2000.0), (5000.0, -2500.0))):
    rng = np.random.default_rng(seed)
    out = []
    for k in range(n):
        t = 30.0 * k
        oi = k % len(observers)
        out.append(make_report(frame, f"R{k:03d}", f"O{oi}", observers[oi], straight_target(t), t,
                               rng, sigma_deg=5.0, sigma_ln=0.15))
    return out


def _track_signature(tr: Track):
    return ([m.mid for m in tr.measurements], tr.state.x.copy(), tr.state.P.copy(), tr.score, tr.status)


def test_late_report_gives_same_track(frame):
    reps = _reports(frame)
    a = Tracker(frame=frame)
    for r in reps:
        a.ingest([r], now=r.t)
    late = reps[5]
    b = Tracker(frame=frame)
    for r in reps:
        if r is not late:
            b.ingest([r], now=r.t)
    b.ingest([late], now=reps[-1].t + 60)  # soseste dupa toate celelalte

    assert len(a.tracks) == len(b.tracks) == 1
    ta, tb = a.tracks["T0001"], b.tracks["T0001"]
    sa, sb = _track_signature(ta), _track_signature(tb)
    assert sa[0] == sb[0]
    assert np.array_equal(sa[1], sb[1]) and np.array_equal(sa[2], sb[2])
    assert sa[3] == sb[3] and sa[4] == sb[4]


def test_late_report_gated_against_state_at_observation_time(frame):
    reps = _reports(frame)
    tr = Tracker(frame=frame)
    for r in reps[:-1]:
        tr.ingest([r], now=r.t)
    t_track = tr.tracks["T0001"]
    m = tr.to_measurement(reps[0])
    # fata de predictia inainte (ora curenta) raportul vechi ar fi la ~16 km distanta
    fwd = H @ t_track.state.x
    assert np.linalg.norm(m.z - fwd) > 10_000
    # fata de starea la ora observarii intra in poarta
    assert tr.gate(t_track, m) is not None


def test_undo_merge_restores_exact_tracks(frame):
    tr = Tracker(frame=frame)
    reps_a = _reports(frame, seed=1)
    # a doua tinta, departe, cu alti observatori
    rng = np.random.default_rng(2)
    reps_b = [make_report(frame, f"Q{k:03d}", "P0", (0.0, 30000.0),
                          straight_target(30.0 * k, x0=(5000.0, 28000.0), v=(0.0, -40.0)), 30.0 * k,
                          rng, 5.0, 0.15) for k in range(8)]
    for k in range(12):
        batch = [reps_a[k]] + ([reps_b[k]] if k < 8 else [])
        tr.ingest(batch, now=30.0 * k)
    assert set(tr.tracks) == {"T0001", "T0002"}
    before = {tid: _track_signature(t) for tid, t in tr.tracks.items()}

    mid = tr.merge("T0001", "T0002", author="op1", automatic=True)
    assert len(tr.tracks["T0001"].measurements) == 20
    tr.undo_merge(mid, author="op1")

    after = {tid: _track_signature(t) for tid, t in tr.tracks.items()}
    for tid in before:
        assert before[tid][0] == after[tid][0]
        assert np.array_equal(before[tid][1], after[tid][1])
        assert np.array_equal(before[tid][2], after[tid][2])
        assert before[tid][3] == after[tid][3]
        assert before[tid][4] == after[tid][4]
    assert tr.merge_metrics() == {"auto_merges": 1, "undone": 1, "undo_rate": 1.0}
    events = [j["event"] for j in tr.journal]
    assert "unire" in events and "anulare_unire" in events


def test_replay_is_deterministic():
    def run():
        scn = sim.dunare()
        t = sim.make_tracker(scn)
        sim.run(scn, t, sim.generate_reports(scn, seed=5))
        return t.snapshot(), t.journal

    s1, j1 = run()
    s2, j2 = run()
    assert s1 == s2
    assert j1 == j2


def test_smoothed_track_is_more_accurate_than_filtered():
    rng = np.random.default_rng(11)
    err_f, err_s = [], []
    for run in range(30):
        tr = Track(f"T{run}", DEFAULT)
        R = np.eye(2) * 400.0 ** 2
        truth = [np.array([20000.0 - 50 * 20 * k, 300.0 * np.sin(k / 5)]) for k in range(25)]
        for k, x in enumerate(truth):
            tr.add(Measurement(f"m{k:02d}", "o", 20.0 * k, x + rng.multivariate_normal([0, 0], R), R, "sensor"))
        _, xs, _ = tr.smoothed()
        for k in range(3, 22):
            err_f.append(np.linalg.norm(tr.steps[k].state.x[:2] - truth[k]))
            err_s.append(np.linalg.norm(xs[k][:2] - truth[k]))
    assert np.mean(err_s) < 0.9 * np.mean(err_f)
