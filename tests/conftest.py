import math

import numpy as np
import pytest

from santinela import Report
from santinela.geo import LocalFrame


@pytest.fixture
def frame():
    return LocalFrame(45.27, 28.46)


def make_report(frame, rid, obs_id, obs_xy, target_xy, t, rng=None, sigma_deg=0.0, sigma_ln=0.0,
                kind="visual", drone_class="geran-2", with_distance=True):
    """Raport sintetic catre o pozitie reala, cu zgomot optional."""
    d = np.asarray(target_xy) - np.asarray(obs_xy)
    b = math.degrees(math.atan2(d[0], d[1]))
    dist = float(np.linalg.norm(d))
    if rng is not None:
        b += rng.normal(0, sigma_deg)
        dist *= math.exp(rng.normal(0, sigma_ln))
    lat, lon = frame.to_latlon(*obs_xy)
    return Report(report_id=rid, observer_id=obs_id, obs_lat=lat, obs_lon=lon, t=t,
                  bearing_deg=b % 360, distance_m=dist if with_distance else None, kind=kind,
                  drone_class=drone_class, received_at=t)


def straight_target(t, x0=(20000.0, 0.0), v=(-50.0, 0.0)):
    return np.array(x0) + np.array(v) * t
