"""Integrarea radarelor: ASTERIX CAT048, detectii polare cu Doppler, trasee radar (CI),
acoperire dependenta de altitudine si inregistrarea erorilor sistematice."""
import math
import struct

import numpy as np
import pytest

from santinela import Tracker, CONFIRMED, TENTATIVE, sim
from santinela.asterix import AsterixError, Cat048Adapter, decode_records, encode_block
from santinela.radar import RadarPlot, RadarSite, RadarTrackReport, convert_plot, radar_horizon

from conftest import make_report


def site(frame, **kw):
    lat, lon = frame.to_latlon(0.0, 0.0)
    args = dict(sensor_id="R1", lat=lat, lon=lon, antenna_height=20.0, max_range=60000.0, min_alt=300.0,
                sac=1, sic=7)
    args.update(kw)
    return RadarSite(**args).attach(frame)


# ---------------------------------------------------------------- ASTERIX
def test_cat048_hand_built_record():
    # Octeti construiti manual dupa specificatie, independent de encoder:
    # FSPEC 0xD0 = FRN 1 (I048/010), 2 (I048/140), 4 (I048/040)
    rec = bytes([0xD0, 1, 7]) + (36000 * 128).to_bytes(3, "big") + struct.pack(">HH", 10 * 256, 16384)
    block = bytes([48]) + struct.pack(">H", 3 + len(rec)) + rec
    r = decode_records(block)[0]
    assert (r["sac"], r["sic"]) == (1, 7)
    assert r["tod"] == 36000.0
    assert r["range_m"] == pytest.approx(10 * 1852.0)
    assert r["azimuth_deg"] == pytest.approx(90.0)


def test_cat048_skips_items_it_does_not_use():
    # Inregistrare cu I048/070 (fix), I048/130 (compus) si I048/250 (repetitiv), urmata de una normala.
    fs = bytes([0x80 | 0x40 | 0x08 | 0x02 | 0x01, 0x20])  # FRN 1, 2, 5, 7 + extensie; FRN 10
    body = (bytes([1, 7]) + (100 * 128).to_bytes(3, "big") + b"\x0f\xff"
            + bytes([0b10100000, 0x11, 0x22])            # I048/130: subcampurile 1 si 3
            + bytes([2]) + bytes(16))                     # I048/250: 2 repetitii x 8 octeti
    rec2 = bytes([0xD0, 1, 7]) + (101 * 128).to_bytes(3, "big") + struct.pack(">HH", 512, 0)
    recs = fs + body + rec2
    block = bytes([48]) + struct.pack(">H", 3 + len(recs)) + recs
    out = decode_records(block)
    assert len(out) == 2
    assert out[0]["items"] == ["010", "140", "070", "130", "250"]
    assert out[1]["range_m"] == pytest.approx(2 * 1852.0)


def test_cat048_ignores_other_categories_and_rejects_truncated():
    other = bytes([62, 0, 5, 0xAA, 0xBB])
    good = encode_block([{"sac": 1, "sic": 7, "tod": 10.0, "range_m": 5000.0, "azimuth_deg": 45.0}])
    assert len(decode_records(other + good)) == 1
    with pytest.raises(AsterixError):
        decode_records(good[:-2])


def test_cat048_round_trip_doppler_height_track(frame):
    rs = site(frame)
    blk = encode_block([
        {"sac": 1, "sic": 7, "tod": 36010.5, "typ": 1, "range_m": 25000.0, "azimuth_deg": 300.0,
         "height_m": 1200.0, "doppler_mps": -37.0},
        {"sac": 1, "sic": 7, "tod": 36011.0, "typ": 1, "track_number": 42, "x_m": -5000.0, "y_m": 8000.0,
         "groundspeed_mps": 41.0, "heading_deg": 250.0, "track_confirmed": True,
         "sigma_x_m": 60.0, "sigma_y_m": 60.0, "sigma_v_mps": 1.5, "sigma_h_deg": 3.0},
        {"sac": 9, "sic": 9, "tod": 36011.0, "range_m": 1000.0, "azimuth_deg": 0.0},  # radar necunoscut
    ])
    plots = Cat048Adapter([rs], tod_ref=36000.0, mode="plots").decode(blk)
    p = plots[0]
    assert isinstance(p, RadarPlot)
    assert p.t == pytest.approx(10.5)
    assert p.range_m == pytest.approx(25000.0, abs=4.0)        # LSB 1/256 NM ~ 7 m
    assert p.azimuth_deg == pytest.approx(300.0, abs=0.006)
    assert p.height_m == pytest.approx(1200.0, abs=4.0)        # LSB 25 ft
    assert p.rr == -37.0
    ad = Cat048Adapter([rs], tod_ref=36000.0, mode="tracks")
    tracks = [x for x in ad.decode(blk) if isinstance(x, RadarTrackReport)]
    assert ad.skipped == 1
    t = tracks[0]
    assert t.track_number == 42
    assert (t.x, t.y) == (pytest.approx(-5000.0, abs=8), pytest.approx(8000.0, abs=8))
    assert math.hypot(t.vx, t.vy) == pytest.approx(41.0, abs=0.2)
    assert t.cov.shape == (4, 4)


