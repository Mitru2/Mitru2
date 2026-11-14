"""Capturi: cititorul pcap/pcapng si decodarea ASTERIX pe trafic real.

Capturile reale (Croatia Control, GPL-2) nu sunt incluse; se descarca cu
``python -c "from santinela.pcap import fetch_asterix_captures as f; f('captures')"`` si se
indica prin SANTINELA_ASTERIX_CAPTURES. Iesirile de referinta sunt ale decodorului lor, independent.
"""
import json
import os
import re
import struct
from collections import defaultdict
from pathlib import Path

import pytest

from santinela import asterix as A
from santinela import pcap
from santinela.geo import LocalFrame
from santinela.radar import RadarSite
from santinela.universal import UniversalAdapter, detect_format

CAP = os.environ.get("SANTINELA_ASTERIX_CAPTURES")
needs = pytest.mark.skipif(not CAP, reason="capturile ASTERIX de test nu sunt descarcate")
NM = 1852.0


def test_pcap_classic_and_feed_file(tmp_path):
    blk = A.encode_block([{"sac": 1, "sic": 7, "tod": 36001.0, "range_m": 5000.0, "azimuth_deg": 10.0}])
    data = pcap.build_udp_pcap([blk, blk])
    dg = pcap.read(data)
    assert len(dg) == 2 and dg[0].payload == blk and dg[0].dport == 8600 and dg[0].dst == "239.1.1.1"
    f = tmp_path / "radar.pcap"
    f.write_bytes(data)
    lat, lon = LocalFrame().to_latlon(0.0, 0.0)
    ad = UniversalAdapter(sites=[RadarSite("R1", lat, lon, sac=1, sic=7)], tod_ref=36000.0)
    assert len(ad.feed_file(f)) == 2


def test_pcapng_and_vlan():
    blk = A.encode_block([{"sac": 1, "sic": 7, "tod": 1.0, "range_m": 5000.0, "azimuth_deg": 10.0}])
    udp = struct.pack(">HHHH", 1, 2, 8 + len(blk), 0) + blk
    ip = struct.pack(">BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), 0, 0, 64, 17, 0, b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02") + udp
    eth = b"\x00" * 12 + b"\x81\x00\x00\x05" + b"\x08\x00" + ip   # cu eticheta VLAN 5

    def block(t, body):
        n = 12 + len(body) + (-len(body) % 4)
        return struct.pack("<II", t, n) + body + b"\x00" * (-len(body) % 4) + struct.pack("<I", n)

    shb = block(0x0A0D0D0A, struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1))
    idb = block(1, struct.pack("<HHI", 1, 0, 65535))
    epb = block(6, struct.pack("<IIIII", 0, 0, 1000, len(eth), len(eth)) + eth)
    dg = pcap.read(shb + idb + epb)
    assert len(dg) == 1 and dg[0].payload == blk


def test_oradis_framing():
    blk = A.encode_block([{"sac": 1, "sic": 7, "tod": 1.0, "range_m": 5000.0, "azimuth_deg": 10.0}])
    framed = b"".join(struct.pack(">H", 6 + len(blk)) + b"\x02\xbb\x40\x35" + blk for _ in range(2))
    assert A.strip_oradis(framed) == blk + blk
    assert detect_format(framed) == "asterix-oradis"
    assert A.strip_oradis(blk) is None


