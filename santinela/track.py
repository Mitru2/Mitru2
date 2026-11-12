"""Traseu cu tampon de masuratori si re-filtrare (imbunatatirile 1 si 2).

Starea unui traseu este o functie pura a listei sale de intrari (masuratori si
evenimente de informatie negativa), sortate dupa (ora, id). De aici rezulta:

* un raport intarziat, inserat la locul lui, da exact acelasi traseu ca si cum
  ar fi sosit la timp;
* scoaterea unei masuratori (anularea unei uniri) da exact traseul care ar fi
  existat fara ea;
* rezultate identice la reluarea acelorasi intrari.
"""
from __future__ import annotations

import bisect
import math
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from .config import Params, DEFAULT
from .fusion import ci_weight
from .imm import IMM, IMMState, H, rts_smooth
from .measurements import Measurement


@dataclass
class NegativeEvent:
    """Un post fix activ, cu acoperire cunoscuta, nu a raportat nimic pana la ora t."""
    mid: str
    post_id: str
    t: float
    center: np.ndarray
    radius: float
    pd: float
    min_alt: float = 0.0  # > 0 pentru radar: se aplica doar daca tinta e stiut deasupra

    @property
    def sort_key(self) -> tuple:
        return (round(self.t, 6), self.mid)


@dataclass
class Step:
    t: float
    entry_id: str
    state: IMMState          # dupa actualizare
    x_pred: np.ndarray       # momente combinate inainte de actualizare
    P_pred: np.ndarray
    score: float
    d2: float = 0.0


TENTATIVE, CONFIRMED, DELETED = "tentativ", "confirmat", "sters"

# Categorii de altitudine din fisa de raportare: (limita inferioara, superioara), m
ALTITUDE_CATEGORIES = {
    "foarte jos": (0.0, 50.0),
    "jos": (50.0, 300.0),
    "mediu": (300.0, 1500.0),
    "inalt": (1500.0, 10000.0),
}


