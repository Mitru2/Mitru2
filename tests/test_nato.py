"""Standarde NATO si conexe: SAPIENT binar (protobuf), STANAG 4607 GMTI, Cursor-on-Target,
NFFI (STANAG 5527), ASTERIX CAT001/002/034, Remote ID, legaturi de date cu specificatie controlata."""
import math
import struct
from datetime import datetime, timezone

import numpy as np
import pytest

from santinela import CONFIRMED, Tracker
from santinela import cot, nffi, remoteid, stanag4607
from santinela.geo import LocalFrame
from santinela.measurements import SensorDetection
from santinela.protowire import ProtoSchema, encode_field, encode_varint
from santinela.radar import RadarPlot, RadarSite, RadarTrackReport
from santinela.universal import UniversalAdapter, detect_format

FR = LocalFrame(45.27, 28.46)
TOD0 = 36000.0


def ll(x, y):
    return FR.to_latlon(x, y)


# ---------------------------------------------------------------- protobuf / SAPIENT binar
SAPIENT_TEST_PROTO = """
syntax = "proto3";
package sapient_msg.test;
import "google/protobuf/timestamp.proto";

enum LocationCoordinateSystem {
  LOCATION_COORDINATE_SYSTEM_UNSPECIFIED = 0;
  LOCATION_COORDINATE_SYSTEM_LAT_LNG_DEG_M = 1;
  LOCATION_COORDINATE_SYSTEM_UTM_M = 3;
}
message Location {
  optional double x = 1;
  optional double y = 2;
  optional double z = 3;
  optional LocationCoordinateSystem coordinate_system = 7;
  optional string utm_zone = 9;
}
message RangeBearing {
  optional double elevation = 1;
  optional double azimuth = 2;
  optional double range = 3;
}
message ENUVelocity { optional double east_rate = 1; optional double north_rate = 2; }
message DetectionReport {
  optional string report_id = 1;
  optional string object_id = 2;
  oneof location_oneof {
    Location location = 4;
    RangeBearing range_bearing = 5;
  }
  message DetectionReportClassification {   // imbricat
    optional string type = 1;
    optional float confidence = 2;
  }
  repeated DetectionReportClassification classification = 8;
  optional ENUVelocity enu_velocity = 9;
  repeated sint32 tags = 10 [packed = true];
  map<string, string> extra = 11;
}
message StatusReport { optional Location node_location = 5; }
message SapientMessage {
  google.protobuf.Timestamp timestamp = 1;
  optional string node_id = 2;
  oneof content {
    StatusReport status_report = 12;
    DetectionReport detection_report = 13;
  }
}
"""


def _ts(epoch: float) -> bytes:
    return encode_field(1, int(epoch)) + encode_field(2, int(round((epoch % 1) * 1e9)))


def _sapient_detection(epoch, node, obj, lat, lon, cls="UAV", conf=0.8) -> bytes:
    loc = encode_field(1, lon) + encode_field(2, lat) + encode_field(3, 50.0) + encode_field(7, 1)
    clsm = encode_field(1, cls) + encode_field(2, conf, "float")
    vel = encode_field(1, -30.0) + encode_field(2, 4.0)
    packed = b"".join(encode_varint((v << 1) ^ (v >> 63)) for v in (-2, 5))
    extra = encode_field(1, "k") + encode_field(2, "v")
    det = (encode_field(1, f"r-{obj}") + encode_field(2, obj) + encode_field(4, loc) + encode_field(8, clsm)
           + encode_field(9, vel) + encode_field(10, packed) + encode_field(11, extra))
    return encode_field(1, _ts(epoch)) + encode_field(2, node) + encode_field(13, det)


def test_proto_schema_decodes_nested_oneof_enum_packed_map():
    sch = ProtoSchema.from_text(SAPIENT_TEST_PROTO)
    lat, lon = ll(2000.0, 1000.0)
    msg = sch.decode("SapientMessage", _sapient_detection(1794477605.25, "n1", "o1", lat, lon))
    det = msg["detection_report"]
    assert msg["node_id"] == "n1"
    assert msg["timestamp"] == {"seconds": 1794477605, "nanos": 250000000}
    assert det["location"]["coordinate_system"] == "LOCATION_COORDINATE_SYSTEM_LAT_LNG_DEG_M"
    assert det["location"]["y"] == pytest.approx(lat)
    assert det["classification"][0]["type"] == "UAV"
    assert det["classification"][0]["confidence"] == pytest.approx(0.8)
    assert det["tags"] == [-2, 5]
    assert det["extra"] == {"k": "v"}


