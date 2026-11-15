"""Starea senzorilor si a SDPS (I034/050, CAT063, CAT065) si configuratia INCS (CAT016).

Structura si textele tabelelor vin din asterix-specs (generate); formulele de corectie a biasurilor
sunt cele din observatiile I063/080-091. Pe trafic real: I034/050 si CAT065 comparate cu iesirea
decodorului de referinta Croatia Control (testele cu SANTINELA_ASTERIX_CAPTURES).
"""
import json
import math
import os
import re
from pathlib import Path

import numpy as np
import pytest

from santinela import asterix as A
from santinela import pcap
from santinela.asterix_spec import CATEGORIES, TABLES
from santinela.geo import LocalFrame
from santinela.measurements import SensorDetection
from santinela.radar import RadarPlot, RadarRegistration, RadarSite, convert_plot
from santinela.tracker import Tracker
from santinela.universal import UniversalAdapter

NM = 1852.0
FR = LocalFrame(45.27, 28.46)
TOD0 = 36000.0
CAP = os.environ.get("SANTINELA_ASTERIX_CAPTURES")
needs = pytest.mark.skipif(not CAP, reason="SANTINELA_ASTERIX_CAPTURES nu e setat (capturi Croatia Control)")


def _site(sac=1, sic=2, **kw):
    lat, lon = FR.to_latlon(0.0, 0.0)
    return RadarSite("R1", lat, lon, sac=sac, sic=sic, min_alt=0.0, **kw)


def _cat034(sac=1, sic=2, **status):
    return A.encode_generic(34, [{"010": {"SAC": sac, "SIC": sic}, "000": 2, "030": TOD0, "050": status}])


def _plot(sac=1, sic=2, az=90.0, rng=5000.0):
    return A.encode_block([{"sac": sac, "sic": sic, "tod": TOD0 + 1, "range_m": rng, "azimuth_deg": az}])


COM_OK = {"NOGO": 0, "RDPC": 0, "RDPR": 0, "OVLRDP": 0, "OVLXMT": 0, "MSC": 0, "TSV": 0}
PSR_OK = {"ANT": 0, "CHAB": 1, "OVL": 0, "MSC": 0}


# ---------------------------------------------------------------- generatorul
def test_generated_names_are_unique_and_every_table_has_texts():
    def names(sp):
        if sp[0] == "F":
            return [[e[0] for e in sp[1]]]
        if sp[0] == "X":
            return [[e[0] for p in sp[1] for e in p]]
        if sp[0] == "R":
            return [[e[0] for e in sp[2]]]
        if sp[0] == "RF":
            return [[e[0] for e in sp[1]]]
        if sp[0] == "C":
            return [n for s in sp[1] if s for n in names(s[1])]
        return []

    for cat, d in CATEGORIES.items():
        for item, (_, sp) in d["items"].items():
            for group in names(sp):
                named = [n for n in group if n]
                assert len(named) == len(set(named)), (cat, item, named)
    # grupurile imbricate pastreaza prefixul (erau suprapuse inainte de verificare)
    assert {"TTF.EP", "SPO.EP", "TTF.VAL", "SPO.VAL"} <= set(TABLES["I063/060"])
    assert {"ADSB.EP", "SCN.EP", "PAI.EP"} <= set(TABLES["I048/020"])
    assert TABLES["I063/060"]["CON"][3] == "Not currently connected"
    assert TABLES["I065/050"][""][3] == "Main radar out of service"
    assert TABLES["I034/050"]["COM.NOGO"][1].startswith("Operational use of System is inhibited")


def test_generic_decoder_keeps_nested_groups_apart():
    blk = A.encode_generic(63, [{"010": {"SAC": 25, "SIC": 100}, "030": TOD0, "050": {"SAC": 1, "SIC": 2},
                                 "060": {"CON": 0, "PSR": 0, "SSR": 0, "MDS": 0, "ADS": 0, "MLT": 0,
                                         "OPS": 0, "ODP": 0, "OXT": 0, "MSC": 0, "TSV": 0, "NPW": 0,
                                         "TTF.EP": 1, "TTF.VAL": 0, "SPO.EP": 1, "SPO.VAL": 1}}])
    f = A.decode_block(blk, full=True)[1][0]["fields"]["060"]
    assert (f["TTF.EP"], f["TTF.VAL"], f["SPO.EP"], f["SPO.VAL"]) == (1, 0, 1, 1)


