"""SANTINELA ca nod de fuziune SAPIENT: Registration/Ack, detectii, Alert/Ack, Task/TaskAck, TCP, nod-copil.

Pe schema-subset din tests/data (numere de camp oficiale). Daca SANTINELA_SAPIENT_PROTO indica
directorul cu fisierele oficiale Dstl (verificate pe manifest), ruleaza si testele pe schema completa.
"""
import math
import os
import socket
from pathlib import Path

import numpy as np
import pytest

from santinela import Tracker
from santinela.geo import LocalFrame
from santinela.protowire import ProtoSchema
from santinela.radar import RadarPlot, RadarSite, RadarTrackReport
from santinela.sapient_node import FrameReader, SapientFusionNode, SapientServer, frame, ulid
from santinela.universal import UniversalAdapter

FR = LocalFrame(45.27, 28.46)
TOD0 = 36000.0
EPOCH0 = 1794477600.0  # 2026-11-12 10:00:00 UTC
SENSOR = "6f1c2e0a-3b5d-4c7e-9f10-2a3b4c5d6e7f"
SUBSET = Path(__file__).parent / "data"
OFFICIAL = os.environ.get("SANTINELA_SAPIENT_PROTO")


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def make_node(schema, strict=False):
    counter = iter(range(1, 10 ** 6))
    ad = UniversalAdapter(frame=FR, tod_ref=TOD0)
    node = SapientFusionNode(schema, ad, node_id="11111111-2222-4333-8444-555555555555", strict=strict,
                             clock=Clock(EPOCH0 + 10), rand=lambda: next(counter).to_bytes(10, "big"))
    return node, ad


def ts(epoch):
    return {"seconds": int(epoch), "nanos": 0}


def detection(lat, lon, obj, epoch=EPOCH0 + 10):
    return {"timestamp": ts(epoch), "node_id": SENSOR,
            "detection_report": {"report_id": ulid(int(epoch * 1000), b"\x01" * 10), "object_id": obj,
                                 "location": {"x": lon, "y": lat, "coordinate_system": 1, "datum": 1},
                                 "enu_velocity": {"east_rate": -20.0, "north_rate": 0.0}}}


def test_ulid_format_and_order():
    a, b = ulid(1000, bytes(10)), ulid(2000, bytes(10))
    assert len(a) == 26 and a < b and set(a) <= set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")


def test_frame_reader_reassembles_split_stream():
    fr = FrameReader()
    data = frame(b"abc") + frame(b"defgh")
    out = fr.feed(data[:5]) + fr.feed(data[5:9]) + fr.feed(data[9:])
    assert out == [b"abc", b"defgh"]
    with pytest.raises(ValueError):
        FrameReader(max_size=10).feed((100).to_bytes(4, "little"))


def test_registration_ack_tolerant_and_strict():
    sch = ProtoSchema.from_dir(SUBSET)
    reg = {"timestamp": ts(EPOCH0), "node_id": SENSOR,
           "registration": {"node_definition": [{"node_type": "NODE_TYPE_RADAR"}]}}  # lipseste icd_version
    node, _ = make_node(sch)
    ack = node.handle(reg)[0]
    assert ack["registration_ack"] == {"acceptance": True}
    assert ack["destination_id"] == SENSOR and node.nodes[SENSOR]["issues"]
    strict, _ = make_node(sch, strict=True)
    ack = strict.handle(reg)[0]["registration_ack"]
    assert ack["acceptance"] is False and any("icd_version" in r for r in ack["ack_response_reason"])
    assert sch.validate("SapientMessage", node.handle(reg)[0]) == []  # raspunsul nostru e valid


def test_detection_alert_task_flow():
    sch = ProtoSchema.from_dir(SUBSET)
    node, ad = make_node(sch)
    # detectie inainte de inregistrare -> Error, dar detectia e folosita
    lat, lon = FR.to_latlon(4000.0, 1000.0)
    r = node.handle(detection(lat, lon, "01JCX3T4ZQ8V7M2N5P6R9S0T1W"))
    assert "error" in r[0] and node.take_items()
    node.handle({"timestamp": ts(EPOCH0), "node_id": SENSOR, "registration": {
        "node_definition": [{"node_type": "NODE_TYPE_CAMERA"}], "icd_version": "BSI Flex 335 v2.0"}})
    assert node.handle(detection(lat, lon, "01JCX3T4ZQ8V7M2N5P6R9S0T1W")) == []
    items = node.take_items()
    assert len(items) == 1 and isinstance(items[0], RadarTrackReport) and items[0].t == pytest.approx(10.0)
    alert_id = ulid(int(EPOCH0 * 1000), b"\x02" * 10)
    ack = node.handle({"timestamp": ts(EPOCH0), "node_id": SENSOR,
                       "alert": {"alert_id": alert_id, "description": "drona la poarta"}})[0]
    assert ack["alert_ack"] == {"alert_id": alert_id, "alert_ack_status": "ALERT_ACK_STATUS_ACCEPTED"}
    assert ad.sapient_alerts[0]["description"] == "drona la poarta"
    task = node.task_look_at(SENSOR, 45.3, 28.5, 120.0)
    tid = task["task"]["task_id"]
    assert sch.validate("SapientMessage", task) == []
    assert task["task"]["command"]["look_at"]["location_list"]["locations"][0]["x"] == 28.5  # x = longitudine
    node.handle({"timestamp": ts(EPOCH0), "node_id": SENSOR,
                 "task_ack": {"task_id": tid, "task_status": "TASK_STATUS_ACCEPTED"}})
    assert node.tasks[tid]["status"] == "TASK_STATUS_ACCEPTED"


