"""DIS (IEEE 1278.1, Entity State) si CAP 1.2 (alerte pentru protectia civila).

DIS: PDU-ul de test din open-dis-python (tests/EntityStatePdu-26.raw, BSD-2-Clause, (c) 2023 Open DIS)
cu valorile asteptate de testul lor; capturile publice Wireshark DIS_EntityState_1/2 (cu
SANTINELA_REFERENCE). CAP: alertele trec validarea pe schema oficiala OASIS CAP-v1.2.xsd (cu
SANTINELA_REFERENCE si lxml) si regulile din text pe care schema nu le exprima.
"""
import math
import os
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from santinela import cap, dis, pcap, sim
from santinela.geo import LocalFrame
from santinela.radar import RadarTrackReport
from santinela.speeds import speed_table_from_site
from santinela.track import CONFIRMED
from santinela.tracker import Tracker
from santinela.universal import UniversalAdapter, detect_format

REF = os.environ.get("SANTINELA_REFERENCE")
needs_ref = pytest.mark.skipif(not REF, reason="SANTINELA_REFERENCE nu e setat (--fetch-reference)")
FR = LocalFrame(45.27, 28.46)
TOD0 = 36000.0

OPEN_DIS_PDU = bytes.fromhex(
    "06070101001e91e200900000002a0004001a010001010027070201000101002707020100000000000000000000000000"
    "4150af90b382c24a4139725ff511aedc41507913179c1a083ff7afa180000000c0146e60002000000200000000000000"
    "000000000000000000000000000000000000000000000000000000000000000001323600000000000000000000000000")


# ---------------------------------------------------------------- DIS
def test_open_dis_reference_pdu():
    e = dis.decode(OPEN_DIS_PDU)[0]
    assert (e.version, e.exercise, e.site, e.application, e.entity, e.force) == (6, 7, 42, 4, 26, 1)
    assert e.entity_type == (1, 1, 39, 7, 2, 1, 0) and e.n_variable == 0 and e.marking == "26"
    assert e.velocity_ecef == (0.0, 0.0, 0.0)
    assert e.location_ecef == pytest.approx((4374082.80485589, 1667679.95730107, 4318284.36890269), abs=1e-6)
    assert e.orientation[0] == pytest.approx(1.93505, abs=1e-5) and e.orientation[2] == pytest.approx(-2.31924, abs=1e-5)
    assert (e.kind, e.domain, e.force_name) == ("platforma", "terestru", "prieten")


def test_wgs84_conversions():
    assert dis.geodetic_to_ecef(0, 0, 0) == pytest.approx((6378137.0, 0.0, 0.0))
    assert dis.geodetic_to_ecef(90, 0, 0) == pytest.approx((0.0, 0.0, 6356752.314245), abs=1e-5)
    rnd = random.Random(4)
    for _ in range(200):
        lat, lon, h = rnd.uniform(-89.9, 89.9), rnd.uniform(-180, 180), rnd.uniform(-500, 20000)
        la, lo, hh = dis.ecef_to_geodetic(*dis.geodetic_to_ecef(lat, lon, h))
        assert (la, lo, hh) == (pytest.approx(lat, abs=1e-9), pytest.approx(lon, abs=1e-9), pytest.approx(h, abs=1e-3))
        v = dis.enu_to_ecef(lat, lon, 3.0, -4.0, 5.0)
        assert dis.ecef_to_enu(lat, lon, v) == pytest.approx((3.0, -4.0, 5.0))