# ---------------------------------------------------------------- I034/050
def test_cat034_nogo_discards_radar_data_until_released():
    site = _site()
    ad = UniversalAdapter(sites=[site], frame=FR, tod_ref=TOD0)
    assert len(ad.feed(_plot())) == 1
    ad.feed(_cat034(COM={**COM_OK, "NOGO": 1}, PSR=PSR_OK))
    assert ad.feed(_plot()) == [] and ad.stats["aruncate_nogo"] == 1
    assert site.active is False and any("NOGO" in e[1] for e in ad.errors)
    ad.feed(_cat034(COM=COM_OK, PSR=PSR_OK))
    assert len(ad.feed(_plot())) == 1 and site.active is True


def test_cat034_psr_channel_or_overload_suspends_negative_info():
    site = _site()
    tr = Tracker(frame=FR, radars=[site])
    ad = UniversalAdapter(sites=[site], frame=FR, tod_ref=TOD0, tracker=tr)
    ad.feed(_cat034(COM=COM_OK, PSR={**PSR_OK, "CHAB": 0}))
    assert site.active is False
    assert ad.sensor_state["R1"]["motiv"] == "PSR: No channel selected"
    ad.feed(_cat034(COM={**COM_OK, "OVLRDP": 1}, PSR=PSR_OK))
    assert site.active is False and "suprasarcina" in ad.sensor_state["R1"]["motiv"]
    ad.feed(_cat034(COM=COM_OK, PSR=PSR_OK))
    assert site.active is True
    ev = [e for e in tr.journal if e["event"] == "stare_senzor"]
    assert [e["activ"] for e in ev] == [False, True]
    assert len(ad.feed(_plot())) == 1               # suprasarcina nu arunca datele, doar informatia negativa


def test_cat034_ssr_only_station_never_gives_negative_info_for_drones():
    site = _site()
    ad = UniversalAdapter(sites=[site], frame=FR, tod_ref=TOD0)
    ad.feed(_cat034(COM=COM_OK, SSR={"ANT": 0, "CHAB": 1, "OVL": 0, "MSC": 0}))
    assert site.negative_info is False and site.coverage_radius(1000.0) is None
    assert any("doar SSR/Mode S" in n for n in ad.sources["SAC1/SIC2"].notes)


def test_cat034_rdpr_resets_track_numbers_once_per_event():
    site = _site()
    tr = Tracker(frame=FR, radars=[site])
    tr.ext_map[("R1", 5)] = "T0001"
    tr.ext_map[("ALT", 5)] = "T0002"
    ad = UniversalAdapter(sites=[site], frame=FR, tod_ref=TOD0, tracker=tr)
    ad.feed(_cat034(COM={**COM_OK, "RDPR": 1}, PSR=PSR_OK))
    assert tr.ext_map == {("ALT", 5): "T0002"}
    tr.ext_map[("R1", 6)] = "T0003"
    ad.feed(_cat034(COM={**COM_OK, "RDPR": 1}, PSR=PSR_OK))      # acelasi eveniment, inca semnalat
    assert ("R1", 6) in tr.ext_map
    assert [e["event"] for e in tr.journal].count("numere_traseu_resetate") == 1


def test_status_before_position_is_applied_when_cat034_teaches_the_site():
    ad = UniversalAdapter(frame=FR, tod_ref=TOD0)
    blk = A.encode_generic(34, [{"010": {"SAC": 7, "SIC": 7}, "000": 2, "030": TOD0,
                                 "050": {"COM": COM_OK, "PSR": {**PSR_OK, "CHAB": 0}},
                                 "120": {"HGT": 100.0, "LAT": 45.3, "LON": 28.5}}])
    ad.feed(blk)
    site = ad._by_asterix[(7, 7)]
    assert site.active is False