def test_proto_unknown_fields_are_skipped_and_errors_on_truncation():
    sch = ProtoSchema.from_text(SAPIENT_TEST_PROTO)
    data = encode_field(99, "necunoscut") + encode_field(2, "n9")
    assert sch.decode("SapientMessage", data) == {"node_id": "n9"}
    with pytest.raises(Exception):
        sch.decode("SapientMessage", encode_field(2, "n9")[:-2])


def test_sapient_binary_end_to_end_with_proto_dir(tmp_path):
    (tmp_path / "sapient.proto").write_text(SAPIENT_TEST_PROTO)
    ad = UniversalAdapter(frame=FR, tod_ref=TOD0, sapient_proto_dir=str(tmp_path))
    lat, lon = ll(3000.0, -1000.0)
    epoch = 1794477600 + 12  # 2026-11-12 10:00:12 UTC
    body = _sapient_detection(epoch, "cuas-1", "o-5", lat, lon)
    bird = _sapient_detection(epoch, "cuas-1", "o-6", lat, lon, cls="Bird", conf=0.97)
    framed = b"".join(len(b).to_bytes(4, "little") + b for b in (body, bird))
    assert detect_format(framed[:4 + len(body)]) == "sapient-protobuf"
    out = ad.feed(framed)
    assert len(out) == 1 and isinstance(out[0], RadarTrackReport)
    assert out[0].t == pytest.approx(12.0) and out[0].vx == -30.0 and out[0].height_m == 50.0


# ---------------------------------------------------------------- STANAG 4607
def _gmti_packet(targets, t_ms=TOD0 * 1000 + 30_000, sensor=(45.0, 28.0)):
    fields = {"revisit_index": 1, "dwell_index": 7, "last_dwell": 0, "target_count": len(targets),
              "dwell_time_ms": t_ms, "sensor_lat": sensor[0], "sensor_lon": sensor[1], "sensor_alt_cm": 600000,
              "dwell_center_lat": 45.27, "dwell_center_lon": 28.46, "dwell_range_half_km": 20.5,
              "dwell_angle_half_deg": 10.0}
    return stanag4607.encode_dwell_packet(fields, targets, platform="AGS-1")


def test_stanag4607_decode_values():
    tgt = {"report_index": 3, "lat": 45.3012345, "lon": 28.4512345, "height_m": 120, "los_velocity_cms": -2550,
           "classification": 6, "slant_range_unc_cm": 2000, "cross_range_unc_dm": 1500, "los_velocity_unc_cms": 50}
    pkt = _gmti_packet([tgt])
    assert stanag4607.looks_like_4607(pkt) and detect_format(pkt) == "stanag4607"
    dw = stanag4607.decode(pkt)[0]
    assert dw.platform_id == "AGS-1" and dw.exercise and dw.reference_date == (2026, 11, 12)
    assert dw.tod == pytest.approx(TOD0 + 30.0)
    assert dw.fields["dwell_range_half_km"] == pytest.approx(20.5)
    t = dw.targets[0]
    assert t["lat"] == pytest.approx(45.3012345, abs=1e-7) and t["lon"] == pytest.approx(28.4512345, abs=1e-7)
    assert (t["height_m"], t["los_velocity_cms"], t["classification"]) == (120, -2550, 6)


def test_stanag4607_delta_positions():
    fields = {"revisit_index": 1, "dwell_index": 1, "last_dwell": 1, "target_count": 1,
              "dwell_time_ms": 1000, "sensor_lat": 45.0, "sensor_lon": 28.0, "sensor_alt_cm": 0,
              "lat_scale": 1e-4, "lon_scale": 1e-4, "dwell_center_lat": 45.2, "dwell_center_lon": 28.4,
              "dwell_range_half_km": 5.0, "dwell_angle_half_deg": 5.0}
    pkt = stanag4607.encode_dwell_packet(fields, [{"report_index": 1, "delta_lat": 120, "delta_lon": -50}])
    t = stanag4607.decode(pkt)[0].targets[0]
    assert t["lat"] == pytest.approx(45.212, abs=1e-6) and t["lon"] == pytest.approx(28.395, abs=1e-6)