def test_tcp_server_end_to_end():
    sch = ProtoSchema.from_dir(SUBSET)
    node, _ = make_node(sch)
    srv = SapientServer(node, "127.0.0.1", 0)
    port = srv.start()
    try:
        reg = sch.encode("SapientMessage", {"timestamp": ts(EPOCH0), "node_id": SENSOR, "registration": {
            "node_definition": [{"node_type": "NODE_TYPE_RADAR"}], "icd_version": "BSI Flex 335 v2.0"}})
        lat, lon = FR.to_latlon(4000.0, 1000.0)
        det = sch.encode("SapientMessage", detection(lat, lon, "01JCX3T4ZQ8V7M2N5P6R9S0T1W"))
        with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
            s.sendall(frame(reg) + frame(det)[:7])     # al doilea mesaj fragmentat
            s.sendall(frame(det)[7:])
            fr = FrameReader()
            got = []
            while not got:
                got = fr.feed(s.recv(65536))
            ack = sch.decode("SapientMessage", got[0])
            assert ack["registration_ack"]["acceptance"] is True
            # sarcina trimisa de SANTINELA catre senzor pe aceeasi conexiune
            assert srv.send(SENSOR, node.task_look_at(SENSOR, 45.3, 28.5))
            got = []
            while not got:
                got = fr.feed(s.recv(65536))
            assert "task" in sch.decode("SapientMessage", got[0])
        for _ in range(50):
            if node.items:
                break
            import time
            time.sleep(0.02)
        assert len(node.take_items()) == 1
    finally:
        srv.stop()


def _track():
    tr = Tracker(frame=FR)
    lat0, lon0 = FR.to_latlon(0.0, 0.0)
    tr.add_radar(RadarSite("R1", lat0, lon0))
    for k in range(6):
        t = 5.0 * k
        p = np.array([20000.0 - 40 * t, 3000.0])
        tr.ingest([RadarPlot(f"p{k}", "R1", t, float(np.linalg.norm(p)), math.degrees(math.atan2(p[0], p[1])))],
                  now=t)
    return tr.tracks["T0001"]


@pytest.mark.skipif(not OFFICIAL, reason="SANTINELA_SAPIENT_PROTO nu e setat (fisierele oficiale Dstl)")
class TestOfficialSchema:
    @pytest.fixture(scope="class")
    def sch(self):
        from santinela.sapient_proto import load_schema
        return load_schema(OFFICIAL)

    def test_own_registration_is_valid(self, sch):
        node, _ = make_node(sch)
        reg = node.registration()
        assert sch.validate("SapientMessage", reg) == []
        back = sch.decode("SapientMessage", sch.encode("SapientMessage", reg))
        assert back["registration"]["node_definition"][0]["node_type"] == "NODE_TYPE_FUSION_NODE"

    def test_track_detection_is_valid_and_round_trips(self, sch):
        node, _ = make_node(sch)
        trk = _track()
        msg = node.track_detection(trk, FR)
        assert sch.validate("SapientMessage", msg) == []
        assert node.track_detection(trk, FR)["detection_report"]["object_id"] == msg["detection_report"]["object_id"]
        decoded = sch.decode("SapientMessage", sch.encode("SapientMessage", msg))
        ad = UniversalAdapter(frame=FR, tod_ref=TOD0)
        item = ad._sapient([decoded], None)[0]
        assert ad.sites[0].center + np.array([item.x, item.y]) == pytest.approx(trk.state.x[:2], abs=1.0)
        assert item.cov[:2, :2] == pytest.approx(trk.state.P[:2, :2], rel=1e-3)

    def test_task_and_messages_are_valid(self, sch):
        node, _ = make_node(sch)
        assert sch.validate("SapientMessage", node.task_look_at(SENSOR, 45.3, 28.5, 120.0)) == []
        reg = {"timestamp": ts(EPOCH0), "node_id": SENSOR,
               "registration": {"node_definition": [{"node_type": "NODE_TYPE_RADAR"}]}}
        ack = node.handle(reg)[0]
        assert sch.validate("SapientMessage", ack) == []
        assert len(node.nodes[SENSOR]["issues"]) >= 5  # Registration incompleta: abaterile sunt retinute
