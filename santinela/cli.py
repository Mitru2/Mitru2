"""Demonstratie: ruleaza un scenariu si scrie COP-ul (GeoJSON) si jurnalul.

    python -m santinela.cli --scenario dunare --out out/
    python -m santinela.cli --scenario val --out out/
    python -m santinela.cli --calibrate exercitiu.csv
"""
from __future__ import annotations

import argparse
import json
import sys

from . import sim
from .calibration import estimate_population, load_csv
from .export import write
from .speeds import speed_table_from_site
from .track import CONFIRMED


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="santinela")
    ap.add_argument("--scenario", choices=["dunare", "val"], default="dunare")
    ap.add_argument("--radar-mode", choices=["plots", "tracks", "off"], default="plots",
                    help="radarul simulat trimite detectii, trasee sau lipseste")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default="out")
    ap.add_argument("--drones-js", default=None, help="fisele site-ului, pentru vitezele pe clase")
    ap.add_argument("--calibrate", default=None, help="CSV din exercitiul de coordonate (Faza 0)")
    a = ap.parse_args(argv)

    if a.calibrate:
        pp = estimate_population(load_csv(a.calibrate))
        print(json.dumps(pp.as_dict(), indent=2))
        return 0

    if a.scenario == "dunare":
        scn = sim.dunare(radar=a.radar_mode != "off",
                         radar_mode=a.radar_mode if a.radar_mode != "off" else "plots")
    else:
        scn = sim.parallel_wave()
    tr = sim.make_tracker(scn, speeds=speed_table_from_site(a.drones_js))
    items = sim.generate_reports(scn, seed=a.seed)
    sim.run(scn, tr, items)
    now = max(i.received_at for i in items)
    p = write(tr, now, a.out, scn.destinations, scn.observers, scn.radars)

    conf = [t for t in tr.tracks.values() if t.status == CONFIRMED and t.group_id is None]
    print(f"Scenariu: {scn.name} | rapoarte: {len(items)} | trasee: {len(tr.tracks)} "
          f"(confirmate: {len(conf)}) | grupuri: {len(tr.groups)} | ambigue in asteptare: {len(tr.pending)}")
    for t in sorted(conf, key=lambda t: t.track_id):
        print(f"  {t.track_id}: {len(t.measurements)} rapoarte, surse={len(t.sources)}, clasa={t.drone_class}, "
              f"scor={t.score:.1f}, operator_in_bucla={t.operator_in_loop}, "
              f"altitudine>={t.altitude_floor if t.altitude_floor is not None else 'necunoscuta (foarte jos)'}")
    for g in tr.groups.values():
        s = g.summary()
        print(f"  {s['group_id']}: ~{s['est_count']} tinte, curs {s['course_deg']} grd, {s['speed_mps']} m/s")
    print(f"COP: {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
