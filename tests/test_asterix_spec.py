"""ASTERIX verificat pe asterix-specs: interpretoarele scrise de mana vs. decodorul generic din
specificatii, encoderele vs. specificatii, regresii de UAP, CAT015 (INCS) si CAT205 (goniometre)."""
import math
import random

import numpy as np
import pytest

from santinela import asterix as A
from santinela.geo import LocalFrame
from santinela.measurements import SensorDetection
from santinela.radar import RadarSite, RadarTrackReport
from santinela.universal import UniversalAdapter

NM, FT = 1852.0, 0.3048
FR = LocalFrame(45.27, 28.46)
TOD0 = 36000.0

# (categorie, articol) -> perechi (valoare interpretor, valoare din specificatie in SI)
CHECKS = {
    (48, "040"): lambda s, g: [(s["range_m"], g["RHO"] * NM), (s["azimuth_deg"], g["THETA"])],
    (48, "042"): lambda s, g: [(s["x_m"], g["X"] * NM), (s["y_m"], g["Y"] * NM)],
    (48, "200"): lambda s, g: [(s["groundspeed_mps"], g["GSP"] * NM), (s["heading_deg"], g["HDG"])],
    (48, "090"): lambda s, g: [(s["fl"], g["FL"])],
    (48, "110"): lambda s, g: [(s["height_m"], g["3DH"] * FT)],
    (48, "140"): lambda s, g: [(s["tod"], g)],
    (48, "210"): lambda s, g: [(s["sigma_x_m"], g["SIGX"] * NM), (s["sigma_y_m"], g["SIGY"] * NM),
                               (s["sigma_v_mps"], g["SIGV"] * NM), (s["sigma_h_deg"], g["SIGH"])],
    (62, "105"): lambda s, g: [(s["lat"], g["LAT"]), (s["lon"], g["LON"])],
    (62, "100"): lambda s, g: [(s["x_m"], g["X"]), (s["y_m"], g["Y"])],
    (62, "185"): lambda s, g: [(s["vx_mps"], g["VX"]), (s["vy_mps"], g["VY"])],
    (62, "070"): lambda s, g: [(s["tod"], g)],
    (62, "130"): lambda s, g: [(s["height_m"], g * FT)],
    (62, "136"): lambda s, g: [(s["fl"], g)],
    (21, "130"): lambda s, g: [(s["lat"], g["LAT"]), (s["lon"], g["LON"])],
    (21, "131"): lambda s, g: [(s["lat_hr"], g["LAT"]), (s["lon_hr"], g["LON"])],
    (21, "140"): lambda s, g: [(s["height_m"], g * FT)],
    (21, "145"): lambda s, g: [(s["fl"], g)],
    (21, "073"): lambda s, g: [(s["tod_073"], g)],
    (21, "160"): lambda s, g: [(s["groundspeed_mps"], g["GS"] * NM), (s["heading_deg"], g["TA"])],
    (1, "040"): lambda s, g: [(s["range_m"], g["RHO"] * NM), (s["azimuth_deg"], g["THETA"])],
    (1, "042"): lambda s, g: [(s["x_m"], g["X"] * NM), (s["y_m"], g["Y"] * NM)],
    (1, "200"): lambda s, g: [(s["groundspeed_mps"], g["GSP"] * NM), (s["heading_deg"], g["HDG"])],
    (1, "141"): lambda s, g: [(s["tod_trunc"], g)],
    (1, "120"): lambda s, g: [(s["doppler_mps"], g * NM)],
    (34, "120"): lambda s, g: [(s["site_height_m"], g["HGT"]), (s["site_lat"], g["LAT"]), (s["site_lon"], g["LON"])],
    (34, "030"): lambda s, g: [(s["tod"], g)],
}


