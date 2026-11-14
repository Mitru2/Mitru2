"""Surse P1: ADS-B direct (Beast/AVR/SBS/readsb), tinte de oportunitate, Remote ID radio,
STANAG 4607 (clasificare, wrap), variante NFFI / FFI MTF, registrul de conventii."""
import json
import math

import numpy as np
import pytest

from santinela import Tracker, adsb, remoteid, stanag4607
from santinela.asterix import encode_block
from santinela.conventions import CONVENTIONS, report
from santinela.geo import LocalFrame
from santinela.radar import RadarPlot, RadarSite
from santinela.universal import UniversalAdapter, detect_format

FR = LocalFrame(45.27, 28.46)
TOD0 = 36000.0

IDENT = "8D4840D6202CC371C32CE0576098"
POS_EVEN = "8D40621D58C382D690C8AC2863A7"
POS_ODD = "8D40621D58C386435CC412692AD6"
VEL = "8D485020994409940838175B284F"


# ---------------------------------------------------------------- Mode S (vectori din "The 1090 MHz Riddle")
def test_mode_s_reference_vectors():
    for h in (IDENT, POS_EVEN, POS_ODD, VEL):
        assert adsb.crc24(bytes.fromhex(h)) == 0
    bad = bytearray.fromhex(IDENT)
    bad[5] ^= 0x01
    assert adsb.crc24(bytes(bad)) != 0
    d = adsb.ModeSDecoder()
    d.decode(bytes.fromhex(IDENT), 0.0)
    assert d.state["4840D6"].callsign == "KLM1023"
    d.decode(bytes.fromhex(POS_ODD), 0.0)
    s = d.decode(bytes.fromhex(POS_EVEN), 2.0)
    assert (s.lat, s.lon, s.alt_ft) == (pytest.approx(52.2572, abs=1e-4), pytest.approx(3.91937, abs=1e-5), 38000)
    d.decode(bytes.fromhex(VEL), 0.0)
    v = d.state["485020"]
    assert v.speed_kt == pytest.approx(159.20, abs=0.01) and v.track_deg == pytest.approx(182.88, abs=0.01)
    assert v.vrate_fpm == -832
    local = adsb.ModeSDecoder(ref_latlon=(52.258, 3.918)).decode(bytes.fromhex(POS_EVEN), 0.0)
    assert (local.lat, local.lon) == (pytest.approx(52.2572, abs=1e-4), pytest.approx(3.91937, abs=1e-5))


def test_beast_escape_and_formats():
    msg = bytes.fromhex(POS_EVEN)
    frame = adsb.encode_beast(msg, ts=0x1A1A1A1A1A1A)       # marca de timp plina de 0x1A
    assert frame.count(b"\x1a\x1a") >= 3
    assert list(adsb.iter_beast(b"\x00garbage" + frame + adsb.encode_beast(bytes.fromhex(POS_ODD)))) == \
        [(0x33, msg), (0x33, bytes.fromhex(POS_ODD))]
    assert detect_format(frame) == "beast"
    assert detect_format(f"*{IDENT};\n*{VEL};") == "avr"
    assert detect_format("MSG,3,1,1,4840D6,1,2026/11/13,10:00:05.000,,,,38000,,,45.3,28.4,,,0,0,0,0") == "sbs"
    assert detect_format(json.dumps({"now": 1.0, "aircraft": []})) == "readsb"