@needs
def test_cat034_048_real_traffic_item_bytes_and_values():
    ref, cur, item = [], None, None
    for line in open(Path(CAP) / "cat_034_048.txt", encoding="latin-1"):
        line = line.rstrip("\n")
        if line.startswith("Data Record"):
            cur = {"items": [], "raw": {}, "vals": defaultdict(dict)}
            ref.append(cur)
        elif line.startswith("Item ") and cur is not None:
            item = line.split()[1]
            cur["items"].append(item)
        elif line.startswith("[") and cur is not None:
            cur["raw"][item] = bytes.fromhex(line.strip("[] ").replace(" ", ""))
        elif line.startswith("\t") and cur is not None:
            m = re.match(r"\t([^:]+): (\S+)(?: \(([-\d.]+) ?\w*\))?", line)
            if m:
                cur["vals"][item][m.group(1).strip()] = (m.group(2), m.group(3))
    mine = []
    for d in pcap.read(Path(CAP) / "cat_034_048.pcap"):
        for cat, blk in A.iter_blocks(d.payload):
            mine += A.decode_block(blk, full=True)[1]
    assert len(mine) == len(ref) == 162
    n = 0
    for r, m in zip(ref, mine):
        for it in r["items"]:
            assert m["raw"][it] == r["raw"][it], (it, m["raw"][it].hex(), r["raw"][it].hex())
            n += 1
        v = r["vals"]
        if m["cat"] == 48 and "040" in v:
            assert m["range_m"] / NM == pytest.approx(float(v["040"]["RHO"][1]), abs=1e-6)
            assert m["azimuth_deg"] == pytest.approx(float(v["040"]["THETA"][1]), abs=1e-6)
        if m["cat"] == 48 and "090" in v:
            assert m["fl"] == pytest.approx(float(v["090"]["FlightLevel"][1]), abs=1e-6)
        if m["cat"] == 48 and "220" in v:
            assert m["icao"] == v["220"]["AircraftAddress"][0]
    assert n == 1940


@needs
def test_cat001_002_oradis_and_cat062_real_values():
    ref = [json.loads(line) for line in open(Path(CAP) / "cat_001_002_json.txt") if line.strip()]
    mine = []
    for d in pcap.read(Path(CAP) / "cat_001_002.pcap"):
        for cat, blk in A.iter_blocks(A.strip_oradis(d.payload)):
            mine += A.decode_block(blk, full=True)[1]
    assert len(mine) == len(ref) == 8
    for r, m in zip(ref, mine):
        hexd = bytes.fromhex(r["hexdata"])
        assert b"".join(m["raw"][it] for it in m["items"]) == hexd[-sum(len(m["raw"][it]) for it in m["items"]):]
        if r["cat"] == 1 and "I040" in r["CAT001"]:
            R = r["CAT001"]
            assert m["range_m"] / NM == pytest.approx(R["I040"]["RHO"], abs=1e-6)
            assert m["tod_trunc"] == pytest.approx(R["I141"]["Time"], abs=1e-6)
            assert m["fl"] == pytest.approx(R["I090"]["ModeC"], abs=1e-6)
            # decodorul de referinta foloseste LSB 0,22 kt; specificatia: 2^-14 NM/s = 0,2197 kt
            raw = struct.unpack(">H", m["raw"]["200"][:2])[0]
            assert R["I200"]["CalcGS"] == pytest.approx(raw * 0.22, abs=1e-6)
            assert m["groundspeed_mps"] / NM * 3600 == pytest.approx(raw * 2 ** -14 * 3600, abs=1e-9)
    ref = [json.loads(line) for line in open(Path(CAP) / "cat_062_065_json.txt") if line.strip()]
    mine = [r for d in pcap.read(Path(CAP) / "cat_062_065.pcap") for c, b in A.iter_blocks(d.payload)
            if c == 62 for r in A.decode_block(b)[1]]
    R = [r for r in ref if r["cat"] == 62][0]["CAT062"]
    m = mine[0]
    assert (m["lat"], m["lon"]) == (pytest.approx(R["I105"]["Lat"], abs=1e-6), pytest.approx(R["I105"]["Lon"], abs=1e-6))
    assert (m["vx_mps"], m["vy_mps"]) == (R["I185"]["Vx"], R["I185"]["Vy"])
    assert m["track_number"] == R["I040"]["TrkN"] and m["icao"] == R["I380"]["ADR"]["ADR"]
    assert m["height_m"] / 0.3048 == pytest.approx(R["I130"]["Alt"], abs=1e-3)


@needs
def test_nonconformant_cat062_capture_is_refused_not_misread():
    ad = UniversalAdapter(tod_ref=0.0)
    out = ad.feed_file(Path(CAP) / "asterix.pcap")
    assert ad.errors                                  # blocurile cu FRN rezervat/structura gresita sunt refuzate
    assert all(abs(o.x) < 2e6 and abs(o.y) < 2e6 for o in out)