def test_round_trip_multi_pdu_datagram_and_detection():
    pdus = [dis.encode_entity_state(1, 2, k, 45.3 + k / 100, 28.5, 300.0, (20.0, -10.0, 0.0), marking=f"UAV-{k}",
                                    seconds_past_hour=600.0 + k) for k in range(3)]
    buf = b"".join(pdus)
    assert detect_format(buf) == "dis" and detect_format(pdus[0]) == "dis"
    es = dis.decode(buf)
    assert [e.marking for e in es] == ["UAV-0", "UAV-1", "UAV-2"]
    lat, lon, h = es[1].geodetic()
    assert (lat, lon, h) == (pytest.approx(45.31), pytest.approx(28.5), pytest.approx(300.0, abs=1e-3))
    assert es[2].velocity_enu() == pytest.approx((20.0, -10.0, 0.0), abs=1e-4)
    assert es[0].seconds_past_hour == pytest.approx(600.0, abs=1e-5) and es[0].absolute_time
    with pytest.raises(dis.DisError):
        dis.decode(buf[:-5])
    assert not dis.looks_like_dis(buf[:-5])


def _exercise_feed(ad, n=12, step=5.0):
    out = []
    for k in range(n):
        lat, lon = FR.to_latlon(8000.0 - 40.0 * step * k, 1000.0)
        p = dis.encode_entity_state(1, 2, 7, lat, lon, 400.0, (-40.0, 0.0, 0.0), force=2,
                                    marking="OPFOR-UAV", seconds_past_hour=(TOD0 % 3600) + step * k)
        out += ad.feed(p, received_at=step * k, source="udp:3000")
    return out


def test_dis_is_only_reported_unless_exercise_mode():
    ad = UniversalAdapter(frame=FR, tod_ref=TOD0)
    assert _exercise_feed(ad, n=2) == []
    assert any("dis_exercise=True" in r for r in ad.requirements())


def test_dis_exercise_targets_become_tracks_and_friends_teams():
    tr = Tracker(frame=FR)
    ad = UniversalAdapter(frame=FR, tod_ref=TOD0, tracker=tr, dis_exercise=True)
    out = _exercise_feed(ad)
    assert len(out) == 12 and all(isinstance(o, RadarTrackReport) for o in out)
    assert out[0].vx == pytest.approx(-40.0, abs=1e-3) and out[0].t == pytest.approx(0.0)
    from santinela import geoid
    lat, lon = FR.to_latlon(8000.0, 1000.0)
    assert out[0].height_m == pytest.approx(400.0 - geoid.undulation(lat, lon), abs=0.01)
    assert ad._by_name["DIS-EX1"].kind == "exercitiu"
    for k in range(0, 12, 3):
        tr.ingest(out[k:k + 3], now=out[k + 2].t)
    conf = [t for t in tr.tracks.values() if t.status == CONFIRMED]
    assert len(conf) == 1 and conf[0].sources == {"DIS-EX1"}
    friend = dis.encode_entity_state(1, 2, 99, 45.25, 28.40, 90.0, force=1, entity_type=(3, 1, 0, 0, 0, 0, 0),
                                     marking="ECHIPA-1", seconds_past_hour=TOD0 % 3600)
    land = dis.encode_entity_state(1, 2, 98, 45.25, 28.41, 90.0, force=2, entity_type=(1, 1, 0, 0, 0, 0, 0))
    assert ad.feed(friend + land, received_at=1.0) == []
    assert ad.friendly["DIS-1-2-99"].name == "ECHIPA-1"
    assert any("ignorate" in n for n in ad.sources["DIS-EX1"].notes)


@needs_ref
def test_wireshark_dis_captures():
    counts, names = [], []
    for f in ("DIS_EntityState_1.pcapng", "DIS_EntityState_2.pcapng"):
        ad = UniversalAdapter(frame=FR, tod_ref=0.0, dis_exercise=True)
        n = 0
        for d in pcap.read(Path(REF) / f):
            assert detect_format(d.payload) == "dis"
            for e in dis.decode(d.payload):
                n += 1
                assert e.entity_type == (3, 1, 225, 3, 0, 1, 0) and e.force == 1
                lat, lon, h = e.geodetic()
                assert abs(h) < 2000.0
            ad.feed(d.payload, received_at=0.0)
        counts.append(n)
        names.append({k: v.name for k, v in ad.friendly.items()})
    assert counts == [287, 20]
    assert names[0] == {"DIS-1-3001-3": "KARL"}
    assert {f"Ent-{k}" for k in range(1, 11)} <= set(names[1].values())


