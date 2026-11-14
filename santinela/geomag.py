"""Declinatia magnetica dupa World Magnetic Model 2025 (NOAA NCEI / BGS).

Coeficientii (santinela/data/WMM2025.COF, valabili 2025.0-2030.0) sunt cei publicati de NOAA,
identici in doua redistribuiri independente. Algoritmul urmeaza raportul tehnic WMM:
conversie geodezic -> geocentric (WGS-84), functii Legendre Schmidt semi-normalizate,
campul in coordonate sferice, rotire inapoi in sistemul geodezic. Verificat pe valorile
oficiale de test WMM2025 (tests/test_geomag.py).

Folosit pentru azimuturile raportate fata de nordul magnetic (ex. SAPIENT RANGE_BEARING_DATUM_MAGNETIC).
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

COF = Path(__file__).parent / "data" / "WMM2025.COF"
A_REF = 6371.2               # km, raza de referinta geomagnetica
WGS84_A = 6378.137           # km
WGS84_F = 1 / 298.257223563
E2 = WGS84_F * (2 - WGS84_F)


@lru_cache(maxsize=1)
def _model():
    lines = COF.read_text().splitlines()
    epoch = float(lines[0].split()[0])
    g, h, gd, hd = {}, {}, {}, {}
    nmax = 0
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 6 or parts[0].startswith("9999"):
            break
        n, m = int(parts[0]), int(parts[1])
        g[n, m], h[n, m], gd[n, m], hd[n, m] = (float(x) for x in parts[2:6])
        nmax = max(nmax, n)
    return epoch, nmax, g, h, gd, hd


def field(lat: float, lon: float, height_km: float = 0.0, year: float | None = None) -> dict:
    """Componentele campului (nT) si unghiurile (grade): X, Y, Z, H, F, D (declinatie), I (inclinatie)."""
    epoch, N, g0, h0, gd, hd = _model()
    year = decimal_year() if year is None else year
    dt = year - epoch
    phi, lam = math.radians(lat), math.radians(lon)
    # geodezic -> geocentric sferic
    rc = WGS84_A / math.sqrt(1 - E2 * math.sin(phi) ** 2)
    p = (rc + height_km) * math.cos(phi)
    z = (rc * (1 - E2) + height_km) * math.sin(phi)
    r = math.hypot(p, z)
    phic = math.asin(z / r)
    theta = math.pi / 2 - phic
    ct, st = math.cos(theta), math.sin(theta)
    # functii Legendre (normare Gauss) si derivatele fata de colatitudine
    P = [[0.0] * (N + 1) for _ in range(N + 1)]
    dP = [[0.0] * (N + 1) for _ in range(N + 1)]
    P[0][0] = 1.0
    for n in range(1, N + 1):
        for m in range(n + 1):
            if n == m:
                P[n][m] = st * P[n - 1][m - 1]
                dP[n][m] = st * dP[n - 1][m - 1] + ct * P[n - 1][m - 1]
            elif n == 1 or m == n - 1:
                P[n][m] = ct * P[n - 1][m]
                dP[n][m] = ct * dP[n - 1][m] - st * P[n - 1][m]
            else:
                k = ((n - 1) ** 2 - m ** 2) / ((2 * n - 1) * (2 * n - 3))
                P[n][m] = ct * P[n - 1][m] - k * P[n - 2][m]
                dP[n][m] = ct * dP[n - 1][m] - st * P[n - 1][m] - k * dP[n - 2][m]
    # factorii Schmidt (semi-normare) aplicati coeficientilor
    S = [[0.0] * (N + 1) for _ in range(N + 1)]
    S[0][0] = 1.0
    for n in range(1, N + 1):
        S[n][0] = S[n - 1][0] * (2 * n - 1) / n
        for m in range(1, n + 1):
            S[n][m] = S[n][m - 1] * math.sqrt((n - m + 1) * (2 if m == 1 else 1) / (n + m))
    br = bt = bp = 0.0
    for n in range(1, N + 1):
        ar = (A_REF / r) ** (n + 2)
        for m in range(n + 1):
            gs = S[n][m] * (g0[n, m] + dt * gd[n, m])
            hs = S[n][m] * (h0[n, m] + dt * hd[n, m])
            cm, sm = math.cos(m * lam), math.sin(m * lam)
            t = gs * cm + hs * sm
            br += (n + 1) * ar * t * P[n][m]
            bt -= ar * t * dP[n][m]
            bp += ar * m * (gs * sm - hs * cm) * P[n][m] / st if st > 1e-12 else 0.0
    xs, ys, zs = -bt, bp, -br
    psi = phic - phi
    x = xs * math.cos(psi) - zs * math.sin(psi)
    zz = xs * math.sin(psi) + zs * math.cos(psi)
    y = ys
    hh = math.hypot(x, y)
    return {"X": x, "Y": y, "Z": zz, "H": hh, "F": math.hypot(hh, zz),
            "D": math.degrees(math.atan2(y, x)), "I": math.degrees(math.atan2(zz, hh))}


def declination(lat: float, lon: float, height_km: float = 0.0, year: float | None = None) -> float:
    """Declinatia magnetica in grade (pozitiva spre est): azimut adevarat = azimut magnetic + D."""
    return field(lat, lon, height_km, year)["D"]


def decimal_year(dt: datetime | None = None) -> float:
    dt = dt or datetime.now(timezone.utc)
    start = datetime(dt.year, 1, 1, tzinfo=timezone.utc)
    end = datetime(dt.year + 1, 1, 1, tzinfo=timezone.utc)
    return dt.year + (dt - start).total_seconds() / (end - start).total_seconds()


def valid(year: float) -> bool:
    epoch = _model()[0]
    return epoch <= year < epoch + 5.0
