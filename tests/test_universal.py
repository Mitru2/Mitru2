"""Adaptorul universal: recunoasterea formatului, conversii si ce lipseste pentru fiecare sursa."""
import json
import math
import struct

import numpy as np
import pytest

from santinela import Tracker, CONFIRMED
from santinela.asterix import decode_block, encode_block
from santinela.geo import LocalFrame
from santinela.measurements import SensorDetection
from santinela.radar import RadarPlot, RadarSite, RadarTrackReport
from santinela.universal import (JsonProfile, UniversalAdapter, detect_format, parse_time_tod,
                                 utm_to_latlon)

FR = LocalFrame(45.27, 28.46)
TOD0 = 36000.0  # 10:00:00 UTC = t 0


def nmea(body: str) -> str:
    c = 0
    for ch in body:
        c ^= ord(ch)
    return f"${body}*{c:02X}"


def ll(x, y):
    return FR.to_latlon(x, y)


# ---------------------------------------------------------------- recunoastere
def test_detect_format():
    assert detect_format(encode_block([{"sac": 1, "sic": 2, "tod": 1.0, "range_m": 100.0, "azimuth_deg": 0}])) \
        == "asterix"
    assert detect_format(nmea("RATTM,01,1.0,45.0,T,10,90,T,,,N,,T,,100000.00,A")) == "nmea"
    assert detect_format('{"nodeId": "n1", "detectionReport": {}}') == "sapient-json"
    assert detect_format('{"lat": 45.1, "lon": 28.2}\n{"lat": 45.2, "lon": 28.3}') == "json"
    assert detect_format("<SensorRegistration/>") == "sapient-xml"
    body = b"\x0a\x03\xff\xfe\x01"
    assert detect_format(len(body).to_bytes(4, "little") + body) == "sapient-protobuf"
    assert detect_format(b"\x00\x01garbage\xff\xfe") == "necunoscut"


def test_parse_time_tod():
    assert parse_time_tod("2026-11-12T10:00:05Z") == pytest.approx(36005.0)
    assert parse_time_tod("2026-11-12T12:00:05+02:00") == pytest.approx(36005.0)
    assert parse_time_tod(1794477605.0) == pytest.approx(1794477605.0 % 86400)
    assert parse_time_tod(1794477605000, "epoch_ms") == pytest.approx(1794477605.0 % 86400)
    assert parse_time_tod({"seconds": 1794477605, "nanos": 500000000}) == pytest.approx(1794477605.5 % 86400)


def test_utm_central_meridian():
    lat, lon = utm_to_latlon(500000.0, 5013000.0, 35, True)
    assert lon == pytest.approx(27.0, abs=1e-9)
    assert 45.2 < lat < 45.3


# ---------------------------------------------------------------- ASTERIX
def test_cat062_hand_built_record_auto_site():
    lat, lon = 45.3, 28.5
    rec = (bytes([0x9B, 0x0C]) + bytes([2, 5]) + int(TOD0 * 128 + 640).to_bytes(3, "big")
           + int(round(lat * 2 ** 25 / 180)).to_bytes(4, "big", signed=True)
           + int(round(lon * 2 ** 25 / 180)).to_bytes(4, "big", signed=True)
           + (-160).to_bytes(2, "big", signed=True) + (40).to_bytes(2, "big", signed=True)
           + struct.pack(">H", 42) + bytes([0x00]))
    block = bytes([62]) + struct.pack(">H", 3 + len(rec)) + rec
    cat, recs = decode_block(block)
    assert cat == 62 and recs[0]["track_number"] == 42
    assert recs[0]["lat"] == pytest.approx(lat, abs=1e-6)
    assert (recs[0]["vx_mps"], recs[0]["vy_mps"]) == (-40.0, 10.0)
    ad = UniversalAdapter(frame=FR, tod_ref=TOD0)
    out = ad.feed(block, received_at=6.0)
    assert len(out) == 1 and isinstance(out[0], RadarTrackReport)
    assert out[0].t == pytest.approx(5.0)
    st = ad.sources["SAC2/SIC5"]
    assert st.site == "SIS-2-5" and "acceptata automat" in st.notes[0]
    assert any("altitudinea minima" in n for n in st.needs)