def test_tod_wraps_past_midnight(frame):
    ad = Cat048Adapter([site(frame)], tod_ref=86390.0)
    p = ad.decode(encode_block([{"sac": 1, "sic": 7, "tod": 5.0, "range_m": 1000.0, "azimuth_deg": 0.0}]))[0]
    assert p.t == pytest.approx(15.0)


# ---------------------------------------------------------------- acoperire
def test_radar_horizon_and_altitude_dependent_coverage(frame):
    rs = site(frame)
    assert radar_horizon(20.0, 30.0) == pytest.approx(4120 * (math.sqrt(20) + math.sqrt(30)))
    assert rs.coverage_radius(None) is None          # altitudine necunoscuta -> "foarte jos"
    assert rs.coverage_radius(50.0) is None          # sub altitudinea minima
    assert rs.coverage_radius(300.0) == 60000.0      # orizontul (~90 km) depaseste raza maxima
    far = site(frame, max_range=200000.0)
    assert far.coverage_radius(300.0) == pytest.approx(radar_horizon(20.0, 300.0))
    assert far.coverage_radius(1000.0) == pytest.approx(radar_horizon(20.0, 1000.0))  # ~149 km


def _tracker_with_track(frame, altitude_cat, rs):
    tr = Tracker(frame=frame, radars=[rs])
    rng = np.random.default_rng(0)
    for k in range(2):
        t = 20.0 * k
        r = make_report(frame, f"R{k}", "O1", (16000.0, 1500.0), (20000.0 - 50 * t, 0.0), t, rng, 3.0, 0.1)
        r.altitude_cat = altitude_cat
        tr.ingest([r], now=t)
    return tr


@pytest.mark.parametrize("cat,penalized", [("mediu", True), ("inalt", True), ("foarte jos", False),
                                           ("jos", False), (None, False)])
def test_radar_silence_is_negative_info_only_above_min_altitude(frame, cat, penalized):
    rs = site(frame)
    tr = _tracker_with_track(frame, cat, rs)
    s0 = tr.tracks["T0001"].score
    applied = tr.apply_negative_info(100.0)
    s1 = tr.tracks["T0001"].score
    assert bool(applied) == penalized
    if penalized:
        assert s1 == pytest.approx(s0 + math.log(1 - rs.pd))
    else:
        assert s1 == s0


def test_later_very_low_report_removes_radar_penalty(frame):
    rs = site(frame)
    tr = _tracker_with_track(frame, "mediu", rs)
    s0 = tr.tracks["T0001"].score
    tr.apply_negative_info(100.0)
    assert tr.tracks["T0001"].score < s0
    # un al treilea raport spune "foarte jos": la re-filtrare penalizarea radarului nu se mai aplica
    rng = np.random.default_rng(1)
    r = make_report(frame, "R9", "O2", (12000.0, -1500.0), (20000.0 - 50 * 40.0, 0.0), 40.0, rng, 3.0, 0.1)
    r.altitude_cat = "foarte jos"
    tr.ingest([r], now=120.0)
    neg = [e for e in tr.tracks["T0001"].entries if getattr(e, "post_id", None) == "R1"]
    assert neg  # evenimentul ramane in tampon (trasabilitate) ...
    assert "R1" not in tr.tracks["T0001"].negative_applied  # ... dar nu mai scade scorul