# ---------------------------------------------------------------- CAT063
def _cat063(con=0, psr=0, **extra):
    rec = {"010": {"SAC": 25, "SIC": 100}, "030": TOD0 + 2, "050": {"SAC": 1, "SIC": 2},
           "060": {"CON": con, "PSR": psr, "SSR": 0, "MDS": 0, "ADS": 0, "MLT": 0}}
    rec.update(extra)
    return A.encode_generic(63, [rec])


def test_cat063_bias_formulas_from_spec_applied_to_plots():
    site = _site()
    tr = Tracker(frame=FR, radars=[site])
    ad = UniversalAdapter(sites=[site], frame=FR, tod_ref=TOD0, tracker=tr)
    b_az, b_r_nm, gain = 0.5, 0.05, 0.001
    ad.feed(_cat063(**{"091": b_az, "090": {"PRG": gain, "PRB": b_r_nm}, "070": 12.0}))
    reg = tr.registrations["R1"]
    assert reg.external == "CAT063 SAC25/SIC100"
    assert math.degrees(reg.bias_az) == pytest.approx(b_az, abs=360 / 2 ** 16)
    assert reg.bias_range == pytest.approx(b_r_nm * NM, abs=NM / 128)
    assert reg.range_gain == pytest.approx(gain, abs=1e-5)
    assert reg.clock_offset == 0.0                    # I063/070: semnul nu e definit, nu se aplica
    assert any("I063/070" in n for n in ad.sources["SAC25/SIC100"].notes)
    # theta_c = theta_m - b_az ; rho_c = (rho_m - b_r) / (1 + gain)
    true_r = 5000.0
    plot = RadarPlot("p", "R1", 1.0, (true_r * (1 + reg.range_gain)) + reg.bias_range,
                     90.0 + math.degrees(reg.bias_az))
    m = convert_plot(plot, site, reg)
    assert m.z == pytest.approx([true_r, 0.0], abs=0.5)
    # aceleasi valori a doua oara: fara intrari noi in jurnal
    ad.feed(_cat063(**{"091": b_az, "090": {"PRG": gain, "PRB": b_r_nm}}))
    assert [e["event"] for e in tr.journal].count("inregistrare_radar_externa") == 1


def test_cat063_external_registration_is_only_compared_by_own_estimate():
    site = _site()
    tr = Tracker(frame=FR, radars=[site])
    tr.set_registration("R1", math.radians(0.3), 60.0, 0.0, "CAT063 SAC25/SIC100")
    rng = np.random.default_rng(3)
    triples = []
    # trei aeronave pe directii diferite (cu una singura, decalajul de ceas nu se separa de bias)
    for x0, y0, vx, vy in ((8000.0, 3000.0, 50.0, -30.0), (-6000.0, 9000.0, -20.0, -60.0),
                           (2000.0, -12000.0, 70.0, 10.0)):
        for k in range(20):
            x, y = x0 + vx * k, y0 + vy * k
            r, az = math.hypot(x, y), math.degrees(math.atan2(x, y))
            plot = RadarPlot(f"p{x0}{k}", "R1", float(k), r + 60.0 + rng.normal(0, 5),
                             az + 0.3 + rng.normal(0, 0.02))
            triples.append((plot, np.array([x, y, vx, vy]), np.eye(2) * 1.0))
    assert tr.register_radar("R1", triples, n_ref=60, source="adsb")
    reg = tr.registrations["R1"]
    assert (math.degrees(reg.bias_az), reg.bias_range) == (pytest.approx(0.3), pytest.approx(60.0))
    cmp = [e for e in tr.journal if e["event"] == "inregistrare_radar_comparatie"][-1]
    assert abs(cmp["dif_az_deg"]) < 0.05 and abs(cmp["dif_range_m"]) < 10.0


