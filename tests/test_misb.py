"""MISB ST 0601 / ST 0903 / ST 1201 si extragerea din MPEG-TS (STANAG 4609).

Vectorii ST 0601 sunt exemplele din ST 0601.11 sectiunea 8 ("Conversions and Mappings"), asa cum
le reproduc testele klvdata (MIT); pachetele de locatie sunt exemplele din anexa ST 0903, asa cum
le reproduc testele jmisb (MIT).
"""
import math

import numpy as np
import pytest

from santinela import misb
from santinela.geo import LocalFrame
from santinela.measurements import SensorDetection
from santinela.universal import UniversalAdapter, detect_format

FR = LocalFrame(45.27, 28.46)
EPOCH0 = 1794477600.0  # 2026-11-12 10:00:00 UTC
TOD0 = 36000.0

ST0601_EXAMPLES = [
    ('07 02 08 b8', 3.405814),  # PlatformRollAngle
    ('08 01 93', 147),  # PlatformTrueAirspeed
    ('09 01 9f', 159),  # PlatformIndicatedAirspeed
    ('0D 04 55 95 B6 6D', 60.1768229669783),  # SensorLatitude
    ('0E 04 5B 53 60 c4', 128.426759042045),  # SensorLongitude
    ('0F 02 c2 21', 14190.72),  # SensorTrueAltitude
    ('10 02 cd 9c', 144.5713),  # SensorHorizontalFieldOfView
    ('11 02 d9 17', 152.6436),  # SensorVerticalFieldOfView
    ('12 04 72 4a 0a 20', 160.719211474396),  # SensorRelativeAzimuthAngle
    ('13 04 87 f8 4b 86', -168.792324833941),  # SensorRelativeElevationAngle
    ('14 04 7d c5 5e ce', 176.865437690572),  # SensorRelativeRollAngle
    ('15 04 03 83 09 26', 68590.9832),  # SlantRange
    ('16 02 12 81', 722.8199),  # TargetWidth
    ('17 04 f1 01 a2 29', -10.5423886331461),  # FrameCenterLatitude
    ('18 04 14 bc 08 2b', 29.157890122923),  # FrameCenterLongitude
    ('19 02 34 f3', 3216.037),  # FrameCenterElevation
    ('22 01 9b', 155),  # IcingDetected
    ('23 02 a7 c4', 235.924),  # WindDirection
    ('24 01 b2', 69.80392),  # WindSpeed
    ('25 02 be ba', 3725.185),  # StaticPressure
    ('26 02 CA 35', 14818.68),  # DensityAltitude
    ('27 01 54', 84),  # OutsideAirTemperature
    ('28 04 8F 69 52 62', -79.1638500518929),  # TargetLocationLatitude
    ('29 04 76 54 57 F2', 166.400812960416),  # TargetLocationLongitude
    ('2A 02 F8 23', 18389.05),  # TargetLocationElevation
    ('2B 01 03', 6),  # TargetTrackGateWidth
    ('2C 01 0F', 30),  # TargetTrackGateHeight
    ('2D 02 1A 95', 425.21515),  # TargetErrorEstimateCE90
    ('2E 02 26 11', 608.92309),  # TargetErrorEstimateLE90
    ('2F 01 31', 49),  # GenericFlagData01
    ('31 02 3D 07', 1191.958),  # DifferentialPressure
    ('32 02 C8 83', -8.670177),  # PlatformAngleOfAttack
    ('33 02 D3 FE', -61.88693),  # PlatformVerticalSpeed
    ('34 02 DF 79', -5.082475),  # PlatformSideslipAngle
    ('35 02 6A F4', 2088.96),  # AirfieldBarometricPressure
    ('36 02 76 70', 8306.806),  # AirfieldElevation
    ('37 01 81', 50.58823),  # RelativeHumidity
    ('38 01 8C', 140),  # PlatformGroundSpeed
    ('39 04 b3 8e ac f1', 3506979.0316),  # GroundRange
    ('3A 02 A4 5D', 6420.539),  # PlatformFuelRemaining
    ('3C 02 AF D8', 45016),  # WeaponLoad
    ('3D 01 BA', 186),  # WeaponFired
    ('3E 02 C6 CF', 50895),  # LaserPRFCode
    ('3F 01 D1', 209),  # SensorFieldOfViewName
    ('40 02 DD C5', 311.8682),  # PlatformMagneticHeading
    ('41 01 E8', 232),  # UASLSVersionNumber
    ('43 04 85 A1 5A 39', -86.041207348947),  # AlternatePlatformLatitude
    ('44 04 00 1C 50 1C', 0.155527554524842),  # AlternatePlatformLongitude
    ('45 02 0B B3', 9.445334),  # AlternatePlatformAltitude
    ('47 02 17 2F', 32.60242),  # AlternatePlatformHeading
    ('4B 02 C2 21', 14190.72),  # SensorEllipsoidHeightConversion
    ('4C 02 0B B3', 9.445334),  # AlternatePlatformEllipsoidHeight
    ('4E 02 0B B3', 9.445334),  # FrameCenterHeightAboveEllipsoid
    ('4F 02 80 01', -327),  # SensorNorthVelocity
    ('50 02 80 01', -327),  # SensorEastVelocity
    ('52 04 F0 F4 12 44', -10.579637999887),  # CornerLatitudePoint1Full
    ('53 04 14 B6 79 B9', 29.1273677986333),  # CornerLongitudePoint1Full
    ('54 04 F0 F8 F8 7E', -10.5661816260963),  # CornerLatitudePoint2Full
    ('55 04 14 B8 EC D6', 29.140824172424),  # CornerLongitudePoint2Full
    ('56 04 F0 FD DE 81', -10.5527275411938),  # CornerLatitudePoint3Full
    ('57 04 14 BB 5F D8', 29.1542782573265),  # CornerLongitudePoint3Full
    ('58 04 F1 02 C4 BB', -10.5392711674031),  # CornerLatitudePoint4Full
    ('59 04 14 BD D2 F5', 29.1677346311172),  # CornerLongitudePoint4Full
    ('5A 04 FF 62 E2 F2', -0.4315251),  # PlatformPitchAngleFull
    ('5B 04 04 D8 04 DF', 3.405814),  # PlatformRollAngleFull
    ('5C 04 F3 AB 48 EF', -8.670177),  # PlatformAngleOfAttackFull
]


