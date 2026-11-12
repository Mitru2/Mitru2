"""Destinatii probabile prin distributii-punte (Ahmad et al., 2016).

Traseul partial este tratat ca un drum care trebuie sa se termine intr-una
dintre destinatiile posibile. Pentru fiecare destinatie D si fiecare ora de
sosire T, probabilitatea este verosimilitatea predictiei filtrului (la T) de a
cadea in D. Se marginalizeaza peste T cu un prior uniform. Nu cere antrenare.

Rezultatul este un strat separat; nu intra in conul principal pana nu este
validat in mod umbra.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .imm import IMM, H
from .track import Track


@dataclass
class Destination:
    name: str
    center: np.ndarray   # (2,)
    extent: float        # m, raza (1 sigma)
    prior: float = 1.0


@dataclass
class DestinationEstimate:
    name: str
    probability: float
    eta_s: float          # secunde de la "now" pana la sosire (medie a posteriori)


def destination_probabilities(track: Track, destinations: list[Destination], now: float | None = None,
                              horizon: float | None = None, step: float | None = None) -> list[DestinationEstimate]:
    p = track.params
    horizon = horizon or p.destination_horizon
    step = step or p.destination_time_step
    now = track.last_time if now is None else now
    imm = IMM(p)
    base = imm.predict(track.state, now) if now > track.state.t else track.state
    Ts = np.arange(step, horizon + step / 2, step)

    preds = []
    s = base
    for T in Ts:
        s = imm.predict(s, now + T)
        preds.append((H @ s.x, H @ s.P @ H.T))

    logw = []
    etas = []
    for d in destinations:
        Sd = np.eye(2) * d.extent ** 2
        lls = []
        for (m, C) in preds:
            S = C + Sd
            r = d.center - m
            _, logdet = np.linalg.slogdet(S)
            lls.append(-0.5 * (r @ np.linalg.solve(S, r) + logdet) - math.log(2 * math.pi))
        lls = np.array(lls)
        mx = lls.max()
        w = np.exp(lls - mx)
        logw.append(math.log(d.prior) + mx + math.log(w.mean()))
        etas.append(float((w * Ts).sum() / w.sum()))
    logw = np.array(logw)
    probs = np.exp(logw - logw.max())
    probs /= probs.sum()
    out = [DestinationEstimate(d.name, float(pr), eta) for d, pr, eta in zip(destinations, probs, etas)]
    return sorted(out, key=lambda e: (-e.probability, e.name))