def test_cat062_skips_complex_compound_items():
    # FRN1 (010), FRN11 (380: doar ADR), FRN14 (290: doar TRK); apoi o a doua inregistrare.
    rec1 = bytes([0x81, 0x12]) + bytes([2, 5]) + bytes([0x80]) + bytes.fromhex("ABCDEF") + bytes([0x80, 0x07])
    rec2 = bytes([0x90]) + bytes([2, 5]) + int(100 * 128).to_bytes(3, "big")
    block = bytes([62]) + struct.pack(">H", 3 + len(rec1) + len(rec2)) + rec1 + rec2
    _, recs = decode_block(block)
    assert recs[0]["items"] == ["010", "380", "290"] and recs[0]["icao"] == "ABCDEF"
    assert recs[1]["tod"] == 100.0


def test_cat062_cartesian_needs_reference_point():
    blk = encode_block([{"sac": 3, "sic": 1, "tod": TOD0 + 10, "x_m": 1000.0, "y_m": -2000.0,
                         "track_number": 7}], cat=62)
    ad = UniversalAdapter(frame=FR, tod_ref=TOD0)
    assert ad.feed(blk) == []
    assert any("punctul de referinta" in r for r in ad.requirements())
    lat, lon = ll(0.0, 0.0)
    ad2 = UniversalAdapter(sites=[RadarSite("SIS", lat, lon, sac=3, sic=1, negative_info=False, kind="sistem")],
                           frame=FR, tod_ref=TOD0)
    out = ad2.feed(blk)
    assert (out[0].x, out[0].y) == (1000.0, -2000.0)
    assert any("I062/500" in n for n in ad2.sources["SAC3/SIC1"].notes)


def test_cat021_adsb_goes_to_cooperative_not_tracker():
    blk = encode_block([{"sac": 9, "sic": 9, "icao": "4B1A2C", "lat_hr": 45.4, "lon_hr": 28.1,
                         "tod_073": TOD0 + 20, "height_m": 9000.0, "groundspeed_mps": 230.0, "heading_deg": 90.0,
                         "callsign": "ROT123"}], cat=21)
    ad = UniversalAdapter(frame=FR, tod_ref=TOD0)
    assert ad.feed(blk) == []
    c = ad.cooperative[0]
    assert (c.icao, c.callsign) == ("4B1A2C", "ROT123")
    assert c.lat == pytest.approx(45.4, abs=1e-6) and c.t == pytest.approx(20.0)
    assert c.vx == pytest.approx(230.0, abs=0.1)


def test_other_asterix_categories_are_recognised_and_ignored():
    cat34 = bytes([34, 0, 6, 0x80, 1, 7])
    ad = UniversalAdapter(frame=FR)
    assert ad.feed(cat34) == []
    assert "CAT034" in ad.sources and not ad.requirements()


def test_cat048_unknown_radar_needs_position():
    ad = UniversalAdapter(frame=FR, tod_ref=TOD0)
    ad.feed(encode_block([{"sac": 7, "sic": 7, "tod": TOD0, "range_m": 5000.0, "azimuth_deg": 10.0}]))
    req = ad.requirements()
    assert req and "SAC7/SIC7" in req[0] and "pozitia radarului" in req[0]
    tpl = ad.config_template()["sources"][0]
    assert (tpl["sac"], tpl["sic"]) == (7, 7) and tpl["lat"] is None