@pytest.mark.parametrize("hexstr,value", ST0601_EXAMPLES)
def test_st0601_examples_decode_and_encode(hexstr, value):
    b = bytes.fromhex(hexstr.replace(" ", ""))
    tag, i = misb.ber_oid(b, 0)
    n, i = misb.ber_length(b, i)
    got = misb.decode_0601_value(tag, b[i:i + n])
    lsb = abs(misb.ST0601[tag][3][1] - misb.ST0601[tag][3][0]) / (misb.ST0601[tag][2][1] - misb.ST0601[tag][2][0])
    assert got == pytest.approx(value, abs=lsb)
    assert misb.encode_mapped(tag, value) == b[i:i + n]


def test_st0903_location_pack_examples():
    lp = bytes.fromhex("27ba9302344a1adf1014")
    L = misb.location_pack(lp)                                   # IMAPB (ST 0903.4+)
    assert (L["lat"], L["lon"], L["hae"]) == (pytest.approx(-10.5423886331461, abs=1e-4),
                                              pytest.approx(29.157890122923, abs=1e-4), pytest.approx(3216.0, abs=0.02))
    S = misb.location_pack(lp + bytes.fromhex("00030060513C"))
    assert (S["sig_e"], S["sig_n"], S["sig_u"]) == (pytest.approx(0.1, abs=0.03), pytest.approx(3.0, abs=0.03),
                                                    pytest.approx(649.9, abs=0.03))
    G = misb.location_pack(bytes.fromhex("BD27D27CCE38E38D8C38"), legacy=True)   # ST 0903.3 B.2
    assert (G["lat"], G["lon"], G["hae"]) == (pytest.approx(43.0, abs=1e-5), pytest.approx(110.0, abs=1e-5),
                                              pytest.approx(10000.0, abs=0.4))
    assert misb.encode_location_pack(-10.5423886331461, 29.157890122923, 3216.0) == lp


def test_imapb_round_trip_and_special_values():
    enc = misb.Imapb(-900, 19000, 2)
    for x in (-900.0, 0.0, 123.4, 18999.0):
        assert enc.decode(enc.encode(x)) == pytest.approx(x, abs=enc.sR)
    assert enc.decode(b"\x80\x00") is None          # valoare speciala (bitul cel mai semnificativ)


def test_st0601_packet_checksum_and_error_indicator():
    pkt = misb.encode_0601({2: int(EPOCH0 * 1e6).to_bytes(8, "big"), 13: misb.encode_mapped(13, 45.2),
                            14: misb.encode_mapped(14, 28.5), 3: b"MISIUNE-7"})
    u = misb.decode_0601(pkt)
    assert u.checksum_ok and u.fields["MissionID"] == "MISIUNE-7"
    assert u.fields["SensorLatitude"] == pytest.approx(45.2, abs=1e-6) and u.time == pytest.approx(EPOCH0)
    bad = bytearray(pkt)
    bad[pkt.index(b"MISIUNE")] ^= 0x01            # un octet de date, nu de structura
    assert misb.decode_0601(bytes(bad)).checksum_ok is False
    assert misb.decode_0601_value(13, b"\x80\x00\x00\x00") is None   # indicatorul de eroare


