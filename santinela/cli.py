"""Demonstratie: ruleaza un scenariu si scrie COP-ul (GeoJSON) si jurnalul.

    python -m santinela.cli --scenario dunare --out out/
    python -m santinela.cli --scenario val --out out/
    python -m santinela.cli --calibrate exercitiu.csv
    python -m santinela.cli --inspect captura.bin flux.nmea --config surse.json
    python -m santinela.cli --fetch-sapient sapient_proto --fetch-sapient-vectors vectori_dstl
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


def conf_all(tr):
    return [t for tid, t in sorted(tr.tracks.items()) if t.status == CONFIRMED and t.group_id is None]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="santinela")
    ap.add_argument("--scenario", choices=["dunare", "val"], default="dunare")
    ap.add_argument("--radar-mode", choices=["plots", "tracks", "off"], default="plots",
                    help="radarul simulat trimite detectii, trasee sau lipseste")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default="out")
    ap.add_argument("--drones-js", default=None, help="fisele site-ului, pentru vitezele pe clase")
    ap.add_argument("--calibrate", default=None, help="CSV din exercitiul de coordonate (Faza 0)")
    ap.add_argument("--inspect", nargs="+", default=None,
                    help="fisiere de captura (ASTERIX binar, NMEA, SAPIENT/JSON): recunoaste formatul si "
                         "spune ce configurare lipseste")
    ap.add_argument("--config", default=None, help="configurarea surselor (JSON) pentru --inspect")
    ap.add_argument("--conventions", action="store_true",
                    help="conventiile semantice folosite de decodoare, cu statutul si sursa fiecareia")
    ap.add_argument("--fetch-sapient", metavar="DIR", default=None,
                    help="descarca definitiile oficiale SAPIENT v2.0 si le verifica pe amprente")
    ap.add_argument("--fetch-sapient-vectors", metavar="DIR", default=None,
                    help="descarca vectorii de test ai validatorului Dstl si le verifica amprentele")
    ap.add_argument("--fetch-asterix-captures", metavar="DIR", default=None,
                    help="descarca capturile ASTERIX reale de test (Croatia Control) si le verifica amprentele")
    ap.add_argument("--sapient-server", metavar="PORT", type=int, default=None,
                    help="porneste nodul de fuziune SAPIENT (cere --sapient-proto)")
    ap.add_argument("--sapient-proto", metavar="DIR", default=None, help="directorul cu .proto oficiale verificate")
    a = ap.parse_args(argv)

    if a.conventions:
        from .conventions import report
        for c in report():
            print(f"[{c['statut']:9s}] {c['cheie']}: {c['valoare']}\n            sursa: {c['sursa']}")
        return 0

    if a.fetch_sapient or a.fetch_sapient_vectors or a.fetch_asterix_captures:
        if a.fetch_sapient:
            from .sapient_proto import fetch
            print(f"definitii SAPIENT descarcate si verificate: {fetch(a.fetch_sapient)}")
        if a.fetch_sapient_vectors:
            from .sapient_proto import fetch_vectors
            print(f"vectori de test Dstl descarcati si verificati: {fetch_vectors(a.fetch_sapient_vectors)}")
        if a.fetch_asterix_captures:
            from .pcap import fetch_asterix_captures
            print(f"capturi ASTERIX descarcate si verificate: {fetch_asterix_captures(a.fetch_asterix_captures)}")
        return 0

    if a.sapient_server is not None:
        import time as _time
        from .sapient_node import SapientFusionNode, SapientServer
        from .sapient_proto import load_schema
        from .universal import UniversalAdapter
        if not a.sapient_proto:
            ap.error("--sapient-server cere --sapient-proto DIR (vezi --fetch-sapient)")
        node = SapientFusionNode(load_schema(a.sapient_proto), UniversalAdapter())
        srv = SapientServer(node, "0.0.0.0", a.sapient_server)
        print(f"nod de fuziune SAPIENT {node.node_id} pe portul {srv.start()} (Ctrl+C pentru oprire)")
        seen = 0
        try:
            while True:
                _time.sleep(1.0)
                for ev in node.log[seen:]:
                    print(ev)
                seen = len(node.log)
        except KeyboardInterrupt:
            srv.stop()
        return 0

    if a.inspect:
        from .universal import UniversalAdapter
        ad = UniversalAdapter.from_config(a.config) if a.config else UniversalAdapter()
        n = 0
        for path in a.inspect:
            n += len(ad.feed_file(path, source=None))
        print(json.dumps({"intrari_pentru_tracker": n, **ad.report()}, indent=2, ensure_ascii=False))
        req = ad.requirements()
        if req:
            print("\nCe lipseste:")
            for r in req:
                print("  - " + r)
            print("\nSablon de configurare (completati si dati cu --config):")
            print(json.dumps(ad.config_template(), indent=2, ensure_ascii=False))
        return 0

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
    # traseele confirmate si ca evenimente Cursor-on-Target, pentru TAK/ATAK
    from pathlib import Path
    from .cot import track_to_cot, utc_from_tod
    utc = utc_from_tod(scn.tod_ref + now)
    events = [track_to_cot(t, tr.frame, utc) for t in conf_all(tr)]
    (Path(a.out) / "cot.xml").write_text("\n".join(events), encoding="utf-8")

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
