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


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "sapient_proto"
    print(f"descarcat si verificat: {fetch(target)}")