def test_cat063_sensor_states_and_spoofing():
    site = _site()
    tr = Tracker(frame=FR, radars=[site])
    ad = UniversalAdapter(sites=[site], frame=FR, tod_ref=TOD0, tracker=tr)
    ad.feed(_cat063(con=3))
    assert site.active is False and ad.sensor_state["R1"]["conexiune"] == "Not currently connected"
    ad.feed(_cat063(con=0, psr=1))
    assert site.active is False and ad.sensor_state["R1"]["motiv"] == "PSR NOGO"
    ad.feed(_cat063(con=0, psr=0))
    assert site.active is True
    rec = {"CON": 0, "PSR": 0, "SSR": 0, "MDS": 0, "ADS": 0, "MLT": 0, "OPS": 0, "ODP": 0, "OXT": 0,
           "MSC": 0, "TSV": 0, "NPW": 1, "TTF.EP": 0, "TTF.VAL": 0, "SPO.EP": 1, "SPO.VAL": 1}
    ad.feed(_cat063(**{"060": rec}))
    assert site.active is False and "No plots being received" in ad.sensor_state["R1"]["motiv"]
    assert any("spoofing" in e[1] for e in ad.errors)
    assert any(e["event"] == "spoofing_senzor" for e in tr.journal)


def test_cat063_unknown_sensor_is_reported_not_applied():
    ad = UniversalAdapter(frame=FR, tod_ref=TOD0)
    ad.feed(_cat063(**{"091": 0.2}))
    assert ad.sensor_bias["SAC1/SIC2"]["psr_az_deg"] == pytest.approx(0.2, abs=0.006)
    assert any("neconfigurat" in n for n in ad.sources["SAC25/SIC100"].notes)


# ---------------------------------------------------------------- CAT065
def test_cat065_sdps_status_batches_and_service_reports():
    tr = Tracker(frame=FR)
    ad = UniversalAdapter(frame=FR, tod_ref=TOD0, tracker=tr)
    ad.feed(A.encode_generic(65, [
        {"010": {"SAC": 25, "SIC": 100}, "000": 1, "015": 1, "030": TOD0,
         "040": {"NOGO": 1, "OVL": 0, "TSV": 1, "PSS": 2, "STTN": 0}},
        {"010": {"SAC": 25, "SIC": 100}, "000": 2, "015": 1, "030": TOD0 + 4, "020": 17},
        {"010": {"SAC": 25, "SIC": 100}, "000": 3, "015": 1, "030": TOD0 + 5, "050": 14}]))
    s = ad.sdps_status["SAC25/SIC100"]
    assert (s["stare"], s["timp_invalid"], s["sdps_selectat"], s["lot"]) == ("Degraded", True, "SDPS-2 selected", 17)
    assert ad.sdps_events[-1]["eveniment"] == "Service synchronised on backup radar"
    notes = ad.sources["SAC25/SIC100"].notes
    assert any("SDPS Degraded" in n for n in notes) and any("timp valida" in n for n in notes)
    assert any(e["event"] == "serviciu_sdps" for e in tr.journal)
    assert "stare_sdps" in ad.report()


# ---------------------------------------------------------------- CAT016
def test_cat016_reference_point_used_for_cat015_polar_reports():
    srp_lat, srp_lon = FR.to_latlon(500.0, 0.0)          # la 500 m est de pozitia configurata
    lat, lon = FR.to_latlon(0.0, 0.0)
    ad = UniversalAdapter(sites=[RadarSite("INCS", lat, lon, sac=4, sic=1)], frame=FR, tod_ref=TOD0)
    ad.feed(A.encode_generic(16, [{"010": {"SAC": 4, "SIC": 1}, "000": 1, "140": TOD0,
                                   "400": {"LAT": srp_lat, "LON": srp_lon}, "405": 80.0,
                                   "410": [{"TID": 1, "LAT": 45.0, "LON": 28.0, "ALT": 300.0,
                                            "TTO": 0.0, "ATO": 0.0, "PCI": 7}],
                                   "420": [{"RID": 2, "LAT": srp_lat, "LON": srp_lon, "ALT": 80.0}]}]))
    notes = ad.sources["SAC4/SIC1"].notes
    assert any("difera cu 500 m" in n for n in notes) and any("1 emitatoare, 1 receptoare" in n for n in notes)
    out = ad.feed(A.encode_generic(15, [{"010": {"SAC": 4, "SIC": 1}, "000": {"MT": 1, "RG": 0},
                                         "020": {"MOMU": 0, "TTAX": 0}, "145": TOD0 + 1,
                                         "625": {"R": 5000.0}, "627": {"AZ": 90.0}}]))
    assert isinstance(out[0], SensorDetection)
    assert FR.to_xy(out[0].lat, out[0].lon) == pytest.approx([5500.0, 0.0], abs=2.0)