def test_adsb_formats_feed_cooperative():
    ad = UniversalAdapter(frame=FR, tod_ref=TOD0, adsb_receiver=(52.258, 3.918))
    assert ad.feed(adsb.encode_beast(bytes.fromhex(POS_EVEN)), received_at=5.0) == []
    assert ad.cooperative[-1].lat == pytest.approx(52.2572, abs=1e-4) and ad.cooperative[-1].t == 5.0
    ad.feed("MSG,3,1,1,4B1A2C,1,2026/11/13,10:00:07.500,2026/11/13,10:00:07.500,ROT123,9000,,,45.3,28.4,,,0,0,0,0")
    c = ad.cooperative[-1]
    assert (c.icao, c.callsign, c.t) == ("4B1A2C", "ROT123", 7.5)
    assert c.height_m == pytest.approx(9000 * 0.3048)
    ad.feed(json.dumps({"now": 1794477600 + 10.0, "aircraft": [
        {"hex": "4b1a2d", "lat": 45.1, "lon": 28.9, "alt_geom": 12000, "gs": 400, "track": 90, "seen_pos": 0.5},
        {"hex": "4b1a2e", "alt_baro": 3000}]}))
    c = ad.cooperative[-1]
    assert c.icao == "4B1A2D" and c.t == pytest.approx(9.5) and c.vx == pytest.approx(400 * 1852 / 3600)


def test_targets_of_opportunity_excluded_and_used_for_registration():
    lat0, lon0 = FR.to_latlon(0.0, 0.0)
    rs = RadarSite("R1", lat0, lon0, antenna_height=20.0, sac=1, sic=7)
    tr = Tracker(frame=FR, radars=[rs])
    ad = UniversalAdapter(sites=[rs], frame=FR, tod_ref=TOD0, tracker=tr)
    rng = np.random.default_rng(1)
    items = []
    for k in range(40):
        t = 5.0 * k
        p = np.array([-25000.0 + 150 * t, 20000.0 - 60 * t])
        la, lo = FR.to_latlon(*p)
        ad.feed("MSG,3,1,1,4B0001,1,2026/11/13,"
                f"10:{int(t // 60):02d}:{t % 60:06.3f},,,,10000,,,{la},{lo},,,0,0,0,0", received_at=t)
        ad.feed(f"MSG,4,1,1,4B0001,1,2026/11/13,10:{int(t // 60):02d}:{t % 60:06.3f},,,,,"
                f"{math.hypot(150, 60) / (1852 / 3600)},{math.degrees(math.atan2(150, -60)) % 360},,,,,0,0,0,0",
                received_at=t)
        # radarul primar vede avionul (fara transponder in plot) si o drona joasa, departe de avion
        drone = np.array([8000.0 - 30 * t, -6000.0])
        recs = [{"sac": 1, "sic": 7, "tod": TOD0 + t, "typ": 1,
                 "range_m": float(np.linalg.norm(q)) + rng.normal(0, 30),
                 "azimuth_deg": math.degrees(math.atan2(q[0], q[1])) + rng.normal(0, 0.2)} for q in (p, drone)]
        items += ad.feed(encode_block(recs), received_at=t)
    ad.flush_registration()
    assert len(items) == 40                       # doar drona ajunge la tracker
    assert tr.registrations["R1"].n >= 30         # avionul a alimentat inregistrarea radarului
    assert any("tinte de oportunitate" in n or "potrivite cu aeronave ADS-B" in n
               for n in ad.sources["SAC1/SIC7"].notes)


# ---------------------------------------------------------------- Remote ID pe purtatori radio
@pytest.mark.parametrize("wrap,carrier", [(remoteid.wrap_bluetooth, "bluetooth"),
                                          (remoteid.wrap_wifi_beacon, "wifi-beacon"),
                                          (remoteid.wrap_nan, "wifi-nan")])
def test_remote_id_radio_carriers_and_operator(wrap, carrier):
    lat, lon = FR.to_latlon(3000.0, 1000.0)
    basic_loc = remoteid.encode_pack("RO-UAS-77", lat, lon, 15.0, 90.0, 120.0, 80.0, (5 * 60) * 10)
    msgs = [basic_loc[3:3 + 25], basic_loc[3 + 25:], remoteid.encode_system(45.25, 28.40, 95.0)]
    frame = wrap(remoteid.pack(*msgs))
    assert remoteid.extract(frame)[0][0] == carrier
    assert detect_format(frame) == "remoteid-radio"
    ad = UniversalAdapter(frame=FR, tod_ref=TOD0)
    assert ad.feed(frame, received_at=300.0) == []
    c = ad.cooperative[0]
    assert c.icao == "RO-UAS-77" and c.source == "RemoteID"
    assert (c.operator_lat, c.operator_lon) == (pytest.approx(45.25), pytest.approx(28.40))
    assert c.t == pytest.approx(300.0)