def test_adsb_mode_s_plots_register_radar_automatically():
    lat, lon = ll(0.0, 0.0)
    rs = RadarSite("R1", lat, lon, antenna_height=20.0, sac=1, sic=7)
    tr = Tracker(frame=FR, radars=[rs])
    ad = UniversalAdapter(sites=[rs], frame=FR, tod_ref=TOD0, tracker=tr)
    rng = np.random.default_rng(0)
    b_az, b_r, c = 1.0, 120.0, 0.8
    out = []
    # doua aeronave cu transponder: una radiala, una transversala
    for k in range(80):
        t = 5.0 * k
        for icao, p0, v in (("4B0001", np.array([30000.0, 30000.0]), np.array([-60.0, -60.0])),
                            ("4B0002", np.array([-30000.0, 25000.0]), np.array([150.0, 0.0]))):
            p = p0 + v * t
            la, lo = ll(*p)
            hd = math.degrees(math.atan2(v[0], v[1]))
            adsb = encode_block([{"sac": 9, "sic": 9, "icao": icao, "lat_hr": la, "lon_hr": lo, "tod_073": TOD0 + t,
                                  "height_m": 3000.0, "groundspeed_mps": float(np.linalg.norm(v)),
                                  "heading_deg": hd}], cat=21)
            out += ad.feed(adsb, received_at=t)
            d = p
            plot = encode_block([{"sac": 1, "sic": 7, "tod": TOD0 + t - c, "typ": 5, "icao": icao,
                                  "range_m": math.hypot(float(np.linalg.norm(d)), 2980.0) + b_r + rng.normal(0, 50),
                                  "azimuth_deg": math.degrees(math.atan2(d[0], d[1])) + b_az + rng.normal(0, 0.5),
                                  "height_m": 3000.0}])
            out += ad.feed(plot, received_at=t)
    ad.flush_registration()
    assert out == []  # aeronavele cooperative nu intra in trackerul de drone
    reg = tr.registrations["R1"]
    assert reg.estimated
    assert math.degrees(reg.bias_az) == pytest.approx(b_az, abs=0.15)
    assert reg.bias_range == pytest.approx(b_r, abs=25.0)
    assert reg.clock_offset == pytest.approx(c, abs=0.4)


# ---------------------------------------------------------------- NMEA
def test_nmea_ttm_configured_radar_and_heading():
    lat, lon = ll(0.0, 0.0)
    rs = RadarSite("NAV1", lat, lon, sigma_range=30.0, sigma_az_deg=1.0)
    ad = UniversalAdapter(sites=[rs], frame=FR, tod_ref=TOD0)
    txt = "\n".join([nmea("RAHDT,90.0,T"),
                     nmea("RATTM,03,2.0,0.0,R,20.0,0.0,R,,,N,,T,,100010.00,A"),
                     nmea("RATTM,04,1.0,45.0,T,10.0,180.0,T,,,N,,L,,100010.00,A")])  # L = pierdut
    out = ad.feed(txt, source="NAV1")
    assert len(out) == 1
    r = out[0]
    assert r.t == pytest.approx(10.0) and r.track_number == 3
    assert (r.x, r.y) == (pytest.approx(2 * 1852.0, rel=1e-3), pytest.approx(0.0, abs=5.0))  # 0 R + cap 90 = est
    assert r.vx == pytest.approx(20 * 1852 / 3600, rel=1e-6)


def test_nmea_bad_checksum_and_unknown_radar():
    ad = UniversalAdapter(frame=FR, tod_ref=TOD0)
    assert ad.feed("$RATTM,01,1.0,45.0,T,10,90,T,,,N,,T,,100000.00,A*00", source="X") == []
    assert ad.errors and "suma de control" in ad.errors[0][1]
    ad.feed(nmea("RATTM,01,1.0,45.0,T,10,90,T,,,N,,T,,100000.00,A"), source="X")
    assert any("pozitia radarului" in r for r in ad.requirements())


def test_nmea_tll_absolute_is_accepted_without_config():
    ad = UniversalAdapter(frame=FR, tod_ref=TOD0)
    out = ad.feed(nmea("RATLL,05,4516.200,N,02827.600,E,,100020.00,T,"), source="RIS")
    assert len(out) == 1 and out[0].t == pytest.approx(20.0)
    assert ad.sources["RIS"].site == "RIS"