@pytest.mark.parametrize("key", sorted(CHECKS))
def test_handwritten_interpreters_match_spec(key):
    cat, item = key
    sp = A.item_spec(cat, item)
    rnd = random.Random(hash(key) & 0xFFFF)
    for _ in range(200):
        d = bytes(rnd.randrange(256) for _ in range(A._ebits(sp[1]) // 8))
        s, g = A.INTERPRETERS[cat](item, d), A.decode_item(sp, d)
        for a, b in CHECKS[key](s, g):
            assert math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6), (cat, item, a, b)


def test_compound_interpreters_match_spec():
    rnd = random.Random(7)
    for cat, item in ((48, "120"), (62, "500")):
        sp = A.item_spec(cat, item)
        subs = [k for k, x in enumerate(sp[1]) if x is not None and x[1][0] == "F"]
        for _ in range(200):
            pick = sorted({rnd.choice(subs) for _ in range(3)})
            nb = max(pick) // 7 + 1
            prim = bytearray(nb)
            for k in pick:
                prim[k // 7] |= 0x80 >> (k % 7)
            for q in range(nb - 1):
                prim[q] |= 1
            d = bytes(prim) + b"".join(bytes(rnd.randrange(256) for _ in range(A._ebits(sp[1][k][1][1]) // 8))
                                       for k in pick)
            s, g = A.INTERPRETERS[cat](item, d), A.decode_item(sp, d)
            if cat == 48 and "CAL" in g:
                assert s["doppler_mps"] == g["CAL"]["CAL"]
                assert s["doppler_valid"] == (g["CAL"]["D"] == 0)
            if cat == 62 and "APC" in g:
                assert (s["sigma_x_m"], s["sigma_y_m"]) == (g["APC"]["X"], g["APC"]["Y"])
            if cat == 62 and "ATV" in g:
                assert (s["sigma_vx_mps"], s["sigma_vy_mps"]) == (g["ATV"]["X"], g["ATV"]["Y"])


def test_encoders_match_spec():
    blk = A.encode_block([{"sac": 1, "sic": 7, "tod": 36010.5, "typ": 1, "range_m": 25000.0, "azimuth_deg": 300.0,
                           "height_m": 1200.0, "doppler_mps": -37.0, "track_number": 42, "x_m": -5000.0,
                           "y_m": 8000.0, "groundspeed_mps": 41.0, "heading_deg": 250.0, "icao": "4B1A2C"}])
    f = A.decode_block(blk, full=True)[1][0]["fields"]
    assert f["040"]["RHO"] * NM == pytest.approx(25000.0, abs=4)
    assert f["110"]["3DH"] * FT == pytest.approx(1200.0, abs=4)
    assert f["120"]["CAL"]["CAL"] == -37 and f["161"]["TRN"] == 42 and f["220"] == 0x4B1A2C
    assert f["200"]["GSP"] * NM == pytest.approx(41.0, abs=0.2)
    blk = A.encode_block([{"sac": 2, "sic": 5, "tod": 100.0, "lat": 45.3, "lon": 28.5, "vx_mps": -40.0,
                           "vy_mps": 10.0, "track_number": 9, "height_m": 300.0, "sigma_x_m": 50.0,
                           "sigma_y_m": 60.0, "sigma_vx_mps": 2.0, "sigma_vy_mps": 3.0}], cat=62)
    f = A.decode_block(blk, full=True)[1][0]["fields"]
    assert f["105"]["LAT"] == pytest.approx(45.3, abs=1e-6) and f["185"] == {"VX": -40.0, "VY": 10.0}
    assert f["500"]["APC"] == {"X": 50.0, "Y": 60.0} and f["500"]["ATV"] == {"X": 2.0, "Y": 3.0}
    blk = A.encode_block([{"sac": 9, "sic": 9, "icao": "4B1A2C", "lat_hr": 45.4, "lon_hr": 28.1, "tod_073": 20.0,
                           "callsign": "ROT123", "groundspeed_mps": 230.0, "heading_deg": 90.0}], cat=21)
    f = A.decode_block(blk, full=True)[1][0]["fields"]
    assert f["170"] == "ROT123" and f["131"]["LAT"] == pytest.approx(45.4, abs=1e-7)


def test_uap_regressions_found_by_verification():
    # CAT001 traseu: FRN 22 = I001/150 (lipsea din tabela scrisa de mana)
    assert A.UAPS[1]["track"][21] == "150"
    # I062/380 subcampul 8 (TIS) e extins: un TIS cu extensie nu mai dezaliniaza inregistrarea
    # FSPEC: FRN1 + FRN11; I062/380 primar: doar subcampul 8 (al doilea octet, primul bit); TIS = 2 octeti
    rec1 = bytes([0x81, 0x10]) + bytes([2, 5]) + bytes([0x01, 0x80]) + bytes([0x81, 0x00])
    rec2 = bytes([0x90]) + bytes([2, 5]) + int(100 * 128).to_bytes(3, "big")
    import struct
    block = bytes([62]) + struct.pack(">H", 3 + len(rec1) + len(rec2)) + rec1 + rec2
    recs = A.decode_block(block)[1]
    assert recs[1]["tod"] == 100.0
    # I001/120: LSB 2^-8 NM/s (era 2^-14 in tabela scrisa de mana)
    assert A.INTERPRETERS[1]("120", bytes([1]))["doppler_mps"] == pytest.approx(NM / 256)


def test_editions_come_from_spec():
    assert A.SPEC_EDITION[48] == "1.32" and A.SPEC_EDITION[62] == "1.21"
    assert A.SPEC_EDITION[21] == "2.7" and A.SPEC_EDITION[34] == "1.29"
    assert A.REFERENCE_EDITION[15] == A.SPEC_EDITION[15]


# ---------------------------------------------------------------- CAT015 / CAT205
def _cat015(**over):
    rec = {"010": {"SAC": 3, "SIC": 9}, "000": {"MT": 2, "RG": 0}, "020": {"MOMU": 1, "TTAX": 0},
           "145": TOD0 + 10.5, "161": 77, "170": {"CNF": 0},
           "600": {"P84": {"LATITUDE": 45.3, "LONGITUDE": 28.5},
                   "HPP": {"SDHPX": 40.0, "SDHPY": 60.0, "COSDHPXY": 0.25}},
           "601": {"GH": 120.0}, "602": {"HV": {"X": -30.0, "Y": 5.0}, "SDHV": {"X": 1.0, "Y": 1.5, "COHVXY": 0}}}
    rec.update(over)
    return A.encode_generic(15, [rec])


def test_cat015_wgs84_track_with_correlated_covariance():
    ad = UniversalAdapter(frame=FR, tod_ref=TOD0)
    out = ad.feed(_cat015())
    assert len(out) == 1 and isinstance(out[0], RadarTrackReport)
    r = out[0]
    assert r.t == pytest.approx(10.5) and r.track_number == 77 and (r.vx, r.vy) == (-30.0, 5.0)
    assert r.cov[0, 1] == pytest.approx(0.25 * 40 * 60) and r.cov[1, 1] == pytest.approx(3600.0)
    from santinela import geoid
    assert r.height_m == pytest.approx(120.0 - geoid.undulation(45.3, 28.5), abs=0.01)  # HAE -> MSL


def test_cat015_polar_needs_site_and_multistatic_needs_wgs84():
    polar = {"600": None}
    blk = A.encode_generic(15, [{"010": {"SAC": 4, "SIC": 1}, "000": {"MT": 1, "RG": 0},
                                 "020": {"MOMU": 0, "TTAX": 0}, "145": TOD0 + 1,
                                 "625": {"R": 5000.0}, "627": {"AZ": 90.0}}])
    ad = UniversalAdapter(frame=FR, tod_ref=TOD0)
    assert ad.feed(blk) == [] and any("pozitia senzorului INCS" in r for r in ad.requirements())
    lat, lon = FR.to_latlon(0.0, 0.0)
    ad2 = UniversalAdapter(sites=[RadarSite("INCS", lat, lon, sac=4, sic=1)], frame=FR, tod_ref=TOD0)
    out = ad2.feed(blk)
    assert isinstance(out[0], SensorDetection)
    assert FR.to_xy(out[0].lat, out[0].lon) == pytest.approx([5000.0, 0.0], abs=2.0)
    multi = A.encode_generic(15, [{"010": {"SAC": 4, "SIC": 1}, "000": {"MT": 1, "RG": 0},
                                   "020": {"MOMU": 1, "TTAX": 0}, "145": TOD0 + 1,
                                   "625": {"R": 5000.0}, "627": {"AZ": 90.0}}])
    assert ad2.feed(multi) == [] and any("multistatic" in r for r in ad2.requirements())
    del polar


def test_cat015_simulated_targets_ignored():
    ad = UniversalAdapter(frame=FR, tod_ref=TOD0)
    assert ad.feed(_cat015(**{"020": {"MOMU": 1, "TTAX": 3}})) == []


def test_cat205_emitter_position_and_bearing_only():
    blk = A.encode_generic(205, [
        {"010": {"SAC": 6, "SIC": 2}, "000": 1, "030": TOD0 + 4, "040": 1,
         "050": {"LAT": 45.25, "LON": 28.40}, "110": 300.0},
        {"010": {"SAC": 6, "SIC": 2}, "000": 2, "030": TOD0 + 5, "040": 2, "070": 123.45, "170": 3}])
    ad = UniversalAdapter(frame=FR, tod_ref=TOD0)
    out = ad.feed(blk)
    assert len(out) == 1 and isinstance(out[0], SensorDetection)
    assert (out[0].lat, out[0].lon) == (pytest.approx(45.25, abs=1e-4), pytest.approx(28.40, abs=1e-4))
    assert out[0].cov[0, 0] == pytest.approx(300.0 ** 2) and out[0].t == pytest.approx(4.0)
    assert any("triangulare" in n for n in ad.sources["SAC6/SIC2"].notes)
