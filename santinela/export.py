"""Export GeoJSON pentru harta (Faza 2): traseu netezit, elipse, con T+5/10/15, grupuri, destinatii."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from .destinations import destination_probabilities
from .geo import ellipse_points
from .imm import H
from .track import CONFIRMED, DELETED


def _poly(frame, mean, cov, chi2=5.991):
    pts = ellipse_points(np.asarray(mean[:2]), np.asarray(cov)[:2, :2], chi2)
    ring = [list(reversed(frame.to_latlon(*p))) for p in pts]
    ring.append(ring[0])
    return {"type": "Polygon", "coordinates": [ring]}


def _pt(frame, xy):
    lat, lon = frame.to_latlon(float(xy[0]), float(xy[1]))
    return {"type": "Point", "coordinates": [lon, lat]}


def _circle(frame, center, radius, n=72):
    ring = []
    for k in range(n + 1):
        a = 2 * math.pi * k / n
        lat, lon = frame.to_latlon(center[0] + radius * math.sin(a), center[1] + radius * math.cos(a))
        ring.append([lon, lat])
    return {"type": "Polygon", "coordinates": [ring]}


def to_geojson(tracker, now: float, destinations: list | None = None, observers: list | None = None,
               radars: list | None = None, friendly: list | None = None) -> dict:
    fr = tracker.frame
    feats = []
    for tid in sorted(tracker.tracks):
        tr = tracker.tracks[tid]
        if tr.status == DELETED or tr.group_id is not None or not tr.steps:
            continue
        times, xs, Ps = tr.smoothed()
        props = {
            "track_id": tid, "status": tr.status, "class": tr.drone_class,
            "score": round(tr.score, 2), "rule": tr.confirm_rule,
            "sources": sorted(tr.sources), "n": len(tr.measurements),
            "human_only": tr.human_only, "operator_in_loop": tr.operator_in_loop,
            "emissions": tr.emissions,
            "altitude_floor_m": tr.altitude_floor,
            "cooperative_id": tr.cooperative_id,
            "speed_kmh": round(float(math.hypot(*tr.state.x[2:])) * 3.6),
        }
        if len(xs) > 1:
            line = [list(reversed(fr.to_latlon(x[0], x[1]))) for x in xs]
            feats.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": line},
                          "properties": {**props, "layer": "traseu_netezit"}})
        s = tr.state
        feats.append({"type": "Feature", "geometry": _pt(fr, s.x), "properties": {**props, "layer": "pozitie"}})
        feats.append({"type": "Feature", "geometry": _poly(fr, s.x, s.P), "properties": {**props, "layer": "elipsa95"}})
        if tr.status == CONFIRMED and now - tr.last_time <= tracker.params.track_timeout:
            for h, x, P in tr.forecast(now):
                feats.append({"type": "Feature", "geometry": _poly(fr, x, P),
                              "properties": {"track_id": tid, "layer": "con", "horizon_min": int(h // 60)}})
            if destinations and len(tr.steps) >= 3:
                for est in destination_probabilities(tr, destinations, now):
                    d = next(d for d in destinations if d.name == est.name)
                    feats.append({"type": "Feature", "geometry": _pt(fr, d.center),
                                  "properties": {"track_id": tid, "layer": "destinatii_umbra",
                                                 "destination": est.name, "p": round(est.probability, 3),
                                                 "eta_min": round(est.eta_s / 60, 1)}})
    for gid in sorted(tracker.groups):
        g = tracker.groups[gid]
        if not g.active:
            continue
        x = g.centroid.state.x
        summ = g.summary()
        feats.append({"type": "Feature", "geometry": _pt(fr, x), "properties": {**summ, "layer": "grup"}})
        feats.append({"type": "Feature", "geometry": _poly(fr, x, g.extent_cov),
                      "properties": {"group_id": gid, "layer": "grup_intindere"}})
        for h, xf, Pf in g.centroid.forecast(now):
            feats.append({"type": "Feature", "geometry": _poly(fr, xf, Pf[:2, :2] + g.extent_cov),
                          "properties": {"group_id": gid, "layer": "coridor_grup", "horizon_min": int(h // 60)}})
    for mid in sorted(tracker.pending):
        m, cand = tracker.pending[mid]
        feats.append({"type": "Feature", "geometry": _pt(fr, m.z),
                      "properties": {"layer": "ambiguu", "report": mid, "candidates": cand}})
    for ob in observers or []:
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [ob.lon, ob.lat]},
                      "properties": {"layer": "observator", "id": ob.observer_id}})
    for fp in friendly or []:
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [fp.lon, fp.lat]},
                      "properties": {"layer": "echipe", "id": fp.unit_id, "name": fp.name, "symbol": fp.symbol,
                                     "source": fp.source}})
    for site in (radars if radars is not None else getattr(tracker, "radars", [])):
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [site.lon, site.lat]},
                      "properties": {"layer": "radar", "id": site.sensor_id, "min_alt_m": site.min_alt,
                                     "max_range_m": site.max_range}})
        r = site.coverage_radius(site.min_alt)
        if r and site.center is not None:
            feats.append({"type": "Feature", "geometry": _circle(fr, site.center, r),
                          "properties": {"layer": "acoperire_radar", "id": site.sensor_id,
                                         "nota": f"acoperire de la {site.min_alt:.0f} m in sus; dedesubt radarul e orb"}})
    return {"type": "FeatureCollection", "features": feats,
            "properties": {"now": now, "ref": [fr.lat0, fr.lon0]}}


def write(tracker, now: float, out_dir: str | Path, destinations=None, observers=None, radars=None) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    gj = to_geojson(tracker, now, destinations, observers, radars)
    p = out / "cop.geojson"
    p.write_text(json.dumps(gj, ensure_ascii=False), encoding="utf-8")
    (out / "jurnal.json").write_text(json.dumps(tracker.journal, ensure_ascii=False, indent=1), encoding="utf-8")
    return p