# ---------------------------------------------------------------- masuratori
def test_plot_conversion_with_height(frame):
    rs = site(frame)
    p = RadarPlot("p", "R1", 0.0, range_m=math.hypot(30000.0, 1180.0), azimuth_deg=90.0, height_m=1200.0)
    m = convert_plot(p, rs)
    assert m.z == pytest.approx([30000.0, 0.0], abs=2.0)
    # incertitudine transversala ~ r * sigma_az, radiala ~ sigma_range
    assert math.sqrt(m.R[1, 1]) == pytest.approx(30000 * math.radians(0.5), rel=0.02)
    assert math.sqrt(m.R[0, 0]) == pytest.approx(50.0, rel=0.05)


def test_doppler_improves_velocity_estimate(frame):
    rs = site(frame)
    v = np.array([-35.0, 25.0])
    errs = {True: [], False: []}
    for run in range(20):
        rng = np.random.default_rng(run)
        for use_rr in (True, False):
            tr = Tracker(frame=frame, radars=[rs])
            rng2 = np.random.default_rng(run)
            for k in range(6):
                t = 5.0 * k
                p = np.array([25000.0, 10000.0]) + v * t
                rng_ = float(np.linalg.norm(p))
                az = math.degrees(math.atan2(p[0], p[1])) + rng2.normal(0, 0.5)
                rr = float(p @ v) / rng_ + rng2.normal(0, 1.0)
                tr.ingest([RadarPlot(f"p{k}", "R1", t, rng_ + rng2.normal(0, 50.0), az,
                                     rr if use_rr else None)], now=t)
            x = tr.tracks["T0001"].state.x
            los = x[:2] / np.linalg.norm(x[:2])
            errs[use_rr].append(abs(float((x[2:] - v) @ los)))
    # Doppler masoara componenta radiala a vitezei: eroarea ei scade puternic
    assert np.mean(errs[True]) < 0.5 * np.mean(errs[False])


def test_radar_tracks_fused_with_ci_keep_one_local_track_through_a_turn(frame):
    rs = site(frame)
    tr = Tracker(frame=frame, radars=[rs])
    rng = np.random.default_rng(3)
    pos = np.array([20000.0, 5000.0])
    v = np.array([-40.0, 0.0])
    for k in range(60):
        t = 5.0 * k
        if k == 30:
            v = np.array([0.0, -40.0])  # viraj de 90 de grade
        pos = pos + v * 5.0
        z = pos + rng.normal(0, 50, 2)
        C = np.diag([60.0 ** 2, 60.0 ** 2, 2.0 ** 2, 2.0 ** 2])
        tr.ingest([RadarTrackReport(f"t{k}", "R1", 17, t, z[0], z[1], v[0], v[1], C)], now=t)
    assert list(tr.tracks) == ["T0001"]
    t1 = tr.tracks["T0001"]
    assert len(t1.measurements) == 60
    assert np.linalg.norm(t1.state.x[:2] - pos) < 200
    assert np.linalg.norm(t1.state.x[2:] - v) < 5
    # CI e prudent: covarianta nu scade sub cea a unei singure actualizari radar
    assert np.trace(t1.state.P[:2, :2]) >= 0.5 * (60.0 ** 2) * 2 * 0.5


def test_radar_with_false_verdicts_cannot_confirm_alone(frame):
    rs = site(frame)

    def run(bad: bool):
        tr = Tracker(frame=frame, radars=[rs])
        if bad:
            for _ in range(6):
                tr.observers.get("R1").record_verdict(False)
        for k in range(8):
            t = 5.0 * k
            p = np.array([20000.0 - 40 * t, 3000.0])
            tr.ingest([RadarPlot(f"p{k}", "R1", t, float(np.linalg.norm(p)),
                                 math.degrees(math.atan2(p[0], p[1])))], now=t)
        return tr.tracks["T0001"].status

    assert run(False) == CONFIRMED
    assert run(True) == TENTATIVE


