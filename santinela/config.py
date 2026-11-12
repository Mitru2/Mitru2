"""Parametri configurabili (plan v2).

Valorile sunt puncte de pornire, nu cifre din literatura; se calibreaza in mod umbra.
Toate unitatile sunt SI (m, s, rad) daca nu se spune altfel.
"""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Params:
    # --- Fizica ---
    speed_of_sound: float = 343.0  # m/s

    # --- IMM: doua modele de viteza constanta (linistit / manevra) ---
    q_low: float = 0.05     # densitate spectrala acceleratie, m^2/s^3
    q_high: float = 8.0
    model_switch_tau: float = 60.0  # s, timp mediu de sedere intr-un model
    init_mu: tuple = (0.8, 0.2)
    init_speed_sigma: float = 60.0  # m/s, cand clasa e necunoscuta

    # --- Porti ---
    gate_chi2: float = 13.82  # chi2(2), p = 0.999
    speed_gate_factor: float = 1.3
    unknown_class_vmax: float = 235.0  # m/s (urcat de la 200 in v2)

    # --- GNN + regula de ambiguitate ---
    ambiguity_margin: float = 2.0  # diferenta minima de cost intre doua trasee

    # --- SPRT ---
    sprt_alpha: float = 1e-3  # probabilitate de confirmare falsa
    sprt_beta: float = 0.1    # probabilitate de a pierde un traseu real
    lambda_fa_base: float = 2.5e-9  # rapoarte false / m^2 (~1 la 20x20 km)
    pd_observer: float = 0.8
    single_source_min_trust: float = 0.6  # o singura sursa poate confirma doar peste acest prag
    track_timeout: float = 300.0  # s fara actualizare -> traseu terminat (~15 km la viteza Geran-2)

    # --- Observatori ---
    trust_prior: tuple = (2.0, 1.0)  # Beta(a, b): a = confirmate, b = false
    min_reports_own_params: int = 10
    sigma_theta_deg: float = 10.0    # populatie (Faza 0 inlocuieste)
    dist_bias_ln: float = 0.0        # deplasare log a distantei estimate
    sigma_ln: float = 0.35           # sigma ln(distanta)
    default_distance_visual: float = 2000.0  # m, cand raportul nu da distanta
    default_distance_audio: float = 3000.0
    sigma_ln_no_distance: float = 0.8
    observer_pos_sigma: float = 15.0  # m, GPS telefon

    # --- Informatie negativa ---
    pd_fixed_post: float = 0.7

    # --- Mod grup ---
    group_ambiguous_count: int = 3
    group_window: float = 300.0   # s
    group_radius: float = 10000.0  # m
    group_timeout: float = 600.0

    # --- Prognoza ---
    forecast_horizons: tuple = (300.0, 600.0, 900.0)  # T+5/10/15 min
    operator_in_loop_q_factor: float = 4.0

    # --- Destinatii (punti) ---
    destination_horizon: float = 3600.0
    destination_time_step: float = 30.0


    @property
    def sprt_confirm(self) -> float:
        return math.log((1 - self.sprt_beta) / self.sprt_alpha)

    @property
    def sprt_delete(self) -> float:
        return math.log(self.sprt_beta / (1 - self.sprt_alpha))

    @property
    def sigma_theta(self) -> float:
        return math.radians(self.sigma_theta_deg)


DEFAULT = Params()
