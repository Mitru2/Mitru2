"""Iesiri: model comun de traseu (identitate APP-6, SIDC), CoT corect (UID, elipsa, stale, stergere),
TAK Protocol v1 (mesh/stream) si trimitere in retea."""
import math
import socket
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import numpy as np
import pytest

from santinela import Tracker
from santinela import cot
from santinela.geo import LocalFrame
from santinela.radar import RadarPlot, RadarSite
from santinela.trackmodel import IDENTITIES, cot_type, from_track, sidc

FR = LocalFrame(45.27, 28.46)
NOW = datetime(2026, 11, 13, 10, 0, 30, tzinfo=timezone.utc)


def tracker_with_track():
    tr = Tracker(frame=FR)
    lat0, lon0 = FR.to_latlon(0.0, 0.0)
    tr.add_radar(RadarSite("R1", lat0, lon0))
    for k in range(6):
        t = 5.0 * k
        p = np.array([20000.0 - 40 * t, 3000.0])
        tr.ingest([RadarPlot(f"p{k}", "R1", t, float(np.linalg.norm(p)), math.degrees(math.atan2(p[0], p[1])))],
                  now=t)
    return tr


def test_sidc_and_cot_type():
    s = sidc("ostil")
    assert len(s) == 20 and s.startswith("10") and s[3] == "6" and s[4:6] == "01" and s[10:16] == "110300"
    assert sidc("suspect", "exercitiu")[2:4] == "15"
    assert cot_type("prieten") == "a-f-A-M-F-Q" and cot_type("in_asteptare") == "a-p-A-M-F-Q"


def test_identity_policy_and_operator_override():
    tr = tracker_with_track()
    t1 = tr.tracks["T0001"]
    assert from_track(t1, FR).identity == "suspect"          # confirmat automat
    t1.cooperative_id = "RemoteID:RO-0042"
    assert from_track(t1, FR).identity == "neutru"
    tr.set_identity("T0001", "ostil", author="op1", reason="confirmat vizual")
    ct = from_track(t1, FR)
    assert ct.identity == "ostil" and ct.identity_author == "op1" and ct.sidc[3] == "6"
    assert tr.journal[-1]["event"] == "identitate"
    with pytest.raises(ValueError):
        tr.set_identity("T0001", "dusman", author="op1")


def test_cot_xml_has_stable_uid_ellipse_stale():
    tr = tracker_with_track()
    ct = from_track(tr.tracks["T0001"], FR, instance="SANT-RO1")
    xml = cot.common_to_xml(ct, NOW, stale_s=45)
    ev = ET.fromstring(xml)
    assert ev.get("uid") == "SANT-RO1-T0001" and ev.get("type") == "a-s-A-M-F-Q"
    assert ev.get("stale") == "2026-11-13T10:01:15.000Z"
    el = ev.find("detail/shape/ellipse")
    assert float(el.get("major")) >= float(el.get("minor")) > 0
    assert ev.find("detail/__santinela").get("sidc") == ct.sidc
    # 95%: semiaxa mare = sqrt(5.991 * lambda_max)
    lam = np.linalg.eigvalsh(tr.tracks["T0001"].state.P[:2, :2]).max()
    assert float(el.get("major")) == pytest.approx(math.sqrt(5.991 * lam), rel=1e-3)


def test_delete_event():
    xml = cot.delete_xml("SANT-RO1-T0001", NOW, "a-s-A-M-F-Q")
    ev = ET.fromstring(xml)
    assert ev.get("type") == "t-x-d-d"
    assert ev.find("detail/link").get("uid") == "SANT-RO1-T0001"
    assert ev.find("detail/__forcedelete") is not None


@pytest.mark.parametrize("mode", ["mesh", "stream"])
def test_tak_protocol_v1_round_trip(mode):
    tr = tracker_with_track()
    xml = cot.common_to_xml(from_track(tr.tracks["T0001"], FR), NOW)
    data = cot.xml_to_tak(xml, mode)
    assert data[:3] == b"\xbf\x01\xbf" if mode == "mesh" else data[:1] == b"\xbf"
    back = ET.fromstring(cot.tak_to_xml(data))
    orig = ET.fromstring(xml)
    assert back.get("uid") == orig.get("uid") and back.get("type") == orig.get("type")
    assert back.get("stale") == orig.get("stale")
    assert float(back.find("point").get("lat")) == pytest.approx(float(orig.find("point").get("lat")))
    assert float(back.find("detail/track").get("speed")) == pytest.approx(float(orig.find("detail/track").get("speed")))
    assert back.find("detail/shape/ellipse") is not None      # detaliile netipizate trec prin xmlDetail
    assert back.find("detail/contact").get("callsign") == "DRONA T0001"


def test_publisher_updates_then_deletes_over_udp():
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    rx.settimeout(2)
    sender = cot.CotSender("udp", "127.0.0.1", rx.getsockname()[1], protocol="xml")
    tr = tracker_with_track()
    pub = cot.CotPublisher(sender, FR, instance="SANT")
    assert pub.publish(tr, NOW) == {"actualizari": ["SANT-T0001"], "stergeri": []}
    ev = ET.fromstring(rx.recv(65536).decode())
    assert ev.get("uid") == "SANT-T0001"
    tr.tracks["T0001"].manual_status = "sters"
    assert pub.publish(tr, NOW) == {"actualizari": [], "stergeri": ["SANT-T0001"]}
    ev = ET.fromstring(rx.recv(65536).decode())
    assert ev.get("type") == "t-x-d-d" and ev.find("detail/link").get("uid") == "SANT-T0001"
    assert pub.publish(tr, NOW)["stergeri"] == []           # stergerea se trimite o singura data
    sender.close()
    rx.close()


def test_tcp_sender_tak_stream():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    sender = cot.CotSender("tcp", "127.0.0.1", srv.getsockname()[1], protocol="tak")
    conn, _ = srv.accept()
    tr = tracker_with_track()
    sender.send(cot.common_to_xml(from_track(tr.tracks["T0001"], FR), NOW))
    data = conn.recv(65536)
    assert data[:1] == b"\xbf"
    assert ET.fromstring(cot.tak_to_xml(data)).get("uid") == "SANTINELA-T0001"
    sender.close()
    conn.close()
    srv.close()


def test_all_identities_have_codes():
    for ident in IDENTITIES:
        assert len(sidc(ident)) == 20