# ---------------------------------------------------------------- SAPIENT
def test_sapient_status_then_range_bearing():
    ad = UniversalAdapter(frame=FR, tod_ref=TOD0)
    det = {"timestamp": "2026-11-12T10:00:30Z", "nodeId": "cuas-7",
           "detectionReport": {"reportId": "d1", "rangeBearing": {"azimuth": 90.0, "range": 3000.0,
                                                                 "elevation": 0.0,
                                                                 "coordinateSystem": "RANGE_BEARING_COORDINATE_SYSTEM_DEGREES_M"}}}
    assert ad.feed(json.dumps(det)) == []
    assert any("pozitia nodului" in r for r in ad.requirements())
    lat, lon = ll(1000.0, 0.0)
    status = {"timestamp": "2026-11-12T10:00:31Z", "nodeId": "cuas-7",
              "statusReport": {"nodeLocation": {"x": lon, "y": lat,
                                                "coordinateSystem": "LOCATION_COORDINATE_SYSTEM_LAT_LNG_DEG_M"}}}
    ad.feed(json.dumps(status))
    out = ad.feed(json.dumps(det))
    assert len(out) == 1 and isinstance(out[0], SensorDetection)
    assert FR.to_xy(out[0].lat, out[0].lon) == pytest.approx([4000.0, 0.0], abs=5.0)
    assert out[0].t == pytest.approx(30.0)


def test_sapient_location_object_track_and_bird_filter():
    ad = UniversalAdapter(frame=FR, tod_ref=TOD0)
    lat, lon = ll(2000.0, 3000.0)
    msgs = [
        {"timestamp": "2026-11-12T10:01:00Z", "nodeId": "n2", "detectionReport": {
            "objectId": "obj-17", "location": {"x": lon, "y": lat, "z": 120.0,
                                               "coordinateSystem": "LOCATION_COORDINATE_SYSTEM_LAT_LNG_DEG_M"},
            "enuVelocity": {"eastRate": -30.0, "northRate": 5.0},
            "classification": [{"type": "UAV", "confidence": 0.7}]}},
        {"timestamp": "2026-11-12T10:01:02Z", "nodeId": "n2", "detectionReport": {
            "objectId": "obj-18", "location": {"x": lon, "y": lat},
            "classification": [{"type": "Bird", "confidence": 0.95}]}},
    ]
    out = ad.feed("\n".join(json.dumps(m) for m in msgs))
    assert len(out) == 1
    r = out[0]
    assert isinstance(r, RadarTrackReport) and r.vx == -30.0 and r.height_m == 120.0
    assert r.t == pytest.approx(60.0)


def test_sapient_binary_needs_decoder():
    ad = UniversalAdapter(frame=FR)
    body = b"\x0a\x03abc"
    ad.feed(len(body).to_bytes(4, "little") + body)
    assert any("protobuf" in r for r in ad.requirements())


# ---------------------------------------------------------------- JSON de producator
def test_vendor_json_heuristic_profile():
    lat, lon = ll(5000.0, 0.0)
    msg = {"sensorId": "V-1", "trackId": "a7", "timestamp": "2026-11-12T10:00:05Z",
           "pos": {"lat": lat, "lon": lon}, "speed": 40.0, "heading": 270.0, "alt": 80.0}
    ad = UniversalAdapter(frame=FR, tod_ref=TOD0)
    out = ad.feed(json.dumps(msg))
    assert len(out) == 1 and isinstance(out[0], RadarTrackReport)
    assert out[0].vx == pytest.approx(-40.0) and out[0].t == pytest.approx(5.0)
    st = ad.sources["V-1"]
    assert any("euristic" in n for n in st.notes)
    assert any("unitatilor" in n for n in st.needs)


