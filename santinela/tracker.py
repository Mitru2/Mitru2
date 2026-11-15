"""Trackerul: asociere GNN cu regula de ambiguitate, SPRT, uniri, informatie negativa, grupuri.

Totul este determinist: aceleasi intrari, in aceeasi ordine, dau aceleasi iesiri.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import linear_sum_assignment

from .config import Params, DEFAULT
from .geo import LocalFrame
from .groups import Group
from .imm import H
from .measurements import Measurement, Report, SensorDetection, convert_report, convert_sensor
from .observers import ObserverRegistry
from .imm import F_cv
from .radar import (RadarPlot, RadarRegistration, RadarSite, RadarTrackReport, convert_plot,
                    convert_track_report, registration_rows)
from .sensors import FixedPost
from .speeds import SpeedTable
from .track import Track, NegativeEvent, TENTATIVE, CONFIRMED, DELETED

BIG = 1e9


@dataclass
class MergeRecord:
    merge_id: str
    target: str
    source: str
    moved: list            # id-uri de masuratori mutate
    source_entries: list   # intrarile traseului sursa, pentru restaurare
    author: str
    automatic: bool
    undone: bool = False


@dataclass
class Tracker:
    params: Params = field(default_factory=lambda: DEFAULT)
    frame: LocalFrame = field(default_factory=LocalFrame)
    speeds: SpeedTable = field(default_factory=SpeedTable)
    observers: ObserverRegistry | None = None
    posts: list = field(default_factory=list)  # FixedPost
    radars: list = field(default_factory=list)  # RadarSite

    def __post_init__(self):
        if self.observers is None:
            self.observers = ObserverRegistry(self.params)
        self.tracks: dict[str, Track] = {}
        self.archive: dict[str, Track] = {}
        self.groups: dict[str, Group] = {}
        self.pending: dict[str, tuple[Measurement, list[str]]] = {}
        self.ambiguity_events: list[tuple[float, np.ndarray, str]] = []
        self.merges: dict[str, MergeRecord] = {}
        self.journal: list[dict] = []
        self._n_track = 0
        self._n_group = 0
        self._n_merge = 0
        self.now = -math.inf
        self.ext_map: dict[tuple, str] = {}           # (radar, nr. traseu extern) -> traseu local
        self.registrations: dict[str, RadarRegistration] = {}
        self._radar_by_id: dict[str, RadarSite] = {}
        for site in self.radars:
            self.add_radar(site)

    def add_radar(self, site: RadarSite) -> RadarSite:
        site.attach(self.frame)
        self._radar_by_id[site.sensor_id] = site
        self.registrations.setdefault(site.sensor_id, RadarRegistration())
        model = self.observers.get(site.sensor_id)
        model.civilian = False
        if site not in self.radars:
            self.radars.append(site)
        return site

    # ------------------------------------------------------------------ util
    def _log(self, t: float, event: str, **kw) -> None:
        self.journal.append({"t": round(float(t), 3) if math.isfinite(t) else None, "event": event, **kw})

    def _new_track(self, m: Measurement) -> Track:
        self._n_track += 1
        tid = f"T{self._n_track:04d}"
        nominal = self.speeds.nominal_speed(m.drone_class)
        sigma = nominal if nominal else self.params.init_speed_sigma
        tr = Track(tid, self.params, speed_sigma=sigma)
        tr.add(m)
        self.tracks[tid] = tr
        if m.ext_track:
            self.ext_map[m.ext_track] = tid
        self._log(m.t, "traseu_nou", track=tid, source=m.source_id, report=m.mid)
        return tr

    def live_tracks(self, t: float | None = None) -> list[Track]:
        out = []
        for tid in sorted(self.tracks):
            tr = self.tracks[tid]
            if tr.status == DELETED or tr.group_id is not None:
                continue
            if t is not None and t - tr.last_time > self.params.track_timeout:
                continue
            out.append(tr)
        return out

    def to_measurement(self, item) -> Measurement:
        if isinstance(item, Measurement):
            return item
        if isinstance(item, Report):
            return convert_report(item, self.frame, self.observers.get(item.observer_id), self.params)
        if isinstance(item, SensorDetection):
            return convert_sensor(item, self.frame, self.observers.get(item.sensor_id))
        if isinstance(item, RadarPlot):
            site = self._radar_by_id[item.sensor_id]
            return convert_plot(item, site, self.registrations[site.sensor_id],
                                self.observers.get(site.sensor_id), self.params)
        if isinstance(item, RadarTrackReport):
            site = self._radar_by_id[item.sensor_id]
            return convert_track_report(item, site, self.observers.get(site.sensor_id), self.params)
        raise TypeError(type(item))

    # ------------------------------------------------------------------ porti
    def gate(self, tr: Track, m: Measurement) -> float | None:
        """Cost de asociere sau None daca masuratoarea iese din porti."""
        s = tr.state_at(m.t)
        S = H @ s.P @ H.T + m.R
        r = m.z - H @ s.x
        d2 = float(r @ np.linalg.solve(S, r))
        if d2 > self.params.gate_chi2:
            return None
        # poarta cinematica fata de cel mai apropiat pas in timp
        cls = tr.drone_class or m.drone_class
        vmax = self.speeds.vmax_any(cls)
        nearest = min(tr.steps, key=lambda st: (abs(st.t - m.t), st.entry_id))
        dt = abs(nearest.t - m.t)
        if dt > 1.0:
            p_near = H @ nearest.state.x
            slack = 2.0 * (math.sqrt(np.trace(m.R)) + math.sqrt(np.trace(H @ nearest.state.P @ H.T)))
            implied = max(0.0, np.linalg.norm(m.z - p_near) - slack) / dt
            if implied > vmax:
                return None
        _, logdet = np.linalg.slogdet(S)
        return d2 + logdet

    # ------------------------------------------------------------------ ingestie
    def ingest(self, items: list, now: float | None = None) -> dict:
        """Proceseaza un lot de rapoarte/detectii sosite in acelasi ciclu."""
        ms = sorted((self.to_measurement(i) for i in items), key=lambda m: m.sort_key)
        if now is None:
            now = max([self.now] + [m.t for m in ms])
        self.now = max(self.now, now)
        result = {"assigned": {}, "new": [], "ambiguous": [], "group": []}

        # 1. rapoarte in zonele de grup
        rest = []
        for m in ms:
            g = self._group_for(m)
            if g is not None:
                g.add(m)
                result["group"].append((m.mid, g.group_id))
            else:
                rest.append(m)
        ms = rest
        if not ms:
            self._after_ingest(result)
            return result

        # 2. continuitatea traseelor radar externe: numarul de traseu e deja decizia
        #    de asociere a radarului, deci o actualizare a aceluiasi traseu extern
        #    merge la acelasi traseu local cat timp acesta e activ (si la manevre,
        #    cand poarta noastra ar respinge-o).
        rest = []
        for m in ms:
            tid = self.ext_map.get(m.ext_track) if m.ext_track else None
            tr = self.tracks.get(tid) if tid else None
            if (tr is not None and tr.status != DELETED and tr.group_id is None
                    and m.t - tr.last_time <= self.params.track_timeout):
                tr.add(m)
                result["assigned"][m.mid] = tr.track_id
            else:
                rest.append(m)
        ms = rest

        # 3. GNN pe "scanari": rapoartele unei surse din ciclul curent. O sursa
        #    raporteaza o drona cel mult o data pe scanare (aceeasi sursa, acelasi
        #    moment); surse sau momente diferite pot privi aceeasi drona, deci nu le
        #    punem in aceeasi atribuire unu-la-unu.
        scans: dict[tuple, list[Measurement]] = {}
        for m in ms:
            scans.setdefault((m.source_id, round(m.t)), []).append(m)
        order = sorted(scans, key=lambda k: (scans[k][0].sort_key, k))
        for k in order:
            self._gnn_scan(scans[k], result)
        self._after_ingest(result)
        return result

    def _gnn_scan(self, ms: list[Measurement], result: dict) -> None:
        tracks = self.live_tracks(max(m.t for m in ms))
        C = np.full((len(ms), max(1, len(tracks))), BIG)
        for j, m in enumerate(ms):
            for i, tr in enumerate(tracks):
                c = self.gate(tr, m)
                if c is not None:
                    C[j, i] = c
        assigned: dict[int, int] = {}
        if tracks:
            rows, cols = linear_sum_assignment(C)
            for r, c in zip(rows, cols):
                if C[r, c] < BIG:
                    assigned[r] = c

        for j, m in enumerate(ms):
            gated = [i for i in range(len(tracks)) if C[j, i] < BIG]
            if j in assigned:
                i = assigned[j]
                # Regula de ambiguitate: alt traseu aproape la fel de bun -> decide operatorul
                tr = tracks[i]
                if tr.status == DELETED:  # unit automat mai devreme in aceasta scanare
                    tr = self._new_track(m)
                    result["new"].append(tr.track_id)
                    continue
                rivals = [k for k in gated if k != i and C[j, k] - C[j, i] < self.params.ambiguity_margin
                          and tracks[k].status != DELETED]
                # Un traseu tanar (<= 2 rapoarte) ale carui rapoarte intra toate in poarta
                # traseului ales este un duplicat: se uneste automat (anulabil cu un clic).
                for k in list(rivals):
                    if self._is_young_duplicate(tracks[k], tr):
                        self.merge(tr.track_id, tracks[k].track_id, "automat", automatic=True)
                        rivals.remove(k)
                if rivals:
                    cand = [tracks[k].track_id for k in [i] + rivals]
                    self.pending[m.mid] = (m, cand)
                    self.ambiguity_events.append((m.t, m.z.copy(), m.mid))
                    self._log(m.t, "asociere_ambigua", report=m.mid, candidates=cand)
                    result["ambiguous"].append(m.mid)
                    continue
                tr.add(m)
                result["assigned"][m.mid] = tr.track_id
                if m.ext_track:
                    self.ext_map[m.ext_track] = tr.track_id
            else:
                tr = self._new_track(m)
                result["new"].append(tr.track_id)

    def _is_young_duplicate(self, young: Track, host: Track) -> bool:
        if young.status != TENTATIVE or len(young.measurements) > 2 or young.first_time < host.first_time:
            return False
        return all(self.gate(host, m) is not None for m in young.measurements)

    def _after_ingest(self, result: dict) -> None:
        self._check_status()
        self._detect_groups()

    def _check_status(self) -> None:
        for tid in sorted(self.tracks):
            tr = self.tracks[tid]
            prev = getattr(tr, "_logged_status", TENTATIVE)
            if tr.status != prev:
                if tr.status == CONFIRMED and tr.manual_status is None:
                    last = tr.steps[-1]
                    srcs = sorted(tr.sources)
                    self._log(last.t, "confirmare", track=tid, rule=tr.confirm_rule,
                              author="automat (SPRT)", sources=srcs, score=round(tr.score, 2))
                elif tr.status == DELETED and tr.manual_status is None:
                    self._log(tr.last_time, "stergere", track=tid, rule="SPRT sub pragul de stergere",
                              author="automat (SPRT)", score=round(tr.score, 2))
                tr._logged_status = tr.status

    # ------------------------------------------------------------------ operator
    def assign(self, mid: str, track_id: str, author: str) -> None:
        """Operatorul rezolva o asociere ambigua."""
        m, _ = self.pending.pop(mid)
        self.tracks[track_id].add(m)
        self._log(m.t, "asociere_manuala", report=mid, track=track_id, author=author)
        self._check_status()

    def confirm(self, track_id: str, author: str, rule: str = "decizie operator") -> None:
        tr = self.tracks[track_id]
        tr.manual_status = CONFIRMED
        tr._logged_status = CONFIRMED
        self._log(self.now, "confirmare", track=track_id, rule=rule, author=author)

    def set_identity(self, track_id: str, identity: str, author: str, reason: str = "") -> None:
        """Identitatea APP-6 a traseului (ex. 'ostil'), decizie de operator, trecuta in jurnal."""
        from .trackmodel import IDENTITIES
        if identity not in IDENTITIES:
            raise ValueError(f"identitate necunoscuta {identity}; posibile: {', '.join(IDENTITIES)}")
        tr = self.tracks[track_id]
        tr.identity_override, tr.identity_author = identity, author
        self._log(self.now, "identitate", track=track_id, identity=identity, author=author, reason=reason)

    def resolve(self, track_id: str, true_track: bool, author: str) -> None:
        """Verdict final pe traseu: actualizeaza increderea si modelul de eroare al observatorilor."""
        tr = self.tracks[track_id]
        obs_ids = sorted({m.source_id for m in tr.measurements if m.source_kind == "observer"})
        sensor_ids = sorted({m.source_id for m in tr.measurements if m.source_kind == "sensor"})
        for oid in obs_ids + sensor_ids:
            self.observers.get(oid).record_verdict(true_track)
        if true_track:
            self._learn_observer_errors(tr)
            self._learn_radar_registration(tr)
        else:
            tr.manual_status = DELETED
            tr._logged_status = DELETED
        self._log(self.now, "verdict", track=track_id, true_track=true_track, author=author, observers=obs_ids)

    def _learn_radar_registration(self, tr: Track, min_ref: int = 3) -> None:
        """Erori sistematice ale fiecarui radar din traseu, fata de traseul refacut FARA acel radar.

        Excluderea radarului evita auto-referinta: traseul de referinta nu e tras spre eroarea lui.
        Cu o referinta slaba (cateva rapoarte umane) estimarea nu trece pragul si nu se aplica.
        """
        for rid in sorted({m.source_id for m in tr.measurements if m.source_id in self._radar_by_id}):
            ref_entries = [m for m in tr.measurements if m.source_id != rid]
            plots = [m for m in tr.measurements if m.source_id == rid and isinstance(m.raw, RadarPlot)]
            if len(ref_entries) < min_ref or not plots:
                continue
            ref = Track(f"{tr.track_id}-ref-{rid}", self.params, speed_sigma=tr.speed_sigma)
            ref.entries = sorted(ref_entries, key=lambda e: e.sort_key)
            ref.refilter()
            times, xs, Ps = ref.smoothed()
            triples = []
            for m in plots:
                t = m.raw.t
                if t < times[0] or t > times[-1]:
                    continue  # doar interpolare, nu extrapolare
                k = max(i for i, tt in enumerate(times) if tt <= t)
                F = F_cv(t - times[k])
                triples.append((m.raw, F @ xs[k], (F @ Ps[k] @ F.T)[:2, :2]))
            self.register_radar(rid, triples, n_ref=len(ref_entries), source=tr.track_id)

    def register_radar(self, radar_id: str, triples: list, n_ref: int, source: str = "referinta") -> bool:
        """Adauga perechi (plot, stare de referinta, covarianta) si incearca estimarea.

        Referinta poate fi un traseu confirmat din alte surse sau, mai bine, tinte
        cooperative cu pozitie cunoscuta (ADS-B, aeronave de calibrare).
        """
        if not triples:
            return False
        site = self._radar_by_id[radar_id]
        reg = self.registrations[radar_id]
        reg.accumulate(registration_rows(site, triples), n_ref)
        if reg.external:
            # inregistrarea vine din SDPS (CAT063): estimarea proprie doar o verifica
            ok = reg.solve(self.params.min_reports_own_params, apply=False)
            if ok:
                b_az, b_r, c = reg.solution
                self._log(self.now, "inregistrare_radar_comparatie", radar=radar_id, n=reg.n, source=source,
                          externa=reg.external, bias_az_deg=round(math.degrees(b_az), 3),
                          bias_range_m=round(b_r, 1), clock_offset_s=round(c, 3),
                          dif_az_deg=round(math.degrees(b_az - reg.bias_az), 3),
                          dif_range_m=round(b_r - reg.bias_range, 1))
            return ok
        ok = reg.solve(self.params.min_reports_own_params)
        sig = None if reg.sigma is None else [round(math.degrees(reg.sigma[0]), 3),
                                               round(float(reg.sigma[1]), 1), round(float(reg.sigma[2]), 3)]
        if ok:
            self._log(self.now, "inregistrare_radar", radar=radar_id, n=reg.n, source=source,
                      bias_az_deg=round(math.degrees(reg.bias_az), 3), bias_range_m=round(reg.bias_range, 1),
                      clock_offset_s=round(reg.clock_offset, 3), sigma=sig)
        else:
            self._log(self.now, "inregistrare_radar_insuficienta", radar=radar_id, n=reg.n, source=source,
                      sigma=sig)
        return ok

    def set_registration(self, radar_id: str, bias_az: float, bias_range: float, range_gain: float = 0.0,
                         source: str = "extern") -> bool:
        """Inregistrare primita din afara (ex. biasurile SDPS din CAT063): azimut (rad), distanta (m), castig.

        Corectia urmeaza formulele CAT063: theta = theta_m - b_az, rho = (rho_m - b_r) / (1 + castig).
        Estimarea proprie continua sa ruleze, dar doar compara (jurnal: inregistrare_radar_comparatie).
        """
        reg = self.registrations.get(radar_id)
        if reg is None:
            return False
        new = (bias_az, bias_range, range_gain, source)
        if (reg.bias_az, reg.bias_range, reg.range_gain, reg.external) == new:
            return False
        reg.bias_az, reg.bias_range, reg.range_gain, reg.external = new
        reg.estimated = True
        self._log(self.now, "inregistrare_radar_externa", radar=radar_id, source=source,
                  bias_az_deg=round(math.degrees(bias_az), 4), bias_range_m=round(bias_range, 1),
                  range_gain=range_gain)
        return True

    def set_sensor_state(self, radar_id: str, active: bool, reason: str = "") -> bool:
        """Starea operationala a unui senzor: tacerea unui senzor inactiv nu e informatie negativa."""
        site = self._radar_by_id.get(radar_id)
        if site is None or site.active == active:
            return False
        site.active = active
        self._log(self.now, "stare_senzor", radar=radar_id, activ=active, motiv=reason)
        return True

    def reset_external_tracks(self, radar_id: str, reason: str = "") -> int:
        """Numerele de traseu ale unei surse nu mai sunt valabile (ex. repornirea procesorului radar)."""
        keys = [k for k in self.ext_map if k[0] == radar_id]
        for k in keys:
            del self.ext_map[k]
        if keys:
            self._log(self.now, "numere_traseu_resetate", radar=radar_id, n=len(keys), motiv=reason)
        return len(keys)

    def _learn_observer_errors(self, tr: Track) -> None:
        times, xs, _ = tr.smoothed()
        pos = {st.entry_id: xs[k][:2] for k, st in enumerate(tr.steps)}
        per_obs: dict[str, tuple[list, list]] = {}
        for m in tr.measurements:
            if m.source_kind != "observer" or m.obs_xy is None or m.bearing is None:
                continue
            d = pos[m.mid] - m.obs_xy
            true_b = math.atan2(d[0], d[1])
            br = (m.bearing - true_b + math.pi) % (2 * math.pi) - math.pi
            b_list, l_list = per_obs.setdefault(m.source_id, ([], []))
            b_list.append(br)
            if m.distance:
                l_list.append(math.log(m.distance) - math.log(max(np.linalg.norm(d), 1.0)))
        for oid in sorted(per_obs):
            b, l = per_obs[oid]
            self.observers.get(oid).add_residuals(b, l)

    # ------------------------------------------------------------------ uniri
    def merge(self, target: str, source: str, author: str, automatic: bool = False) -> str:
        a, b = self.tracks[target], self.tracks[source]
        self._n_merge += 1
        mid = f"M{self._n_merge:04d}"
        moved = [m.mid for m in b.measurements]
        rec = MergeRecord(mid, target, source, moved, list(b.entries), author, automatic)
        for m in b.measurements:
            a._insert(m)
        a.refilter()
        b.manual_status = DELETED
        b._logged_status = DELETED
        self.merges[mid] = rec
        self._log(self.now, "unire", merge=mid, target=target, source=source, author=author, automatic=automatic)
        self._check_status()
        return mid

    def undo_merge(self, merge_id: str, author: str) -> None:
        """Anulare cu un clic: scoate masuratorile mutate (re-filtrare exacta) si restaureaza sursa."""
        rec = self.merges[merge_id]
        a, b = self.tracks[rec.target], self.tracks[rec.source]
        a.remove(set(rec.moved))
        b.entries = list(rec.source_entries)
        b.manual_status = None
        b.refilter()
        b._logged_status = b.status
        rec.undone = True
        self._log(self.now, "anulare_unire", merge=merge_id, author=author, automatic_original=rec.automatic)
        self._check_status()

    def auto_merge(self, chi2: float = 13.28) -> list[str]:
        """Uneste automat trasee cu stari statistic identice (4 grade de libertate, p = 0.99)."""
        done = []
        live = self.live_tracks(self.now)
        used = set()
        for i, a in enumerate(live):
            for b in live[i + 1:]:
                if a.track_id in used or b.track_id in used:
                    continue
                t = max(a.last_time, b.last_time)
                sa, sb = a.state_at(t), b.state_at(t)
                d = sa.x - sb.x
                if float(d @ np.linalg.solve(sa.P + sb.P, d)) < chi2:
                    target, source = (a, b) if len(a.measurements) >= len(b.measurements) else (b, a)
                    done.append(self.merge(target.track_id, source.track_id, "automat", automatic=True))
                    used.update({a.track_id, b.track_id})
        return done

    def merge_metrics(self) -> dict:
        auto = [r for r in self.merges.values() if r.automatic]
        undone = [r for r in auto if r.undone]
        return {"auto_merges": len(auto), "undone": len(undone),
                "undo_rate": (len(undone) / len(auto)) if auto else 0.0}

    # ------------------------------------------------------------------ informatie negativa
    def apply_negative_info(self, now: float, step: float = 10.0) -> list[tuple[str, str]]:
        """Posturile fixe active care nu au raportat scad scorul traseelor tentative.

        Se aplica doar surselor cu acoperire si stare cunoscute, niciodata observatorilor civili.
        """
        applied = []
        for tr in self.live_tracks(now):
            if tr.status != TENTATIVE:
                continue
            for post in list(self.posts) + list(self.radars):
                if not isinstance(post, (FixedPost, RadarSite)) or not post.active:
                    continue
                # raza in care postul/radarul ar fi trebuit sa vada tinta; pentru radar
                # depinde de altitudine si e None cand tinta poate fi sub acoperire
                radius = post.coverage_radius(tr.altitude_floor)
                if radius is None:
                    continue
                min_alt = getattr(post, "min_alt", 0.0)
                if post.post_id in tr.sources:
                    continue
                eid = f"neg:{post.post_id}:{tr.track_id}"
                if any(e.mid == eid for e in tr.entries):
                    continue
                t = tr.last_time
                hit = None
                while t <= now:
                    if np.linalg.norm(H @ tr.state_at(t).x - post.center) <= radius:
                        hit = t
                        break
                    t += step
                if hit is None:
                    continue
                tr.add(NegativeEvent(eid, post.post_id, hit, post.center.copy(), radius, post.pd, min_alt))
                applied.append((tr.track_id, post.post_id))
                self._log(hit, "informatie_negativa", track=tr.track_id, post=post.post_id,
                          score=round(tr.score, 2))
        self._check_status()
        return applied

    # ------------------------------------------------------------------ grupuri
    def _group_for(self, m: Measurement) -> Group | None:
        for gid in sorted(self.groups):
            g = self.groups[gid]
            if g.active and m.t - g.last_time > self.params.group_timeout:
                g.active = False
                self._log(m.t, "grup_inchis", group=gid)
            if g.contains(m):
                return g
        return None

    def _detect_groups(self) -> None:
        p = self.params
        recent = [(t, z, mid) for (t, z, mid) in self.ambiguity_events if t >= self.now - p.group_window
                  and mid in self.pending]
        for t, z, mid in recent:
            if mid not in self.pending:
                continue
            near = [(t2, z2, m2) for (t2, z2, m2) in recent
                    if m2 in self.pending and np.linalg.norm(z2 - z) <= p.group_radius]
            if len(near) < p.group_ambiguous_count:
                continue
            center = np.mean([z2 for _, z2, _ in near], axis=0)
            self._n_group += 1
            gid = f"G{self._n_group:03d}"
            g = Group(gid, p, created_at=self.now)
            seeds = [self.pending.pop(m2)[0] for _, _, m2 in near]
            for tr in self.live_tracks(self.now):
                if np.linalg.norm(H @ tr.state_at(self.now).x - center) <= p.group_radius:
                    tr.group_id = gid
                    g.members.append(tr.track_id)
                    seeds.extend(tr.measurements)
            # rapoarte ambigue ramase in zona
            for pm in sorted(self.pending):
                m2, _ = self.pending[pm]
                if np.linalg.norm(m2.z - center) <= p.group_radius:
                    seeds.append(self.pending.pop(pm)[0])
            uniq = {m.mid: m for m in seeds}
            g.seed(list(uniq.values()))
            self.groups[gid] = g
            self._log(self.now, "mod_grup", group=gid, members=list(g.members),
                      rule=f">= {p.group_ambiguous_count} asocieri ambigue in {p.group_window:.0f} s pe {p.group_radius/1000:.0f} km",
                      author="automat")

    # ------------------------------------------------------------------ rezumat
    def snapshot(self) -> dict:
        """Rezumat determinist (pentru teste de reluare si pentru export)."""
        out = {"tracks": {}, "groups": {}, "pending": sorted(self.pending)}
        for tid in sorted(self.tracks):
            tr = self.tracks[tid]
            out["tracks"][tid] = {
                "status": tr.status,
                "score": round(tr.score, 9),
                "x": [round(float(v), 6) for v in tr.state.x],
                "measurements": [m.mid for m in tr.measurements],
                "group": tr.group_id,
            }
        for gid in sorted(self.groups):
            out["groups"][gid] = self.groups[gid].summary()
        return out