# ---------------------------------------------------------------- CAP
SENT = datetime(2026, 11, 16, 10, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def scenario():
    scn = sim.dunare()
    tr = sim.make_tracker(scn, speeds=speed_table_from_site(None))
    items = sim.generate_reports(scn, seed=1)
    sim.run(scn, tr, items)
    return tr, max(i.received_at for i in items)


def _xsd():
    etree = pytest.importorskip("lxml.etree")
    if not REF:
        pytest.skip("SANTINELA_REFERENCE nu e setat (schema CAP-v1.2.xsd)")
    return etree, etree.XMLSchema(etree.parse(str(Path(REF) / "CAP-v1.2.xsd")))


def test_cap_rules_and_content(scenario):
    tr, now = scenario
    alerts = cap.alerts_for(tr, now, SENT, "santinela.exercitiu", "Exercise")
    assert len(alerts) == 4
    for x in alerts.values():
        assert cap.check(x) == []
        assert "<sent>2026-11-16T10:00:00-00:00</sent>" in x and "<expires>2026-11-16T10:15:00-00:00</expires>" in x
        assert x.count("<polygon>") == 4                       # elipsa curenta + T+5/10/15
        assert "<scope>Restricted</scope>" in x and "<status>Exercise</status>" in x
        assert "<value>1015" in x                             # SIDC in context de exercitiu
    tid = sorted(alerts)[0]
    tr.set_identity(tid, "ostil", author="operator-1", reason="test")
    x = cap.track_alert(tr.tracks[tid], tr.frame, SENT, "santinela.exercitiu", "Exercise", now)
    assert "<responseType>Shelter</responseType>" in x and "<severity>Severe</severity>" in x
    assert "<event>Drona ostila</event>" in x


def test_cap_guards():
    ok = SENT
    with pytest.raises(cap.CapError):
        cap.cap_time(ok.replace(tzinfo=None))
    assert cap.cap_time(ok.astimezone(timezone(timedelta(hours=2)))) == "2026-11-16T12:00:00+02:00"
    tr = Tracker(frame=FR)
    fake = type("T", (), {})
    with pytest.raises(cap.CapError):
        cap.track_alert(fake, tr.frame, ok, "santinela", "Real")
    with pytest.raises(cap.CapError):
        cap.track_alert(fake, tr.frame, ok, "santinela ro", "Exercise")
    with pytest.raises(cap.CapError):
        cap.track_alert(fake, tr.frame, ok, "santinela", "Exercise", msg_type="Update")
    bad = "<alert><sent>2026-11-16T10:00:00Z</sent><polygon>45,28 45.1,28 45,28</polygon></alert>"
    assert len(cap.check(bad)) == 2


def test_cap_validates_against_official_schema(scenario):
    etree, schema = _xsd()
    tr, now = scenario
    alerts = cap.alerts_for(tr, now, SENT, "santinela.exercitiu", "Exercise")
    tid = sorted(alerts)[0]
    upd = cap.track_alert(tr.tracks[tid], tr.frame, SENT + timedelta(minutes=1), "santinela.exercitiu", "Exercise",
                          now, msg_type="Update",
                          references=[f"santinela.exercitiu,SANTINELA-{tid}-20261116T100000,{cap.cap_time(SENT)}"])
    cancel = cap.cancel_alert(f"SANTINELA-{tid}-20261116T100000", SENT, "santinela.exercitiu",
                              SENT + timedelta(minutes=2), "Exercise")
    for x in list(alerts.values()) + [upd, cancel]:
        assert schema.validate(etree.fromstring(x.encode())), schema.error_log.last_error
    broken = list(alerts.values())[0].replace("<urgency>Immediate</urgency>", "<urgency>Acum</urgency>")
    assert not schema.validate(etree.fromstring(broken.encode()))
    assert math.isfinite(now)