def test_vendor_json_declared_profile_polar_km():
    lat, lon = ll(0.0, 0.0)
    rs = RadarSite("V-2", lat, lon, sigma_range=20.0, sigma_az_deg=0.3)
    prof = JsonProfile(name="vendor", match="tgt", source_const="V-2", track_id="tgt.id", time="ts",
                       time_format="epoch", range="tgt.r_km", azimuth="tgt.az", range_scale=1000.0)
    ad = UniversalAdapter(sites=[rs], frame=FR, tod_ref=TOD0, json_profiles=[prof])
    out = ad.feed(json.dumps({"ts": 1794477600 + 7, "tgt": {"id": 9, "r_km": 4.0, "az": 180.0}}))
    assert len(out) == 1
    assert (out[0].x, out[0].y) == (pytest.approx(0.0, abs=1.0), pytest.approx(-4000.0, abs=5.0))
    assert ad.requirements() == []


def test_unrecognised_json_asks_for_profile():
    ad = UniversalAdapter(frame=FR)
    ad.feed('{"foo": 1, "bar": [2, 3]}', source="S9")
    assert any("profil JSON" in r for r in ad.requirements())


def test_from_config_and_template_round_trip(tmp_path):
    cfg = {"tod_ref": TOD0, "sources": [{"sensor_id": "R1", "lat": 45.2, "lon": 28.6, "sac": 1, "sic": 7,
                                         "min_alt": 250.0}]}
    p = tmp_path / "surse.json"
    p.write_text(json.dumps(cfg))
    ad = UniversalAdapter.from_config(p, frame=FR)
    out = ad.feed(encode_block([{"sac": 1, "sic": 7, "tod": TOD0 + 3, "range_m": 5000.0, "azimuth_deg": 10.0}]))
    assert len(out) == 1 and isinstance(out[0], RadarPlot)
    assert ad.sites[0].min_alt == 250.0


# ---------------------------------------------------------------- flux mixt in tracker
def test_mixed_stream_one_target_one_confirmed_track():
    lat0, lon0 = ll(0.0, 0.0)
    radar = RadarSite("R1", lat0, lon0, antenna_height=20.0, sac=1, sic=7)
    tr = Tracker(frame=FR, radars=[radar])
    ad = UniversalAdapter(sites=[radar], frame=FR, tod_ref=TOD0, tracker=tr)
    nlat, nlon = ll(8000.0, -6000.0)
    ad.feed(json.dumps({"timestamp": "2026-11-12T10:00:00Z", "nodeId": "cuas",
                        "statusReport": {"nodeLocation": {"x": nlon, "y": nlat,
                                                          "coordinateSystem": "LAT_LNG_DEG_M"}}}))
    rng = np.random.default_rng(5)
    v = np.array([-40.0, 0.0])
    for k in range(24):
        t = 5.0 * k
        p = np.array([15000.0, -3000.0]) + v * t
        hh, mm, ss = 10, int(t // 60), t % 60
        batch = []
        batch += ad.feed(encode_block([{"sac": 1, "sic": 7, "tod": TOD0 + t, "typ": 1,
                                        "range_m": math.hypot(float(np.linalg.norm(p)), 380.0) + rng.normal(0, 50),
                                        "azimuth_deg": math.degrees(math.atan2(p[0], p[1])) + rng.normal(0, 0.5),
                                        "height_m": 400.0}]), received_at=t)
        d = p - np.array([8000.0, -6000.0])
        if k % 2 == 0:
            batch += ad.feed(json.dumps({"timestamp": f"2026-11-12T{hh:02d}:{mm:02d}:{ss:06.3f}Z", "nodeId": "cuas",
                                         "detectionReport": {"rangeBearing": {
                                             "azimuth": math.degrees(math.atan2(d[0], d[1])) + rng.normal(0, 1),
                                             "range": float(np.linalg.norm(d)) + rng.normal(0, 20)}}}),
                             received_at=t)
        tr.ingest(batch, now=t)
    confirmed = [x for x in tr.tracks.values() if x.status == CONFIRMED]
    assert len(confirmed) == 1
    assert confirmed[0].sources == {"R1", "cuas"}
    assert len(confirmed[0].measurements) >= 30