@dataclass
class Track:
    track_id: str
    params: Params = field(default=DEFAULT, repr=False)
    speed_sigma: float = DEFAULT.init_speed_sigma
    entries: list = field(default_factory=list)       # Measurement | NegativeEvent, sortate
    steps: list = field(default_factory=list)         # Step pe fiecare masuratoare
    score: float = 0.0
    auto_status: str = TENTATIVE
    confirm_rule: str | None = None
    manual_status: str | None = None                  # decizia operatorului are prioritate
    group_id: str | None = None
    negative_applied: set = field(default_factory=set)
    _ctx: dict | None = field(default=None, repr=False)
    cooperative_id: str | None = None   # identitate cooperativa (Remote ID, ADS-B), daca s-a potrivit

    # ---------- interogari ----------
    @property
    def status(self) -> str:
        return self.manual_status or self.auto_status

    @property
    def measurements(self) -> list[Measurement]:
        return [e for e in self.entries if isinstance(e, Measurement)]

    @property
    def sources(self) -> set[str]:
        return {m.source_id for m in self.measurements}

    @property
    def last_time(self) -> float:
        return self.steps[-1].t if self.steps else -math.inf

    @property
    def first_time(self) -> float:
        return self.steps[0].t if self.steps else math.inf

    @property
    def drone_class(self) -> str | None:
        c = Counter(m.drone_class for m in self.measurements if m.drone_class)
        if not c:
            return None
        best = max(c.values())
        return sorted(k for k, v in c.items() if v == best)[0]

    @property
    def operator_in_loop(self) -> bool:
        """Emisie mesh / 4G detectata pe traseu -> posibil operator in bucla."""
        return any(m.emission in ("mesh", "4g") for m in self.measurements)

    @property
    def emissions(self) -> list[str]:
        return sorted({m.emission for m in self.measurements if m.emission})

    @property
    def altitude_floor(self) -> float | None:
        """Limita inferioara a altitudinii tintei (m), sau None = presupunem "foarte jos".

        Ipoteza cea mai defavorabila: orice raport "foarte jos" coboara limita la 0.
        """
        lows = []
        for m in self.measurements:
            if m.altitude_m is not None:
                lows.append(max(0.0, m.altitude_m - 100.0))
            elif m.altitude_cat:
                lows.append(ALTITUDE_CATEGORIES.get(m.altitude_cat, (0.0, 0.0))[0])
        if not lows:
            return None
        return min(lows)

    @property
    def human_only(self) -> bool:
        return all(m.source_kind == "observer" for m in self.measurements)

    @property
    def state(self) -> IMMState:
        return self.steps[-1].state

    def imm(self, q_factor: float = 1.0) -> IMM:
        return IMM(self.params, q_factor)

    def state_at(self, t: float) -> IMMState:
        """Starea traseului la ora t (pentru porti, inclusiv pentru rapoarte intarziate)."""
        times = [s.t for s in self.steps]
        i = bisect.bisect_right(times, t) - 1
        imm = self.imm()
        if i < 0:
            # inainte de prima masuratoare: retrodictie din primul pas
            return imm.predict(self.steps[0].state, t)
        return imm.predict(self.steps[i].state, t)

    # ---------- modificari ----------
    def _insert(self, e) -> int:
        keys = [x.sort_key for x in self.entries]
        i = bisect.bisect_right(keys, e.sort_key)
        self.entries.insert(i, e)
        return i

    def add(self, e) -> None:
        idx = self._insert(e)
        # Un raport cu altitudine poate schimba retroactiv validitatea penalizarilor
        # radar (min_alt), deci atunci se refiltreaza tot.
        retro = (isinstance(e, Measurement) and (e.altitude_cat or e.altitude_m is not None)
                 and any(isinstance(x, NegativeEvent) and x.min_alt > 0 for x in self.entries))
        if idx == len(self.entries) - 1 and self._ctx is not None and not retro:
            # Cale rapida: intrarea e ultima in ordine. Aceleasi operatii, in aceeasi
            # ordine, ca la re-filtrarea completa -> rezultat identic bit cu bit.
            self._apply(e)
            self._publish()
        else:
            self.refilter(idx)

    def remove(self, mids: set[str]) -> list:
        removed = [e for e in self.entries if e.mid in mids]
        if not removed:
            return []
        first = min(self.entries.index(e) for e in removed)
        self.entries = [e for e in self.entries if e.mid not in mids]
        self.refilter(first)
        return removed

    # ---------- re-filtrare ----------
    def refilter(self, from_entry: int = 0) -> None:
        """Re-filtrare exacta din tampon.

        Reluarea porneste de la inceput: la cateva rapoarte pe traseu costa
        microsecunde si garanteaza ca rezultatul nu depinde de ordinea de sosire.
        """
        del from_entry
        self.steps = []
        self._ctx = {"state": None, "score": 0.0, "status": TENTATIVE, "rule": None,
                     "negs": set(), "sources": []}
        for e in self.entries:
            self._apply(e)
        self._publish()

    def _publish(self) -> None:
        c = self._ctx
        self.score = c["score"]
        self.auto_status = c["status"]
        self.confirm_rule = c["rule"]
        self.negative_applied = set(c["negs"])

    def _apply(self, e) -> None:
        p = self.params
        imm = self.imm()
        c = self._ctx
        state = c["state"]
        if isinstance(e, NegativeEvent):
            if state is None or c["status"] != TENTATIVE:
                return
            if e.min_alt > 0:
                af = self.altitude_floor
                if af is None or af < e.min_alt:
                    return  # tinta poate fi sub acoperirea radarului
            pred = imm.predict(state, e.t) if e.t >= state.t else state
            pos = H @ pred.x
            if np.linalg.norm(pos - e.center) <= e.radius and e.post_id not in c["negs"]:
                c["score"] += math.log(1.0 - e.pd)
                c["negs"].add(e.post_id)
                if c["score"] <= p.sprt_delete:
                    c["status"] = DELETED
            return
        m: Measurement = e
        if state is None:
            state = imm.init(m.z, m.R, m.t, self.speed_sigma)
            if m.H_full is not None and m.H_full.shape == (4, 4):
                # traseu extern cu viteza: initializare direct din estimarea lui
                Minit = len(state.mu)
                state = IMMState(np.tile(m.z_full, (Minit, 1)), np.tile(m.R_full, (Minit, 1, 1)), state.mu, m.t)
            x_pred = state.x.copy()
            P_pred = state.P.copy()
            d2 = 0.0
        else:
            pred = imm.predict(state, m.t)
            x_pred, P_pred = pred.x, pred.P
            S = H @ P_pred @ H.T + m.R
            r = m.z - H @ x_pred
            d2 = float(r @ np.linalg.solve(S, r))
            _, logdet = np.linalg.slogdet(S)
            # increment de scor (Blackman): ln(P_D) - ln(lambda_FA) - ln(2pi) - 0.5 ln|S| - d2/2
            c["score"] += (math.log(m.pd) - math.log(m.lambda_fa) - math.log(2 * math.pi)
                           - 0.5 * logdet - 0.5 * d2)
            omega = None
            if m.ci:
                Jm = m.H_full if m.H_full is not None else H
                Rm = m.R_full if m.H_full is not None else m.R
                omega = ci_weight(P_pred, Jm, Rm)
            state, _, _ = imm.update_m(pred, m, omega)
        c["state"] = state
        c["sources"].append(m.source_id)
        if c["status"] == TENTATIVE:
            if c["score"] >= p.sprt_confirm:
                ok, why = self._source_rule(c["sources"], m)
                if ok:
                    c["status"], c["rule"] = CONFIRMED, why
            elif c["score"] <= p.sprt_delete and len(c["sources"]) > 1:
                c["status"] = DELETED
        self.steps.append(Step(m.t, m.mid, state.copy(), x_pred, P_pred, c["score"], d2))

    def _source_rule(self, sources: list[str], m: Measurement) -> tuple[bool, str]:
        """Regula de confirmare: SPRT + (>= 2 surse independente sau o sursa de incredere)."""
        p = self.params
        distinct = sorted(set(sources))
        if len(distinct) >= 2:
            return True, f"SPRT>={p.sprt_confirm:.2f} si {len(distinct)} surse independente"
        trust = m.trust
        if m.source_kind == "sensor" and trust is None:
            return True, f"SPRT>={p.sprt_confirm:.2f}, senzor unic"
        if m.source_kind == "sensor" and trust >= p.single_source_min_trust:
            return True, f"SPRT>={p.sprt_confirm:.2f}, senzor unic cu incredere {trust:.2f}"
        if trust is not None and trust >= p.single_source_min_trust:
            return True, f"SPRT>={p.sprt_confirm:.2f}, sursa unica cu incredere {trust:.2f}"
        return False, ""

    # ---------- netezire si prognoza ----------
    def smoothed(self) -> tuple[list[float], list[np.ndarray], list[np.ndarray]]:
        if not self.steps:
            return [], [], []
        times = [s.t for s in self.steps]
        if len(self.steps) == 1:
            return times, [self.steps[0].state.x], [self.steps[0].state.P]
        xf = [s.state.x for s in self.steps]
        Pf = [s.state.P for s in self.steps]
        xp = [s.x_pred for s in self.steps]
        Pp = [s.P_pred for s in self.steps]
        xs, Ps = rts_smooth(times, xf, Pf, xp, Pp)
        return times, xs, Ps

    def forecast(self, now: float, horizons: tuple | None = None) -> list[tuple[float, np.ndarray, np.ndarray]]:
        """Conul de prognoza: (orizont, medie, covarianta) la now + h.

        Un traseu cu posibil operator in bucla primeste zgomot de proces marit.
        """
        horizons = horizons or self.params.forecast_horizons
        qf = self.params.operator_in_loop_q_factor if self.operator_in_loop else 1.0
        imm = self.imm(qf)
        out = []
        for h in horizons:
            s = imm.predict(self.state, now + h)
            out.append((h, s.x, s.P))
        return out
