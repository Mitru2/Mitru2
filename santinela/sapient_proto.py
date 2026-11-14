"""Definitiile oficiale SAPIENT (BSI Flex 335 v2.0): descarcare si verificare pe amprente.

Fisierele .proto nu sunt incluse in proiect (licenta lor nu a putut fi confirmata).
Se descarca din depozitul public Dstl si se verifica pe amprentele SHA-256 fixate in
``santinela/data/sapient_bsi_flex_335_v2_0.json``; orice diferenta opreste incarcarea.

    python -m santinela.sapient_proto ./sapient_proto        # descarca si verifica
"""
from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from pathlib import Path

from .protowire import ProtoSchema

MANIFEST = Path(__file__).parent / "data" / "sapient_bsi_flex_335_v2_0.json"


class ManifestMismatch(RuntimeError):
    pass


def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def verify(directory: str | Path) -> list[str]:
    """Lista diferentelor fata de manifest (goala = fisiere identice)."""
    m = manifest()
    d = Path(directory)
    problems = []
    for rel, digest in m["files"].items():
        f = d / rel
        if not f.exists():
            problems.append(f"lipseste {rel}")
        elif hashlib.sha256(f.read_bytes()).hexdigest() != digest:
            problems.append(f"amprenta diferita: {rel}")
    return problems


def fetch(directory: str | Path, timeout: float = 30.0) -> Path:
    m = manifest()
    d = Path(directory)
    for rel in m["files"]:
        remote = rel[len(m["remote_prefix_strip"]):] if rel.startswith(m["remote_prefix_strip"]) else rel
        data = urllib.request.urlopen(m["source"] + remote, timeout=timeout).read()
        out = d / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
    problems = verify(d)
    if problems:
        raise ManifestMismatch("; ".join(problems))
    return d


def load_schema(directory: str | Path, strict: bool = True) -> ProtoSchema:
    """Schema SAPIENT v2.0 dintr-un director verificat (``strict=False`` doar pentru teste/dezvoltare)."""
    problems = verify(directory)
    if problems and strict:
        raise ManifestMismatch("definitiile SAPIENT nu corespund manifestului: " + "; ".join(problems))
    return ProtoSchema.from_dir(directory)



# ------------------------------------------------------------------ reguli suplimentare (validatorul Dstl)
# Regulile din validatorul C# al Test Harness-ului Dstl (SapientServices/Data/Validation, Apache-2.0,
# commit 6a2999d) care nu sunt exprimate prin optiunile din .proto.
ICD_VERSION = "BSI Flex 335 v2.0"


def _is_uuid(v) -> bool:
    import uuid as _uuid
    try:
        _uuid.UUID(str(v))
        return True
    except ValueError:
        return False


def _is_ulid(v) -> bool:
    import re as _re
    return isinstance(v, str) and bool(_re.fullmatch(r"[0-7][0-9A-HJKMNP-TV-Za-hjkmnp-tv-z]{25}", v))


def dstl_rules(msg: dict) -> list[str]:
    """Abaterile fata de regulile suplimentare ale validatorului Dstl (pe forma decodata)."""
    out = []

    def scalar(v, path):
        if v is not None and not (0.0 <= float(v) <= 1.0):
            out.append(f"{path}: trebuie sa fie intre 0 si 1 ({v})")

    reg = msg.get("registration")
    if reg is not None:
        if reg.get("icd_version") not in (None, "") and reg.get("icd_version") != ICD_VERSION:
            out.append(f"Registration.icd_version: trebuie sa fie '{ICD_VERSION}' ({reg.get('icd_version')!r})")
        for k, n in enumerate(reg.get("dependent_nodes", []) or []):
            if n and not _is_uuid(n):
                out.append(f"Registration.dependent_nodes[{k}]: nu e UUID valid ({n!r})")
        for k, mode in enumerate(reg.get("mode_definition", []) or []):
            task = mode.get("task")
            if task is not None and task.get("concurrent_tasks") is None:
                out.append(f"Registration.mode_definition[{k}].task.concurrent_tasks: camp obligatoriu lipsa")
    task = msg.get("task")
    if task is not None:
        follow = (task.get("command") or {}).get("follow")
        if follow is not None and not _is_ulid(follow.get("follow_object_id")):
            out.append(f"Task.command.follow.follow_object_id: nu e ULID valid ({follow.get('follow_object_id')!r})")
    det = msg.get("detection_report")
    if det is not None:
        scalar(det.get("detection_confidence"), "DetectionReport.detection_confidence")
        pl = det.get("prediction_location")
        if pl is not None and ("location" in pl) == ("range_bearing" in pl):
            out.append("DetectionReport.prediction_location: trebuie exact una dintre location si range_bearing")
        for k, dd in enumerate(det.get("derived_detection", []) or []):
            p = f"DetectionReport.derived_detection[{k}]"
            if dd.get("timestamp") is None:
                out.append(f"{p}.timestamp: camp obligatoriu lipsa")
            if not _is_uuid(dd.get("node_id", "")):
                out.append(f"{p}.node_id: nu e UUID valid ({dd.get('node_id')!r})")
            if not _is_ulid(dd.get("object_id")):
                out.append(f"{p}.object_id: nu e ULID valid ({dd.get('object_id')!r})")
        for k, c in enumerate(det.get("classification", []) or []):
            scalar(c.get("confidence"), f"DetectionReport.classification[{k}].confidence")
        for k, b in enumerate(det.get("behaviour", []) or []):
            scalar(b.get("confidence"), f"DetectionReport.behaviour[{k}].confidence")
    alert = msg.get("alert")
    if alert is not None:
        scalar(alert.get("confidence"), "Alert.confidence")
        scalar(alert.get("ranking"), "Alert.ranking")
    return out


def validate(schema: ProtoSchema, msg: dict) -> list[str]:
    """Validarea completa: optiunile din .proto (obligatoriu/UUID/ULID) + regulile suplimentare Dstl."""
    return schema.validate("SapientMessage", msg) + dstl_rules(msg)


# ------------------------------------------------------------------ vectorii de test oficiali Dstl
VECTORS = Path(__file__).parent / "data" / "sapient_test_vectors.json"


def fetch_vectors(directory: str | Path, timeout: float = 30.0) -> Path:
    """Descarca vectorii validatorului Dstl (True/ = valide, False/ = invalide) si le verifica amprentele."""
    m = json.loads(VECTORS.read_text(encoding="utf-8"))
    d = Path(directory)
    for rel, digest in m["files"].items():
        url = m["source"] + m["commit"] + "/" + (m["prefix"] + rel).replace(" ", "%20")
        data = urllib.request.urlopen(url, timeout=timeout).read()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ManifestMismatch(f"amprenta diferita: {rel}")
        out = d / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
    return d


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "sapient_proto"
    print(f"descarcat si verificat: {fetch(target)}")
    if len(sys.argv) > 2:
        print(f"vectori de test: {fetch_vectors(sys.argv[2])}")