def test_cat016_teaches_unknown_incs_sensor():
    srp_lat, srp_lon = FR.to_latlon(-2000.0, 1000.0)
    ad = UniversalAdapter(frame=FR, tod_ref=TOD0)
    ad.feed(A.encode_generic(16, [{"010": {"SAC": 4, "SIC": 9}, "000": 1, "140": TOD0,
                                   "400": {"LAT": srp_lat, "LON": srp_lon}, "405": 80.0}]))
    out = ad.feed(A.encode_generic(15, [{"010": {"SAC": 4, "SIC": 9}, "000": {"MT": 1, "RG": 0},
                                         "020": {"MOMU": 0, "TTAX": 0}, "145": TOD0 + 1,
                                         "625": {"R": 3000.0}, "627": {"AZ": 0.0}}]))
    assert FR.to_xy(out[0].lat, out[0].lon) == pytest.approx([-2000.0, 4000.0], abs=2.0)
    assert ad._by_asterix[(4, 9)].negative_info is False


# ---------------------------------------------------------------- trafic real
@needs
def test_real_cat034_status_bits_match_reference_decoder():
    ref, item = [], None
    for line in open(Path(CAP) / "cat_034_048.txt", encoding="latin-1"):
        if line.startswith("Item "):
            item = line.split()[1]
            if item == "050":
                ref.append([])
        elif item == "050" and line.startswith("\t") and not line.startswith("\tspare"):
            m = re.match(r"\t[^:]+: (\d+)", line)
            if m:
                ref[-1].append(int(m.group(1)))
    mine = []
    for d in pcap.read(Path(CAP) / "cat_034_048.pcap"):
        for cat, blk in A.iter_blocks(d.payload):
            if cat == 34:
                mine += [r["fields"]["050"] for r in A.decode_block(blk, full=True)[1] if "050" in r["fields"]]
    assert len(mine) == len(ref) > 0
    for r, m in zip(ref, mine):
        flat = [v for part in ("COM", "PSR", "SSR", "MDS") if part in m for v in m[part].values()]
        assert flat == r
    # adaptorul: statiile fara PSR nu dau informatie negativa; SIC11 are PSR pe canalul A
    ad = UniversalAdapter(tod_ref=0.0, sites=[RadarSite(f"R{k}", 43.5, 16.4, sac=25, sic=k) for k in (11, 12, 14)])
    ad.feed_file(Path(CAP) / "cat_034_048.pcap")
    s = {k: ad._by_asterix[(25, k)] for k in (11, 12, 14)}
    assert s[11].negative_info and s[11].active
    assert not s[12].negative_info and not s[14].negative_info


@needs
def test_real_cat065_matches_reference_decoder():
    ref = [json.loads(line) for line in open(Path(CAP) / "cat_062_065_json.txt") if line.strip()]
    R = [r for r in ref if r["cat"] == 65][0]["CAT065"]
    mine = [r for d in pcap.read(Path(CAP) / "cat_062_065.pcap") for c, b in A.iter_blocks(d.payload)
            if c == 65 for r in A.decode_block(b, full=True)[1]]
    f = mine[0]["fields"]
    assert f["010"] == R["I010"] and f["000"] == R["I000"]["Typ"] and f["015"] == R["I015"]["SID"]
    assert f["030"] == R["I030"]["ToD"] and f["020"] == R["I020"]["BTN"]
    ad = UniversalAdapter(tod_ref=0.0)
    ad.feed_file(Path(CAP) / "cat_062_065.pcap")
    assert ad.sdps_status["SAC25/SIC100"]["lot"] == 1
    assert ad.sdps_status["SAC25/SIC100"]["ultimul_mesaj"] == "End of Batch"
