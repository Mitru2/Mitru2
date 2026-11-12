"""Simulator si scenarii de test.

Scenariul principal "Dunare" reproduce geometria din nordul judetului Tulcea:
tinte la altitudine foarte mica, de-a lungul Dunarii, fara corespondent radar,
cu observatori pe malul romanesc. Doar date publice; pozitiile observatorilor
sunt fictive.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .config import Params, DEFAULT
from .geo import LocalFrame
from .imm import F_cv, Q_cv, IMM
from .measurements import Report, SensorDetection
from .sensors import FixedPost
from .destinations import Destination
from .radar import RadarSite


# ---------------------------------------------------------------- adevar
@dataclass
class Target:
    target_id: str
    waypoints: list            # [(lat, lon), ...]
    speed_kmh: float
    t0: float = 0.0
    drone_class: str | None = None
    emission: str | None = None
    altitude_m: float = 30.0   # implicit "foarte jos"

    def _xy(self, frame: LocalFrame) -> np.ndarray:
        return np.array([frame.to_xy(*w) for w in self.waypoints])

    def duration(self, frame: LocalFrame) -> float:
        P = self._xy(frame)
        return float(np.sum(np.linalg.norm(np.diff(P, axis=0), axis=1)) / (self.speed_kmh / 3.6))

    def position(self, t: float, frame: LocalFrame) -> np.ndarray | None:
        if t < self.t0:
            return None
        P = self._xy(frame)
        s = (t - self.t0) * self.speed_kmh / 3.6
        for a, b in zip(P[:-1], P[1:]):
            L = float(np.linalg.norm(b - a))
            if s <= L:
                return a + (b - a) * (s / L)
            s -= L
        return None

    def velocity(self, t: float, frame: LocalFrame) -> np.ndarray | None:
        p1, p2 = self.position(t, frame), self.position(t + 1.0, frame)
        if p1 is None or p2 is None:
            return None
        return p2 - p1


@dataclass
class SimObserver:
    observer_id: str
    lat: float
    lon: float
    sigma_theta_deg: float = 10.0
    bias_ln: float = 0.1
    sigma_ln: float = 0.35
    visual_range: float = 3000.0
    audio_range: float = 5000.0
    report_interval: float = 60.0   # cel mult un raport pe tinta pe interval
    p_report: float = 0.8
    false_rate_per_h: float = 0.0   # rapoarte false pe ora
    delay_mean: float = 20.0        # s pana la trimitere


@dataclass
class Scenario:
    name: str
    frame: LocalFrame
    targets: list
    observers: list
    posts: list = field(default_factory=list)
    sensors: list = field(default_factory=list)   # (sensor_id, lat, lon, range, sigma, pd, tip)
    destinations: list = field(default_factory=list)
    t_end: float = 3600.0
    radars: list = field(default_factory=list)       # RadarSite (cu SAC/SIC)
    radar_truth: dict = field(default_factory=dict)  # id -> RadarTruth (erori reale, pasari)
    radar_mode: str = "plots"                        # "plots" | "tracks"
    tod_ref: float = 36000.0                         # ora zilei la t = 0 (10:00 UTC)
    p_altitude_report: float = 0.5                   # cat de des fisa contine altitudinea


@dataclass
class RadarTruth:
    """Erorile sistematice reale ale unui radar simulat (de gasit prin inregistrare)."""
    bias_az_deg: float = 0.0
    bias_range_m: float = 0.0
    clock_offset_s: float = 0.0       # t_real = t_marcat + offset
    scan_period: float = 5.0
    birds_per_scan: float = 0.05      # plot-uri false pe rotatie


def altitude_category(alt: float) -> str:
    if alt < 50:
        return "foarte jos"
    if alt < 300:
        return "jos"
    if alt < 1500:
        return "mediu"
    return "inalt"


def _bearing_deg(frm: np.ndarray, to: np.ndarray) -> float:
    d = to - frm
    return math.degrees(math.atan2(d[0], d[1])) % 360.0


def generate_reports(scn: Scenario, seed: int = 1) -> list:
    """Rapoarte si detectii, fiecare cu ``received_at``; deterministe pentru un seed dat."""
    rng = np.random.default_rng(seed)
    rng_alt = np.random.default_rng(seed + 1000)  # fluxuri separate: adaugarea lor nu schimba restul
    fr = scn.frame
    out = []
    n = 0
    for ob in scn.observers:
        oxy = fr.to_xy(ob.lat, ob.lon)
        for tg in scn.targets:
            t = tg.t0 + rng.uniform(0, ob.report_interval)
            while t < scn.t_end:
                p = tg.position(t, fr)
                if p is not None:
                    rng_d = float(np.linalg.norm(p - oxy))
                    kind = None
                    if rng_d <= ob.visual_range:
                        kind = "visual"
                    elif rng_d <= ob.audio_range:
                        kind = "audio"
                    if kind and rng.uniform() < ob.p_report:
                        b = _bearing_deg(oxy, p) + rng.normal(0, ob.sigma_theta_deg)
                        d = rng_d * math.exp(ob.bias_ln + rng.normal(0, ob.sigma_ln))
                        t_rep = t + (rng_d / 343.0 if kind == "audio" else 0.0)
                        n += 1
                        out.append(Report(
                            report_id=f"R{n:05d}", observer_id=ob.observer_id, obs_lat=ob.lat, obs_lon=ob.lon,
                            t=round(t_rep, 3), bearing_deg=b % 360.0, distance_m=d, kind=kind,
                            drone_class=tg.drone_class, compass_accuracy_deg=None,
                            altitude_cat=(altitude_category(tg.altitude_m)
                                          if rng_alt.uniform() < scn.p_altitude_report else None),
                            emission=None, received_at=round(t_rep + rng.exponential(ob.delay_mean), 3)))
                t += ob.report_interval
        # rapoarte false
        if ob.false_rate_per_h > 0:
            k = rng.poisson(ob.false_rate_per_h * scn.t_end / 3600.0)
            for _ in range(k):
                t = float(rng.uniform(0, scn.t_end))
                n += 1
                out.append(Report(
                    report_id=f"R{n:05d}", observer_id=ob.observer_id, obs_lat=ob.lat, obs_lon=ob.lon,
                    t=round(t, 3), bearing_deg=float(rng.uniform(0, 360)), distance_m=float(rng.uniform(500, 3000)),
                    kind="visual", received_at=round(t + rng.exponential(ob.delay_mean), 3)))
    for (sid, lat, lon, rmax, sigma, pd, kind) in scn.sensors:
        sxy = fr.to_xy(lat, lon)
        for tg in scn.targets:
            if kind == "rf" and tg.emission not in ("mesh", "4g"):
                continue  # RF vede doar variantele care emit
            t = tg.t0
            while t < scn.t_end:
                p = tg.position(t, fr)
                if p is not None and np.linalg.norm(p - sxy) <= rmax and rng.uniform() < pd:
                    z = p + rng.normal(0, sigma, 2)
                    lat_z, lon_z = fr.to_latlon(*z)
                    n += 1
                    out.append(SensorDetection(
                        report_id=f"S{n:05d}", sensor_id=sid, t=round(t, 3), lat=lat_z, lon=lon_z,
                        cov=np.eye(2) * sigma ** 2, drone_class=tg.drone_class, emission=tg.emission,
                        pd=pd, received_at=round(t + 2.0, 3)))
                t += 20.0
    out.extend(generate_radar(scn, seed))
    out.sort(key=lambda r: (r.received_at, r.report_id))
    return out


def generate_radar(scn: Scenario, seed: int = 1) -> list:
    """Radarele simulate emit blocuri ASTERIX CAT048, decodate apoi de adaptor.

    Tintele sub altitudinea minima sau dincolo de orizontul radar nu sunt vazute.
    """
    from .asterix import Cat048Adapter, encode_block  # import local: evita ciclul
    if not scn.radars:
        return []
    rng = np.random.default_rng(seed + 2000)
    fr = scn.frame
    adapter = Cat048Adapter(scn.radars, tod_ref=scn.tod_ref, mode=scn.radar_mode)
    out = []
    for site in scn.radars:
        site.attach(fr)
        truth = scn.radar_truth.get(site.sensor_id, RadarTruth())
        t = 0.0
        while t < scn.t_end:
            recs = []
            for k, tg in enumerate(scn.targets):
                p = tg.position(t, fr)
                if p is None or not site.detects(p, tg.altitude_m) or rng.uniform() >= site.pd:
                    continue
                v = tg.velocity(t, fr)
                if v is None:  # ultimul punct al rutei
                    prev = tg.position(t - 1.0, fr)
                    v = p - prev if prev is not None else np.zeros(2)
                d = p - site.center
                ground = float(np.linalg.norm(d))
                dh = tg.altitude_m - site.antenna_height
                slant = math.hypot(ground, dh)
                az = math.degrees(math.atan2(d[0], d[1]))
                t_stamp = t - truth.clock_offset_s
                if scn.radar_mode == "tracks":
                    noisy = p + rng.normal(0, site.sigma_range, 2)
                    gs = float(np.linalg.norm(v)) + rng.normal(0, 1.0)
                    hdg = math.degrees(math.atan2(v[0], v[1])) + rng.normal(0, 2.0)
                    recs.append({"sac": site.sac, "sic": site.sic, "tod": scn.tod_ref + t_stamp, "typ": 1,
                                 "track_number": 100 + k, "x_m": noisy[0] - site.center[0],
                                 "y_m": noisy[1] - site.center[1], "groundspeed_mps": gs, "heading_deg": hdg,
                                 "track_confirmed": True, "height_m": tg.altitude_m,
                                 "sigma_x_m": site.sigma_range, "sigma_y_m": site.sigma_range,
                                 "sigma_v_mps": 1.0, "sigma_h_deg": 2.0})
                else:
                    rr = float(d @ v) / max(ground, 1.0) + rng.normal(0, site.sigma_rr)
                    recs.append({"sac": site.sac, "sic": site.sic, "tod": scn.tod_ref + t_stamp, "typ": 1,
                                 "range_m": slant + truth.bias_range_m + rng.normal(0, site.sigma_range),
                                 "azimuth_deg": az + truth.bias_az_deg + rng.normal(0, site.sigma_az_deg),
                                 "height_m": tg.altitude_m, "doppler_mps": rr})
            # ecouri false (pasari, clutter), fara inaltime si fara Doppler valid
            if scn.radar_mode == "plots":
                for _ in range(rng.poisson(truth.birds_per_scan)):
                    recs.append({"sac": site.sac, "sic": site.sic, "tod": scn.tod_ref + t, "typ": 1,
                                 "range_m": float(rng.uniform(2000, site.max_range * 0.5)),
                                 "azimuth_deg": float(rng.uniform(0, 360))})
            if recs:
                out.extend(adapter.decode(encode_block(recs), received_at=round(t + 1.0, 3)))
            t += truth.scan_period
    return out


def run(scn: Scenario, tracker, items: list, cycle: float = 30.0, negative_info: bool = True):
    """Alimenteaza trackerul pe cicluri, dupa ora de sosire (rapoartele pot fi intarziate)."""
    if not items:
        return tracker
    t = math.floor(items[0].received_at / cycle) * cycle
    i = 0
    while i < len(items):
        t += cycle
        batch = []
        while i < len(items) and items[i].received_at <= t:
            batch.append(items[i])
            i += 1
        if batch:
            tracker.ingest(batch, now=t)
        if negative_info and (tracker.posts or tracker.radars):
            tracker.apply_negative_info(t)
    return tracker


# ---------------------------------------------------------------- scenarii
def dunare(seed_targets: int = 4, radar: bool = True, radar_mode: str = "plots") -> Scenario:
    """Scenariul "Dunare": tinte foarte jos, din est spre vest, de-a lungul fluviului.

    Un radar de supraveghere (pozitie fictiva) nu vede tintele A, B, C (foarte jos,
    ca la Luncavita), dar vede tinta D (altitudine medie).
    """
    fr = LocalFrame(45.27, 28.46)
    route_a = [(45.345, 28.95), (45.30, 28.70), (45.27, 28.47), (45.285, 28.27), (45.26, 28.05)]
    route_b = [(45.38, 28.85), (45.31, 28.60), (45.272, 28.40), (45.24, 28.15)]
    route_c = [(45.33, 29.05), (45.20, 28.82), (45.18, 28.55)]
    route_d = [(45.42, 28.95), (45.30, 28.62), (45.20, 28.35)]
    targets = [
        Target("A", route_a, 185, t0=0, drone_class="geran-2"),
        Target("B", route_b, 185, t0=1200, drone_class="geran-2", emission="mesh"),
        Target("C", route_c, 160, t0=900, drone_class="gerbera"),
        # tinta la altitudine medie: singura pe care radarul o poate vedea
        Target("D", route_d, 150, t0=300, drone_class="orlan-10", altitude_m=1200.0),
    ][:seed_targets]
    obs_sites = [
        ("O-Isaccea-1", 45.268, 28.455), ("O-Isaccea-2", 45.262, 28.48), ("O-Grindu", 45.278, 28.40),
        ("O-Luncavita", 45.282, 28.268), ("O-Rachelu", 45.29, 28.33), ("O-Vacareni", 45.30, 28.20),
        ("O-Garvan", 45.24, 28.15), ("O-Tulcea-1", 45.18, 28.80), ("O-Tulcea-2", 45.19, 28.72),
        ("O-Nufaru", 45.22, 28.92), ("O-Niculitel", 45.19, 28.48), ("O-Revarsarea", 45.28, 28.60),
        ("O-Parches", 45.25, 28.66), ("O-Somova", 45.19, 28.67),
    ]
    observers = [SimObserver(oid, la, lo) for oid, la, lo in obs_sites]
    observers.append(SimObserver("O-Fals", 45.21, 28.40, false_rate_per_h=6.0, p_report=0.0))
    posts = [FixedPost("P-Macin", fr.to_xy(45.245, 28.13), 2500.0, True, 0.7)]
    dests = [
        Destination("D-Vest", fr.to_xy(45.25, 27.95), 3000.0),
        Destination("D-Sud", fr.to_xy(45.05, 28.55), 3000.0),
        Destination("D-SudVest", fr.to_xy(45.15, 28.10), 3000.0),
    ]
    sensors = [("RF-Isaccea", 45.262, 28.47, 8000.0, 600.0, 0.5, "rf")]
    radars, truth = [], {}
    if radar:
        radars = [RadarSite("R-Dunare", 45.12, 28.62, antenna_height=20.0, max_range=60000.0,
                            min_alt=300.0, sac=1, sic=7).attach(fr)]
        truth = {"R-Dunare": RadarTruth(bias_az_deg=1.0, bias_range_m=120.0, clock_offset_s=0.8)}
    return Scenario("Dunare", fr, targets, observers, posts=posts, sensors=sensors,
                    destinations=dests, t_end=3000.0, radars=radars, radar_truth=truth,
                    radar_mode=radar_mode)


def make_tracker(scn: Scenario, **kw):
    from .tracker import Tracker
    return Tracker(frame=scn.frame, posts=scn.posts, radars=list(scn.radars), **kw)


def parallel_wave(n: int = 12, spacing: float = 1200.0) -> Scenario:
    """Val dens: n tinte paralele spre vest, cu observatori deasupra culoarului."""
    fr = LocalFrame(45.27, 28.46)
    targets = []
    for k in range(n):
        y = (k - (n - 1) / 2) * spacing
        a = fr.to_latlon(30000.0, y)
        b = fr.to_latlon(-30000.0, y)
        targets.append(Target(f"W{k:02d}", [a, b], 185, t0=0.0, drone_class="geran-2"))
    observers = []
    for i, x in enumerate(np.arange(-20000, 20001, 4000)):
        for j, y in enumerate((-4000.0, 0.0, 4000.0)):
            la, lo = fr.to_latlon(float(x), y)
            observers.append(SimObserver(f"O{i:02d}{j}", la, lo, report_interval=45.0, p_report=0.7))
    return Scenario("Val paralel", fr, targets, observers, t_end=1200.0)


def simulate_imm_truth(rng: np.random.Generator, params: Params, n: int, dt: float,
                       x0: np.ndarray) -> np.ndarray:
    """Traiectorie generata chiar din modelul IMM (comutare Markov intre zgomote)."""
    imm = IMM(params)
    T = imm.transition(dt)
    mode = 0
    x = x0.copy()
    xs = [x.copy()]
    F = F_cv(dt)
    for _ in range(n - 1):
        mode = int(rng.choice(len(imm.qs), p=T[mode]))
        Q = Q_cv(dt, imm.qs[mode])
        x = F @ x + rng.multivariate_normal(np.zeros(4), Q)
        xs.append(x.copy())
    return np.array(xs)