def test_stanag4607_through_adapter_into_tracker_with_doppler():
    tr = Tracker(frame=FR)
    ad = UniversalAdapter(frame=FR, tod_ref=TOD0, tracker=tr)
    s_lat, s_lon = ll(-40000.0, -30000.0)
    s_xy = FR.to_xy(s_lat, s_lon)
    v = np.array([-40.0, 10.0])
    for k in range(10):
        t = 10.0 * k
        p = np.array([10000.0, 5000.0]) + v * t
        lat, lon = ll(*p)
        u = (p - s_xy) / np.linalg.norm(p - s_xy)
        pkt = _gmti_packet([{"report_index": k, "lat": lat, "lon": lon, "height_m": 150,
                             "los_velocity_cms": float(v @ u) * 100, "slant_range_unc_cm": 3000,
                             "cross_range_unc_dm": 2000, "los_velocity_unc_cms": 50}],
                           t_ms=(TOD0 + t) * 1000, sensor=(s_lat, s_lon))
        items = ad.feed(pkt, received_at=t)
        assert isinstance(items[0], SensorDetection) and items[0].rr is not None
        tr.ingest(items, now=t)
    track = tr.tracks["T0001"]
    assert track.status == CONFIRMED
    assert len(track.measurements) == 10
    assert np.linalg.norm(track.state.x[2:] - v) < 8.0
    assert any("semnul vitezei" in n for n in ad.sources["4607:AGS-1"].notes)


# ---------------------------------------------------------------- Cursor-on-Target
def _cot(uid, typ, lat, lon, time="2026-11-12T10:00:20Z", ce=25.0, how="m-g", course=None, speed=None):
    trk = f'<track course="{course}" speed="{speed}"/>' if course is not None else ""
    return (f'<event version="2.0" uid="{uid}" type="{typ}" how="{how}" time="{time}" start="{time}" '
            f'stale="2026-11-12T10:05:00Z"><point lat="{lat}" lon="{lon}" hae="80" ce="{ce}" le="9999999"/>'
            f'<detail>{trk}<contact callsign="{uid}"/></detail></event>')


def test_cot_roles():
    lat, lon = ll(1000.0, 2000.0)
    xml = "".join([_cot("drona-1", "a-h-A-M-F-Q", lat, lon, course=270, speed=45),
                   _cot("echipa-3", "a-f-G-U-C", 45.2, 28.5),
                   _cot("avion-n", "a-n-A-C-F", 45.4, 28.1),
                   _cot("tanc", "a-h-G-E-V-A-T", 45.1, 28.2)])
    assert detect_format(xml) == "cot"
    ad = UniversalAdapter(frame=FR, tod_ref=TOD0)
    out = ad.feed(xml, source="ATAK")
    assert len(out) == 1 and isinstance(out[0], RadarTrackReport)
    assert out[0].t == pytest.approx(20.0) and out[0].vx == pytest.approx(-45.0)
    assert "echipa-3" in ad.friendly and ad.cooperative[0].icao == "avion-n"


def test_track_to_cot_round_trip():
    tr = Tracker(frame=FR)
    lat0, lon0 = ll(0.0, 0.0)
    rs = RadarSite("R1", lat0, lon0, sac=1, sic=7)
    tr.add_radar(rs)
    for k in range(6):
        t = 5.0 * k
        p = np.array([20000.0 - 40 * t, 3000.0])
        tr.ingest([RadarPlot(f"p{k}", "R1", t, float(np.linalg.norm(p)), math.degrees(math.atan2(p[0], p[1])))],
                  now=t)
    trk = tr.tracks["T0001"]
    xml = cot.track_to_cot(trk, FR, datetime(2026, 11, 12, 10, 0, 25, tzinfo=timezone.utc))
    ev = cot.parse(xml)[0]
    assert ev.type == "a-h-A-M-F-Q" and ev.role == "tinta"
    assert FR.to_xy(ev.lat, ev.lon) == pytest.approx(trk.state.x[:2], abs=1.0)
    assert ev.speed == pytest.approx(40.0, abs=5.0)


# ---------------------------------------------------------------- NFFI
NFFI_XML = """<?xml version="1.0"?>
<NFFI xmlns="urn:nato:nffi:1.3">
  <FFI>
    <positionalData><location><coordinates><latitude>45.2510</latitude><longitude>28.4400</longitude>
      </coordinates></location><dateTimeGroup>2026-11-12T10:02:00Z</dateTimeGroup></positionalData>
    <identificationData><systemId>RO-ECH-07</systemId><unitName>Echipa 7</unitName>
      <symbolCode>SFGPU----------</symbolCode></identificationData>
  </FFI>
  <FFI>
    <positionalData><location><coordinates><latitude>45.3</latitude><longitude>28.6</longitude>
      </coordinates></location><dateTimeGroup>121015ZNOV26</dateTimeGroup></positionalData>
    <identificationData><systemId>RO-ECH-08</systemId></identificationData>
  </FFI>
</NFFI>"""


def test_nffi_friendly_positions():
    assert detect_format(NFFI_XML) == "nffi"
    ad = UniversalAdapter(frame=FR)
    assert ad.feed(NFFI_XML) == []
    a, b = ad.friendly["RO-ECH-07"], ad.friendly["RO-ECH-08"]
    assert (a.lat, a.lon, a.name, a.symbol) == (45.251, 28.44, "Echipa 7", "SFGPU----------")
    assert a.tod == 36120.0 and b.tod == 36900.0


