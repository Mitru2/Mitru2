"""STANAG 4607 (AEDP-7) - format NATO pentru detectii GMTI (Ground Moving Target Indicator).

Decodeaza pachete (antet de 32 de octeti + segmente) si extrage din segmentele
"dwell" (tip 2) pozitia senzorului si rapoartele de tinta: pozitie WGS-84,
inaltime, viteza pe linia de vizare, incertitudini, clasificare.
Segmentul "mission" (tip 1) da data de referinta pentru ora dwell-ului.

Codari (AEDP-7), verificate pe disectorul Wireshark packet-stanag4607.c: SA32 = unghi cu
semn, LSB 45/2^30 grd (+-90); BA32 = unghi fara semn, LSB 360/2^32 grd; SA16 si BA16 LSB
90/2^14 grd; B16 = valoare/128. Masca de existenta: 64 de biti, bitul cel mai semnificativ =
D2, apoi D3 ... D31, D32.1 ... D32.18, ultimii 16 biti rezervati.

Segmentul job (tip 5) da modelul de teren si de geoid folosit pentru inaltimi (D32.6);
indicatorul din antet distinge operatie/exercitiu si date reale/simulate/sintetizate.
Ramane de validat pe trafic real de la senzorul tinta.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

PACKET_HEADER = 32
SEGMENT_HEADER = 5

# (camp, tip, octeti) in ordinea mastii de existenta
DWELL_FIELDS = [
    ("D2", "revisit_index", "I", 2), ("D3", "dwell_index", "I", 2), ("D4", "last_dwell", "I", 1),
    ("D5", "target_count", "I", 2), ("D6", "dwell_time_ms", "I", 4), ("D7", "sensor_lat", "SA32", 4),
    ("D8", "sensor_lon", "BA32", 4), ("D9", "sensor_alt_cm", "S", 4), ("D10", "lat_scale", "SA32", 4),
    ("D11", "lon_scale", "BA32", 4), ("D12", "sensor_unc_along_cm", "I", 4), ("D13", "sensor_unc_cross_cm", "I", 4),
    ("D14", "sensor_unc_alt_cm", "I", 2), ("D15", "sensor_track_deg", "BA16", 2), ("D16", "sensor_speed_mms", "I", 4),
    ("D17", "sensor_vvel_dms", "S", 1), ("D18", "sensor_track_unc_deg", "I", 1), ("D19", "sensor_speed_unc_mms", "I", 2),
    ("D20", "sensor_vvel_unc_cms", "I", 2), ("D21", "platform_heading_deg", "BA16", 2),
    ("D22", "platform_pitch_deg", "SA16", 2), ("D23", "platform_roll_deg", "SA16", 2),
    ("D24", "dwell_center_lat", "SA32", 4), ("D25", "dwell_center_lon", "BA32", 4),
    ("D26", "dwell_range_half_km", "B16", 2), ("D27", "dwell_angle_half_deg", "BA16", 2),
    ("D28", "sensor_heading_deg", "BA16", 2), ("D29", "sensor_pitch_deg", "SA16", 2),
    ("D30", "sensor_roll_deg", "SA16", 2), ("D31", "mdv_dms", "I", 1),
]
TARGET_FIELDS = [
    ("D32.1", "report_index", "I", 2), ("D32.2", "lat", "SA32", 4), ("D32.3", "lon", "BA32", 4),
    ("D32.4", "delta_lat", "S", 2), ("D32.5", "delta_lon", "S", 2), ("D32.6", "height_m", "S", 2),
    ("D32.7", "los_velocity_cms", "S", 2), ("D32.8", "wrap_velocity_cms", "I", 2), ("D32.9", "snr_db", "S", 1),
    ("D32.10", "classification", "I", 1), ("D32.11", "class_probability", "I", 1),
    ("D32.12", "slant_range_unc_cm", "I", 2), ("D32.13", "cross_range_unc_dm", "I", 2),
    ("D32.14", "height_unc_m", "I", 1), ("D32.15", "los_velocity_unc_cms", "I", 2),
    ("D32.16", "truth_tag_app", "I", 1), ("D32.17", "truth_tag_entity", "I", 4), ("D32.18", "rcs_half_dbsm", "S", 1),
]
CLASSIFICATION = {  # D32.10 (conventia 'stanag4607.classification'); NU exista clasa UAS
    0: "fara informatie", 1: "vehicul pe senile", 2: "vehicul cu roti", 3: "aeronava cu aripa rotativa",
    4: "aeronava cu aripa fixa", 5: "rotator stationar", 6: "maritim", 7: "baliza", 8: "amfibiu",
    9: "persoana", 10: "vehicul", 11: "animal", 12: "retur multiplu mare (sol)",
    13: "retur multiplu mare (maritim)", 126: "altul", 127: "necunoscut", 143: "dispozitiv de marcare (tagging)",
}
GEOID_MODELS = {0: "nespecificat", 1: "EGM96", 2: "GEO96", 3: "Pamant plat"}
DATA_KIND = {0: "real", 1: "simulat", 2: "sintetizat"}


def classification(code: int) -> tuple[str, bool]:
    """(eticheta, simulat). Valorile 128-255 sunt aceleasi clase marcate simulat (254 altul, 255 necunoscut)."""
    if code == 143:
        return CLASSIFICATION[143], False
    if code >= 128:
        base = {254: 126, 255: 127}.get(code, code - 128)
        return CLASSIFICATION.get(base, f"cod {code}"), True
    return CLASSIFICATION.get(code, f"cod {code}"), False


class Stanag4607Error(ValueError):
    pass


def _val(kind: str, b: bytes) -> float:
    n = len(b)
    if kind == "I":
        return int.from_bytes(b, "big")
    if kind == "S":
        return int.from_bytes(b, "big", signed=True)
    if kind == "SA32":
        return int.from_bytes(b, "big", signed=True) * 45.0 / 2 ** 30
    if kind == "BA32":
        v = int.from_bytes(b, "big") * 360.0 / 2 ** 32
        return v - 360.0 if v > 180.0 else v
    if kind == "SA16":
        return int.from_bytes(b, "big", signed=True) * 90.0 / 2 ** 14
    if kind == "BA16":
        return int.from_bytes(b, "big") * 360.0 / 2 ** 16
    if kind == "B16":
        return int.from_bytes(b, "big") / 128.0
    raise Stanag4607Error(f"tip {kind} ({n} octeti)")


def _enc(kind: str, v: float, n: int) -> bytes:
    if kind == "I":
        return int(round(v)).to_bytes(n, "big")
    if kind == "S":
        return int(round(v)).to_bytes(n, "big", signed=True)
    if kind == "SA32":
        return int(round(v * 2 ** 30 / 45.0)).to_bytes(4, "big", signed=True)
    if kind == "BA32":
        return (int(round((v % 360.0) * 2 ** 32 / 360.0)) % 2 ** 32).to_bytes(4, "big")
    if kind == "SA16":
        return int(round(v * 2 ** 14 / 90.0)).to_bytes(2, "big", signed=True)
    if kind == "BA16":
        return (int(round((v % 360.0) * 2 ** 16 / 360.0)) % 2 ** 16).to_bytes(2, "big")
    if kind == "B16":
        return int(round(v * 128)).to_bytes(2, "big")
    raise Stanag4607Error(kind)


@dataclass
class Dwell:
    platform_id: str
    mission_id: int
    nationality: str
    exercise: bool
    fields: dict
    targets: list = field(default_factory=list)
    reference_date: tuple | None = None  # (an, luna, zi) din segmentul mission
    data_kind: str = "real"              # real | simulat | sintetizat (antet, P7)
    geoid_model: str | None = None       # din segmentul job: referinta inaltimilor D32.6
    edition: str = ""

    @property
    def tod(self) -> float:
        return (self.fields["dwell_time_ms"] / 1000.0) % 86400.0


def looks_like_4607(buf: bytes) -> bool:
    if not isinstance(buf, (bytes, bytearray)) or len(buf) < PACKET_HEADER + SEGMENT_HEADER:
        return False
    pos = 0
    try:
        while pos < len(buf):
            # P1: editia ('0'-'3' STANAG, '4'-'9' AEDP-4607 A-F) si versiunea, ambele cifre ASCII
            if not (0x30 <= buf[pos] <= 0x39 and 0x30 <= buf[pos + 1] <= 0x39):
                return False
            size = struct.unpack(">I", buf[pos + 2:pos + 6])[0]
            if size < PACKET_HEADER or pos + size > len(buf):
                return False
            pos += size
    except struct.error:
        return False
    return pos == len(buf)


def decode(buf: bytes) -> list[Dwell]:
    """Toate segmentele dwell din unul sau mai multe pachete concatenate."""
    out = []
    pos = 0
    ref_date = None
    geoid = {}  # job id -> model de geoid
    while pos < len(buf):
        if len(buf) - pos < PACKET_HEADER:
            raise Stanag4607Error("antet de pachet trunchiat")
        hdr = buf[pos:pos + PACKET_HEADER]
        size = struct.unpack(">I", hdr[2:6])[0]
        if size < PACKET_HEADER or pos + size > len(buf):
            raise Stanag4607Error("dimensiune de pachet invalida")
        nationality = hdr[6:8].decode("ascii", "replace")
        exercise = hdr[13] >= 128  # 128+ = exercitiu (AEDP-7, P7)
        kind = DATA_KIND.get(hdr[13] & 0x7F, f"cod {hdr[13]}")
        job_id = struct.unpack(">I", hdr[28:32])[0]
        e = hdr[0]
        edition = f"STANAG {chr(e)}" if e <= 0x33 else f"AEDP-4607 {chr(e + 13)}"
        platform = hdr[14:24].decode("ascii", "replace").strip("\x00 ")
        mission = struct.unpack(">I", hdr[24:28])[0]
        i, end = pos + PACKET_HEADER, pos + size
        while i < end:
            stype = buf[i]
            ssize = struct.unpack(">I", buf[i + 1:i + 5])[0]
            if ssize < SEGMENT_HEADER or i + ssize > end:
                raise Stanag4607Error("dimensiune de segment invalida")
            body = buf[i + SEGMENT_HEADER:i + ssize]
            if stype == 1 and len(body) >= 39:
                ref_date = (struct.unpack(">H", body[35:37])[0], body[37], body[38])
            elif stype == 5 and len(body) >= 68:
                geoid[struct.unpack(">I", body[0:4])[0]] = GEOID_MODELS.get(body[67], f"cod {body[67]}")
            elif stype == 2:
                d = _dwell(body, platform, mission, nationality, exercise)
                d.reference_date = ref_date
                d.data_kind = kind
                d.geoid_model = geoid.get(job_id)
                d.edition = edition
                out.append(d)
            i += ssize
        pos = end
    return out


def _dwell(body: bytes, platform, mission, nationality, exercise) -> Dwell:
    mask = int.from_bytes(body[:8], "big")
    i = 8
    fields = {}
    names = [f for f in DWELL_FIELDS] + [f for f in TARGET_FIELDS]
    present = {names[k][0] for k in range(len(names)) if mask & (1 << (63 - k))}
    for fid, name, kind, n in DWELL_FIELDS:
        if fid in present:
            fields[name] = _val(kind, body[i:i + n])
            i += n
    for req in ("dwell_time_ms", "sensor_lat", "sensor_lon", "target_count"):
        if req not in fields:
            raise Stanag4607Error(f"camp obligatoriu lipsa: {req}")
    targets = []
    for _ in range(int(fields["target_count"])):
        t = {}
        for fid, name, kind, n in TARGET_FIELDS:
            if fid in present:
                if i + n > len(body):
                    raise Stanag4607Error("raport de tinta trunchiat")
                t[name] = _val(kind, body[i:i + n])
                i += n
        if "lat" not in t and "delta_lat" in t:
            t["lat"] = fields["dwell_center_lat"] + t["delta_lat"] * fields.get("lat_scale", 0.0)
            t["lon"] = fields["dwell_center_lon"] + t["delta_lon"] * fields.get("lon_scale", 0.0)
        targets.append(t)
    return Dwell(platform, mission, nationality, exercise, fields, targets)


def encode_dwell_packet(fields: dict, targets: list, platform: str = "SIM", mission: int = 1,
                        nationality: str = "RO", exercise: bool = True, ref_date=(2026, 11, 12),
                        data_kind: int = 0, job_id: int = 0, geoid_model: int | None = None) -> bytes:
    """Pachet de test: segment mission + un segment dwell (doar pentru simulare si teste)."""
    names = DWELL_FIELDS + TARGET_FIELDS
    present = set(fields) | {k for t in targets for k in t}
    mask = 0
    for k, (fid, name, kind, n) in enumerate(names):
        if name in present:
            mask |= 1 << (63 - k)
    body = mask.to_bytes(8, "big")
    for fid, name, kind, n in DWELL_FIELDS:
        if name in fields:
            body += _enc(kind, fields[name], n)
    for t in targets:
        for fid, name, kind, n in TARGET_FIELDS:
            if name in present:
                body += _enc(kind, t[name], n)
    dwell = bytes([2]) + (len(body) + SEGMENT_HEADER).to_bytes(4, "big") + body
    mbody = b"PLAN".ljust(12, b" ") + b"FLIGHT".ljust(12, b" ") + bytes([0]) + b"CFG".ljust(10, b" ") \
        + struct.pack(">HBB", *ref_date)
    mseg = bytes([1]) + (len(mbody) + SEGMENT_HEADER).to_bytes(4, "big") + mbody
    jseg = b""
    if geoid_model is not None:
        jbody = job_id.to_bytes(4, "big") + bytes(63) + bytes([geoid_model])
        jseg = bytes([5]) + (len(jbody) + SEGMENT_HEADER).to_bytes(4, "big") + jbody
    size = PACKET_HEADER + len(mseg) + len(jseg) + len(dwell)
    hdr = (b"31" + size.to_bytes(4, "big") + nationality.encode()[:2].ljust(2) + bytes([5]) + b"XN"
           + b"\x00\x00" + bytes([(128 if exercise else 0) | data_kind]) + platform.encode()[:10].ljust(10, b"\x00")
           + mission.to_bytes(4, "big") + job_id.to_bytes(4, "big"))
    return hdr + mseg + jseg + dwell
