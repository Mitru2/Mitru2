"""Viteze pe clase pentru portile cinematice.

Sursa unica de adevar sunt fisele site-ului (``drones.js``, campul ``ttx.speed``).
Daca fisierul e disponibil, trackerul il citeste la pornire cu
:func:`load_speeds_from_drones_js`; altfel foloseste tabelul de mai jos, copiat
din planul v2. Poarta este limita superioara x 1,3.
"""
from __future__ import annotations

import re
from pathlib import Path

from .config import DEFAULT

# km/h, limita superioara din fisa (si picaj unde exista)
BUILTIN_SPEEDS_KMH: dict[str, dict[str, float]] = {
    "fpv": {"cruise": 120},
    "supercam-s350": {"cruise": 120},
    "zala-421-16": {"cruise": 130},
    "lancet-3": {"cruise": 110, "dive": 300},
    "orlan-10": {"cruise": 150},
    "orlan-30": {"cruise": 150},
    "gerbera": {"cruise": 160},
    "geran-2": {"cruise": 185},
    "shahed-136": {"cruise": 185},
    "geran-3": {"cruise": 340},
    "geran-4": {"cruise": 500},  # o singura sursa secundara
    "banderol": {"cruise": 650},
    "dan-t": {"cruise": 650},
}

# Clase fara viteza in fisa -> plicul "clasa necunoscuta"
UNKNOWN_SPEED_CLASSES = {"molniya-2", "italmas", "v2u", "kub-bla"}


def normalize_class(name: str | None) -> str | None:
    if not name:
        return None
    s = name.strip().lower()
    s = re.sub(r"[\s_/]+", "-", s)
    return s


def gate_mps(upper_kmh: float, factor: float = DEFAULT.speed_gate_factor) -> float:
    return upper_kmh * factor / 3.6


class SpeedTable:
    def __init__(self, speeds_kmh: dict[str, dict[str, float]] | None = None,
                 factor: float = DEFAULT.speed_gate_factor,
                 unknown_vmax: float = DEFAULT.unknown_class_vmax):
        self.speeds_kmh = dict(speeds_kmh or BUILTIN_SPEEDS_KMH)
        self.factor = factor
        self.unknown_vmax = unknown_vmax

    def vmax(self, drone_class: str | None, dive: bool = False) -> float:
        """Poarta v_max (m/s) pentru clasa data."""
        c = normalize_class(drone_class)
        if c is None or c not in self.speeds_kmh:
            return self.unknown_vmax
        entry = self.speeds_kmh[c]
        if dive and "dive" in entry:
            return gate_mps(entry["dive"], self.factor)
        return gate_mps(entry["cruise"], self.factor)

    def vmax_any(self, drone_class: str | None) -> float:
        """Poarta cea mai larga a clasei (inclusiv picaj) - folosita in asociere."""
        c = normalize_class(drone_class)
        if c is None or c not in self.speeds_kmh:
            return self.unknown_vmax
        return gate_mps(max(self.speeds_kmh[c].values()), self.factor)

    def nominal_speed(self, drone_class: str | None) -> float | None:
        c = normalize_class(drone_class)
        if c is None or c not in self.speeds_kmh:
            return None
        return self.speeds_kmh[c]["cruise"] / 3.6


_SPEED_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:[–\-]\s*(\d+(?:[.,]\d+)?))?")


def parse_speed_text(text: str) -> dict[str, float] | None:
    """Extrage limitele superioare dintr-un text de tip ``"~80–110 km/h; ~300 in picaj"``."""
    out: dict[str, float] = {}
    for part in re.split(r"[;,]", text):
        nums = [float(x.replace(",", ".")) for m in _SPEED_RE.finditer(part) for x in m.groups() if x]
        if not nums:
            continue
        key = "dive" if re.search(r"picaj|pica|dive", part, re.I) else "cruise"
        out[key] = max(out.get(key, 0.0), max(nums))
    return out or None


def load_speeds_from_drones_js(path: str | Path) -> dict[str, dict[str, float]]:
    """Citeste ``drones.js`` si intoarce ``{clasa: {cruise, dive?}}`` in km/h.

    Parser tolerant: cauta perechi ``name``/``id`` urmate de ``ttx: { ... speed: "..." }``.
    """
    src = Path(path).read_text(encoding="utf-8")
    result: dict[str, dict[str, float]] = {}
    pattern = re.compile(
        r"(?:id|name)\s*:\s*['\"]([^'\"]+)['\"][\s\S]*?ttx\s*:\s*\{[\s\S]*?speed\s*:\s*['\"`]([^'\"`]+)['\"`]")
    for m in pattern.finditer(src):
        cls = normalize_class(m.group(1))
        parsed = parse_speed_text(m.group(2))
        if cls and parsed and "cruise" in parsed:
            result[cls] = parsed
    return result


def speed_table_from_site(path: str | Path | None) -> SpeedTable:
    if path and Path(path).exists():
        loaded = load_speeds_from_drones_js(path)
        if loaded:
            merged = dict(BUILTIN_SPEEDS_KMH)
            merged.update(loaded)
            return SpeedTable(merged)
    return SpeedTable()