# ---------------------------------------------------------------- ASTERIX CAT001/002/034
def test_cat034_teaches_radar_position_then_cat001_plots_flow():
    lat_r, lon_r = 45.2, 28.6
    c34 = (bytes([0xE1, 0x10, 1, 7, 1]) + int(TOD0 * 128).to_bytes(3, "big")
           + (25).to_bytes(2, "big", signed=True)
           + int(round(lat_r * 2 ** 23 / 180)).to_bytes(3, "big", signed=True)
           + int(round(lon_r * 2 ** 23 / 180)).to_bytes(3, "big", signed=True))
    c01 = bytes([0xE2, 1, 7, 0x10]) + struct.pack(">HH", 1280, 8192) + struct.pack(">H", 260 * 128)
    ad = UniversalAdapter(frame=FR, tod_ref=TOD0)
    ad.feed(bytes([34]) + struct.pack(">H", 3 + len(c34)) + c34)
    site = ad._by_asterix[(1, 7)]
    assert (site.lat, site.lon) == (pytest.approx(lat_r, abs=1e-4), pytest.approx(lon_r, abs=1e-4))
    out = ad.feed(bytes([1]) + struct.pack(">H", 3 + len(c01)) + c01, received_at=100.0)
    assert len(out) == 1 and isinstance(out[0], RadarPlot)
    p = out[0]
    assert p.t == pytest.approx(100.0)                      # 36100 - 36000, din ora trunchiata
    assert p.range_m == pytest.approx(10 * 1852.0)          # I001/040: LSB 1/128 NM
    assert p.azimuth_deg == pytest.approx(45.0)


# ---------------------------------------------------------------- Remote ID
def test_remote_id_decodes_and_marks_matching_track():
    lat, lon = ll(5000.0, 2000.0)
    pack = remoteid.encode_pack("RO-DRONA-0042", lat, lon, speed=12.0, direction=200.0, alt_geo=150.0,
                                height=90.0, ts_tenths=(5 * 60 + 30) * 10)  # 10:05:30
    assert remoteid.looks_like_remoteid(pack) and detect_format(pack) == "remoteid"
    s = remoteid.decode(pack)[0]
    assert s.uas_id == "RO-DRONA-0042" and s.direction == 200.0 and s.speed == pytest.approx(12.0)
    assert s.lat == pytest.approx(lat, abs=1e-6) and s.alt_geo == pytest.approx(150.0)
    tr = Tracker(frame=FR)
    lat0, lon0 = ll(0.0, 0.0)
    tr.add_radar(RadarSite("R1", lat0, lon0))
    ad = UniversalAdapter(frame=FR, tod_ref=TOD0, tracker=tr)
    assert ad.feed(pack, received_at=330.0) == []
    d = np.array([5000.0, 2000.0])
    tr.ingest([RadarPlot("p1", "R1", 330.0, float(np.linalg.norm(d)), math.degrees(math.atan2(d[0], d[1])))],
              now=330.0)
    assert ad.cooperative[0].t == pytest.approx(330.0)
    assert ad.annotate_tracks(tr) == {"T0001": "RemoteID:RO-DRONA-0042"}


# ---------------------------------------------------------------- legaturi de date controlate
def test_controlled_datalinks_need_accredited_plugin():
    ad = UniversalAdapter(frame=FR, tod_ref=TOD0)
    ad.declare_source("GW-L16", "link16")
    assert ad.feed(b"\x00\x01\x02", source="GW-L16") == []
    assert any("Link 16" in r and "register_decoder" in r for r in ad.requirements())

    # modulul suplimentar (scris in mediul acreditat) se inregistreaza si datele curg
    lat, lon = ll(4000.0, 0.0)

    def decode_j32(data, received_at, source):
        return [SensorDetection(f"J3.2:{received_at}", "L16-NET", received_at, lat, lon, np.eye(2) * 200.0 ** 2)]

    ad.register_decoder("link16", decode_j32)
    out = ad.feed(b"\x00\x01\x02", received_at=5.0, source="GW-L16")
    assert len(out) == 1 and "L16-NET" in ad._by_name


def test_plugin_with_detect_function():
    ad = UniversalAdapter(frame=FR)
    ad.register_decoder("vendor-bin", lambda d, r, s: [], detect=lambda d: isinstance(d, bytes) and d[:2] == b"VB")
    ad.feed(b"VB\x00\x01")
    assert ad.stats["vendor-bin"] == 1