# ---------------------------------------------------------------- inregistrare
def _cooperative_triples(rs, truth_bias, n=120, seed=0):
    """Tinta cooperativa (ADS-B, ~15 m): o trecere radiala si una transversala.

    Pe un cerc in jurul radarului eroarea de azimut si decalajul de ceas ar fi
    inseparabile (ambele muta plot-ul de-a lungul traiectoriei); e nevoie de
    componenta radiala a miscarii.
    """
    rng = np.random.default_rng(seed)
    b_az, b_r, c = truth_bias
    triples = []
    for k in range(n):
        t = 10.0 * k
        if k < n // 2:   # radial, spre radar, pe azimutul 40
            u = np.array([math.sin(math.radians(40)), math.cos(math.radians(40))])
            p = rs.center + u * (50000.0 - 70.0 * t)
            v = -70.0 * u
        else:            # transversal, la 25 km nord
            p = rs.center + np.array([-30000.0 + 70.0 * (t - 600.0), 25000.0])
            v = np.array([70.0, 0.0])
        # radarul marcheaza cu ora t_s = t_real - c
        t_s = t - c
        d = p - rs.center
        plot = RadarPlot(f"c{k}", rs.sensor_id, t_s,
                         float(np.linalg.norm(d)) + b_r + rng.normal(0, rs.sigma_range),
                         math.degrees(math.atan2(d[0], d[1])) + math.degrees(b_az) + rng.normal(0, rs.sigma_az_deg))
        ref_pos = p - v * c + rng.normal(0, 15.0, 2)  # pozitia ADS-B la ora marcata
        triples.append((plot, np.concatenate([ref_pos, v]), np.eye(2) * 15.0 ** 2))
    return triples


def test_registration_recovers_biases_from_cooperative_target(frame):
    rs = site(frame)
    tr = Tracker(frame=frame, radars=[rs])
    truth = (math.radians(1.0), 120.0, 0.8)
    assert tr.register_radar("R1", _cooperative_triples(rs, truth), n_ref=120, source="ADS-B")
    reg = tr.registrations["R1"]
    assert math.degrees(reg.bias_az) == pytest.approx(1.0, abs=0.1)
    assert reg.bias_range == pytest.approx(120.0, abs=15.0)
    assert reg.clock_offset == pytest.approx(0.8, abs=0.3)
    # corectia se aplica detectiilor noi
    d = np.array([10000.0, 20000.0])
    raw = RadarPlot("x", "R1", 50.0, float(np.linalg.norm(d)) + 120.0,
                    math.degrees(math.atan2(d[0], d[1])) + 1.0)
    m = tr.to_measurement(raw)
    assert np.linalg.norm(m.z - (rs.center + d)) < 40.0
    assert m.t == pytest.approx(50.0 + reg.clock_offset)
    assert any(j["event"] == "inregistrare_radar" and j["source"] == "ADS-B" for j in tr.journal)


def test_registration_refused_with_weak_reference():
    scn = sim.dunare()
    tr = sim.make_tracker(scn)
    sim.run(scn, tr, sim.generate_reports(scn, seed=1))
    d = [t for t in tr.tracks.values() if t.drone_class == "orlan-10" and t.status == CONFIRMED][0]
    tr.resolve(d.track_id, True, author="op1")
    reg = tr.registrations["R-Dunare"]
    assert not reg.estimated and reg.bias_az == 0.0
    assert any(j["event"] == "inregistrare_radar_insuficienta" for j in tr.journal)


# ---------------------------------------------------------------- scenariul "Dunare"
@pytest.mark.parametrize("mode", ["plots", "tracks"])
def test_dunare_radar_sees_only_medium_altitude_target(mode):
    for seed in (1, 2):
        scn = sim.dunare(radar_mode=mode)
        tr = sim.make_tracker(scn)
        sim.run(scn, tr, sim.generate_reports(scn, seed=seed))
        confirmed = [t for t in tr.tracks.values() if t.status == CONFIRMED and t.group_id is None]
        assert len(confirmed) == 4
        with_radar = [t for t in confirmed if "R-Dunare" in t.sources]
        assert [t.drone_class for t in with_radar] == ["orlan-10"]
        # tintele foarte joase nu sunt penalizate de tacerea radarului
        low = {t.track_id for t in confirmed if t.drone_class != "orlan-10"}
        assert not [j for j in tr.journal if j["event"] == "informatie_negativa"
                    and j.get("post") == "R-Dunare" and j["track"] in low]