def _scene():
    lat, lon = FR.to_latlon(6000.0, 2000.0)
    vmti = misb.encode_vmti_body([(7, misb.encode_location_pack(lat, lon, 150.0, sig=(20.0, 40.0, 30.0),
                                                                rho=(0.5, 0.0, 0.0)))])
    slat, slon = FR.to_latlon(0.0, -5000.0)
    tlat, tlon = FR.to_latlon(-3000.0, 4000.0)
    return misb.encode_0601({2: int((EPOCH0 + 15) * 1e6).to_bytes(8, "big"), 4: b"UAV-RO1",
                             13: misb.encode_mapped(13, slat), 14: misb.encode_mapped(14, slon),
                             40: misb.encode_mapped(40, tlat), 41: misb.encode_mapped(41, tlon),
                             45: misb.encode_mapped(45, 21.46)}, vmti_body=vmti)


def test_klv_to_tracker_inputs():
    pkt = _scene()
    assert detect_format(pkt) == "klv"
    ad = UniversalAdapter(frame=FR, tod_ref=TOD0)
    out = ad.feed(pkt)
    assert len(out) == 2 and all(isinstance(o, SensorDetection) for o in out)
    tgt = [o for o in out if o.report_id.endswith(":tinta")][0]
    assert tgt.t == pytest.approx(15.0) and tgt.cov[0, 0] == pytest.approx(10.0 ** 2, rel=0.01)  # CE90/2,146
    v = [o for o in out if "vmti7" in o.report_id][0]
    assert FR.to_xy(v.lat, v.lon) == pytest.approx([6000.0, 2000.0], abs=1.0)
    assert v.cov[0, 0] == pytest.approx(400.0, rel=0.02) and v.cov[1, 1] == pytest.approx(1600.0, rel=0.02)
    from santinela import geoid
    assert v.cov[0, 1] == pytest.approx(0.5 * 20 * 40, rel=0.02)
    assert v.altitude_m == pytest.approx(150.0 - geoid.undulation(v.lat, v.lon), abs=1)  # HAE -> MSL


def _mpegts(payload: bytes, pid: int = 0x1F1) -> bytes:
    pes = b"\x00\x00\x01\xbd" + (len(payload) + 3).to_bytes(2, "big") + b"\x80\x00\x00" + payload
    out = b""
    first = True
    for k in range(0, len(pes), 184):
        chunk = pes[k:k + 184]
        hdr = bytes([0x47, (0x40 if first else 0) | (pid >> 8), pid & 0xFF])
        if len(chunk) < 184:  # umplere prin campul de adaptare
            pad = 184 - len(chunk)
            af = bytes([pad - 1]) + (b"\x00" + b"\xff" * (pad - 2) if pad > 1 else b"")
            out += hdr + bytes([0x30]) + af + chunk
        else:
            out += hdr + bytes([0x10]) + chunk
        first = False
    video = bytes([0x47, 0x41, 0x00, 0x10]) + bytes(184)  # alt PID (video), ignorat
    return video + out + video


def test_klv_from_mpegts_stanag4609():
    pkt = _scene()
    ts = _mpegts(pkt + pkt)
    assert len(ts) % 188 == 0 and detect_format(ts) == "mpegts"
    assert misb.klv_from_mpegts(ts) == [pkt, pkt]
    ad = UniversalAdapter(frame=FR, tod_ref=TOD0)
    assert len(ad.feed(ts)) == 4


def test_vmti_pixel_only_targets_need_geolocation():
    pack = misb.enc_ber_oid(9) + misb.enc_ber_oid(1) + misb.enc_ber_length(2) + b"\x01\x00"
    body = b"\x04\x01\x05" + misb.enc_ber_oid(101) + misb.enc_ber_length(len(pack) + 1) + misb.enc_ber_length(len(pack)) + pack
    slat, slon = FR.to_latlon(0.0, 0.0)
    pkt = misb.encode_0601({2: int(EPOCH0 * 1e6).to_bytes(8, "big"), 13: misb.encode_mapped(13, slat),
                            14: misb.encode_mapped(14, slon)}, vmti_body=body)
    ad = UniversalAdapter(frame=FR, tod_ref=TOD0)
    assert ad.feed(pkt) == []
    assert any("doar in pixeli" in r for r in ad.requirements())