def test_remote_id_single_message_bluetooth_legacy():
    lat, lon = FR.to_latlon(0.0, 0.0)
    loc = remoteid.encode_pack("X", lat, lon, 5.0, 10.0, 50.0, 20.0, 100)[3 + 25:]
    out = remoteid.extract(remoteid.wrap_bluetooth(loc))
    assert out and len(out[0][1]) == 25


# ---------------------------------------------------------------- STANAG 4607
def test_4607_classification_table_and_simulated():
    assert stanag4607.classification(1) == ("vehicul pe senile", False)
    assert stanag4607.classification(2) == ("vehicul cu roti", False)
    assert stanag4607.classification(4) == ("aeronava cu aripa fixa", False)
    assert stanag4607.classification(132) == ("aeronava cu aripa fixa", True)
    assert stanag4607.classification(255) == ("necunoscut", True)
    assert "UAS" not in " ".join(stanag4607.CLASSIFICATION.values())


def test_4607_wrap_velocity_and_class_not_used_as_drone_class():
    fields = {"revisit_index": 1, "dwell_index": 1, "last_dwell": 0, "target_count": 2,
              "dwell_time_ms": (TOD0 + 5) * 1000, "sensor_lat": 45.0, "sensor_lon": 28.0, "sensor_alt_cm": 0,
              "dwell_center_lat": 45.2, "dwell_center_lon": 28.4, "dwell_range_half_km": 5.0,
              "dwell_angle_half_deg": 5.0}
    tg = [{"report_index": 1, "lat": 45.2, "lon": 28.4, "los_velocity_cms": 2000, "wrap_velocity_cms": 20000,
           "classification": 4},
          {"report_index": 2, "lat": 45.21, "lon": 28.41, "los_velocity_cms": 2000, "wrap_velocity_cms": 60000,
           "classification": 3}]
    ad = UniversalAdapter(frame=FR, tod_ref=TOD0)
    out = ad.feed(stanag4607.encode_dwell_packet(fields, tg))
    assert out[0].rr is None and out[1].rr == pytest.approx(20.0)   # 200 m/s < 2 x 235: ambiguu
    assert all(o.drone_class is None for o in out)
    notes = " ".join(ad.sources["4607:SIM"].notes)
    assert "ambigua" in notes and "indiciu slab" in notes


# ---------------------------------------------------------------- NFFI / FFI MTF
def test_nffi_variants_and_ffi_mtf_need():
    from santinela import nffi
    assert nffi.variant('<NFFI xmlns="urn:nato:nffi:1.4"><FFI><latitude>1</latitude></FFI></NFFI>') == "nffi-1.4"
    assert nffi.variant('<NFFI version="1.3"><FFI><latitude>1</latitude></FFI></NFFI>') == "nffi-1.3"
    mtf = "EXER/RO-DUNARE//\nMSGID/FFI/NATO/1//\nPOSITION/45.25N/028.44E//"
    assert detect_format(mtf) == "ffi-mtf"
    ad = UniversalAdapter(frame=FR)
    ad.feed(mtf, source="FFT-GW")
    assert any("APP-11" in r for r in ad.requirements())


# ---------------------------------------------------------------- conventii
def test_conventions_registry():
    assert {c["statut"] for c in report()} <= {"verificat", "raport", "presupus"}
    assert CONVENTIONS["sapient.location.x"].status == "verificat"
    assert "departarea" in CONVENTIONS["stanag4607.d32_7"].value
    for key in ("sapient.framing", "asterix.editions", "nffi.standard", "tak.protocol_v1", "app6.sidc"):
        assert key in CONVENTIONS


