"""SAPIENT pe vectorii oficiali ai validatorului Dstl (Test Harness, commit 6a2999d).

Ruleaza cand SANTINELA_SAPIENT_PROTO (definitiile .proto oficiale) si SANTINELA_SAPIENT_VECTORS
(vectorii, descarcati cu ``python -m santinela.sapient_proto DIR_PROTO DIR_VECTORI``) sunt setate.
Regulile suplimentare (dstl_rules) se testeaza si fara ele.
"""
import json
import os
import struct
from pathlib import Path

import pytest

from santinela.sapient_proto import VECTORS, dstl_rules

PROTO = os.environ.get("SANTINELA_SAPIENT_PROTO")
VEC = os.environ.get("SANTINELA_SAPIENT_VECTORS")
needs = pytest.mark.skipif(not (PROTO and VEC), reason="vectorii/definitiile SAPIENT oficiale nu sunt setate")
MAN = json.loads(VECTORS.read_text(encoding="utf-8"))
UUID = "a8654cdf-4328-47de-81fa-c495589e30c8"
ULID = "01GGYFBAXGDG7AGAHRZ6XSNY12"


def test_manifest_lists_valid_and_invalid_vectors():
    names = list(MAN["files"])
    assert sum(n.startswith("True/") for n in names) == 38
    assert sum(n.startswith("False/") for n in names) == 193
    assert set(MAN["stale"]) <= set(names)


def test_dstl_rules():
    assert dstl_rules({"registration": {"icd_version": "BSI Flex 335 v2.0", "dependent_nodes": [UUID]}}) == []
    issues = dstl_rules({"registration": {"icd_version": "v1", "dependent_nodes": ["x"],
                                          "mode_definition": [{"task": {"region_definition": {}}}]}})
    assert len(issues) == 3
    assert dstl_rules({"task": {"command": {"follow": {"follow_object_id": "abc"}}}})
    assert not dstl_rules({"task": {"command": {"follow": {"follow_object_id": ULID}}}})
    det = {"detection_confidence": 1.2, "prediction_location": {},
           "derived_detection": [{"node_id": "x", "object_id": ULID}],
           "classification": [{"type": "UAV", "confidence": 0.5}]}
    issues = dstl_rules({"detection_report": det})
    assert len(issues) == 4                       # incredere, predictie, timestamp, node_id
    assert dstl_rules({"alert": {"confidence": -0.1, "ranking": 2}})


def _f32_equal(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_f32_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_f32_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, float) and isinstance(b, float) and a != b:
        return struct.unpack("<f", struct.pack("<f", a))[0] == b
    return a == b


@needs
class TestOfficialVectors:
    @pytest.fixture(scope="class")
    @staticmethod
    def sch():
        from santinela.sapient_proto import load_schema
        return load_schema(PROTO)

    def _load(self, rel):
        return json.loads((Path(VEC) / rel).read_text(encoding="utf-8-sig"))

    def test_valid_vectors_have_no_deviations_and_round_trip(self, sch):
        from santinela.sapient_proto import validate
        for rel in (n for n in MAN["files"] if n.startswith("True/")):
            unknown = []
            d = sch.from_json("SapientMessage", self._load(rel), unknown=unknown)
            assert unknown == [], rel
            assert validate(sch, d) == [], rel
            assert _f32_equal(d, sch.decode("SapientMessage", sch.encode("SapientMessage", d))), rel

    def test_invalid_vectors_are_detected(self, sch):
        from santinela.sapient_proto import validate
        missed = []
        for rel in (n for n in MAN["files"] if n.startswith("False/")):
            try:
                d = sch.from_json("SapientMessage", self._load(rel), unknown=[])
            except Exception:
                continue  # respins la conversie (ex. timestamp invalid)
            if not validate(sch, d):
                missed.append(rel)
        assert sorted(missed) == sorted(MAN["stale"])

    def test_valid_vectors_flow_through_fusion_node(self, sch):
        from santinela.geo import LocalFrame
        from santinela.sapient_node import SapientFusionNode
        from santinela.universal import UniversalAdapter
        node = SapientFusionNode(sch, UniversalAdapter(frame=LocalFrame(45.27, 28.46)))
        for rel in (n for n in MAN["files"] if n.startswith("True/")):
            d = sch.from_json("SapientMessage", self._load(rel), unknown=[])
            node.handle(d, b"")
        items = node.take_items()
        assert items  # detectiile din mostre ajung in tracker
        # mostrele Dstl pun lat/lon in campuri UTM, cu latitudinea pe x: alegem ordinea plauzibila (Marea Britanie)
        lats = {round(getattr(i, "lat", 0.0)) for i in items if hasattr(i, "lat")}
        assert lats <= {51}