# ---------------------------------------------------------------- STANAG 4607 pe formulele Wireshark
def _manual_dwell(lat_raw: int, lon_raw: int, b16_raw: int, kind: int = 0, version: bytes = b"31",
                  geoid: int | None = None) -> bytes:
    """Pachet construit octet cu octet (fara encoderul proiectului)."""
    import struct as st
    mask = 0
    # D2-D9, D24-D27, D32.1-D32.3; Wireshark: D32_1 = bitul 4*8+1 (de la coada) = indicele 30 de la cap
    for k in list(range(8)) + [22, 23, 24, 25] + [30, 31, 32]:
        mask |= 1 << (63 - k)
    body = mask.to_bytes(8, "big")
    body += st.pack(">HHBHI", 1, 1, 0, 1, 36_000_000)        # D2-D6: un raport, ora 10:00:00
    body += st.pack(">iIi", lat_raw, lon_raw, 0)             # D7-D9 senzor
    body += st.pack(">iIHH", lat_raw, lon_raw, b16_raw, 0)   # D24-D27
    body += st.pack(">HiI", 7, lat_raw, lon_raw)             # D32.1-D32.3 tinta
    seg = bytes([2]) + (len(body) + 5).to_bytes(4, "big") + body
    job = b""
    if geoid is not None:
        jb = (5).to_bytes(4, "big") + bytes(63) + bytes([geoid])
        job = bytes([5]) + (len(jb) + 5).to_bytes(4, "big") + jb
    hdr = version + (32 + len(job) + len(seg)).to_bytes(4, "big") + b"RO" + bytes([5]) + b"XN" + b"\x00\x00" \
        + bytes([kind]) + b"PLAT".ljust(10, b"\x00") + (1).to_bytes(4, "big") + (5).to_bytes(4, "big")
    return hdr + job + seg


def test_4607_codings_match_wireshark_formulas():
    # Wireshark prt_sa32: x = (int32)v / 2^30 * 45 ; prt_ba32: x = v / 2^30 * 90 ; prt_kilo: v / 128
    pkt = _manual_dwell(2 ** 29, 2 ** 28, 2560)
    dw = stanag4607.decode(pkt)[0]
    assert dw.fields["sensor_lat"] == pytest.approx(22.5)
    assert dw.fields["sensor_lon"] == pytest.approx(22.5)
    assert dw.fields["dwell_range_half_km"] == pytest.approx(20.0)
    assert dw.targets[0]["lat"] == pytest.approx(22.5) and dw.targets[0]["report_index"] == 7
    assert stanag4607.decode(_manual_dwell(-(2 ** 30), 0, 0))[0].fields["sensor_lat"] == pytest.approx(-45.0)


def test_4607_header_editions_data_kind_and_geoid():
    assert stanag4607.looks_like_4607(_manual_dwell(0, 0, 0, version=b"40"))    # AEDP-4607 editia A
    assert not stanag4607.looks_like_4607(b"XY" + _manual_dwell(0, 0, 0)[2:])
    dw = stanag4607.decode(_manual_dwell(0, 0, 0, version=b"40", geoid=1))[0]
    assert dw.edition == "AEDP-4607 A" and dw.geoid_model == "EGM96"
    ad = UniversalAdapter(frame=FR, tod_ref=TOD0)
    lat_raw = int(45.27 * 2 ** 30 / 45)
    lon_raw = int(28.46 * 2 ** 32 / 360)
    assert ad.feed(_manual_dwell(lat_raw, lon_raw, 0, kind=129)) == []            # exercitiu, simulat
    assert any("simulat" in n for n in ad.sources["4607:PLAT"].notes)
    out = ad.feed(_manual_dwell(lat_raw, lon_raw, 0, kind=128, geoid=1))         # exercitiu, real
    assert len(out) == 1 and out[0].lat == pytest.approx(45.27, abs=1e-6)
    assert any("EGM96" in n for n in ad.sources["4607:PLAT"].notes)
