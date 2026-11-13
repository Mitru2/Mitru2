"""Cursor-on-Target (CoT): intrare de la TAK/ATAK si iesire spre TAK.

Tipul CoT urmeaza MIL-STD-2525 / APP-6: ``a-<afiliere>-<dimensiune>-...``
(afiliere: f prieten, a presupus prieten, h ostil, s suspect, u necunoscut,
n neutru, p in asteptare, j/k exercitiu; dimensiune: A aer, G sol, S mare ...).

* aer ostil / suspect / necunoscut / in asteptare -> traseu pentru tracker;
* prieten / presupus prieten (orice dimensiune) -> pozitie de echipa;
* aer neutru -> aeronava cooperativa.

``ce`` (eroare circulara) se ia ca 1 sigma in metri; 9999999 inseamna necunoscut.
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np

UNKNOWN_ERR = 9999999.0


@dataclass
class CotEvent:
    uid: str
    type: str
    how: str
    time: str
    lat: float
    lon: float
    hae: float | None
    ce: float | None
    le: float | None
    course: float | None
    speed: float | None
    callsign: str | None

    @property
    def affiliation(self) -> str:
        parts = self.type.split("-")
        return parts[1] if len(parts) > 1 and parts[0] == "a" else ""

    @property
    def dimension(self) -> str:
        parts = self.type.split("-")
        return parts[2] if len(parts) > 2 and parts[0] == "a" else ""

    @property
    def role(self) -> str:
        """'tinta' | 'prieten' | 'cooperativ' | 'altul'."""
        a, d = self.affiliation, self.dimension
        if a in ("f", "a"):
            return "prieten"
        if d == "A" and a in ("h", "s", "u", "p", "j", "k"):
            return "tinta"
        if d == "A" and a == "n":
            return "cooperativ"
        return "altul"


def parse(text: str) -> list[CotEvent]:
    """Unul sau mai multe evenimente <event> (si radacini care le contin)."""
    text = text.strip()
    if text.count("<event") > 1 or not text.startswith("<event"):
        text = f"<root>{_strip_decl(text)}</root>"
    root = ET.fromstring(text)
    events = [root] if root.tag == "event" else root.iter("event")
    out = []
    for ev in events:
        pt = ev.find("point")
        if pt is None:
            continue
        det = ev.find("detail")
        trk = det.find("track") if det is not None else None
        contact = det.find("contact") if det is not None else None

        def f(el, k):
            v = el.get(k) if el is not None else None
            try:
                return float(v) if v not in (None, "") else None
            except ValueError:
                return None

        out.append(CotEvent(
            uid=ev.get("uid", ""), type=ev.get("type", ""), how=ev.get("how", ""),
            time=ev.get("time") or ev.get("start") or "",
            lat=float(pt.get("lat")), lon=float(pt.get("lon")),
            hae=f(pt, "hae"), ce=f(pt, "ce"), le=f(pt, "le"),
            course=f(trk, "course"), speed=f(trk, "speed"),
            callsign=contact.get("callsign") if contact is not None else None))
    return out


def _strip_decl(t: str) -> str:
    if t.startswith("<?xml"):
        return t[t.index("?>") + 2:]
    return t


def looks_like_cot(text: str) -> bool:
    return "<event" in text and "<point" in text and "type=" in text


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def common_to_xml(ct, now_utc: datetime, stale_s: float = 60.0, how: str = "m-f") -> str:
    """Evenimentul CoT al unui traseu comun (trackmodel.CommonTrack).

    UID stabil pe toata viata traseului; ``stale`` = cat ramane valabil fara actualizare;
    elipsa de 95% in <shape><ellipse>; identitatea APP-6 si SIDC in <__santinela>.
    """
    t0 = _iso(now_utc)
    ev = ET.Element("event", version="2.0", uid=ct.uid, type=ct.cot_type, how=how, time=t0, start=t0,
                    stale=_iso(now_utc + timedelta(seconds=stale_s)))
    ET.SubElement(ev, "point", lat=f"{ct.lat:.7f}", lon=f"{ct.lon:.7f}",
                  hae=f"{ct.hae:.1f}" if ct.hae is not None else f"{UNKNOWN_ERR:.0f}",
                  ce=f"{ct.ce:.1f}", le=f"{ct.le:.1f}" if ct.le is not None else f"{UNKNOWN_ERR:.0f}")
    det = ET.SubElement(ev, "detail")
    ET.SubElement(det, "track", course=f"{ct.course:.1f}", speed=f"{ct.speed:.1f}")
    ET.SubElement(det, "contact", callsign=f"DRONA {ct.track_id}")
    shape = ET.SubElement(det, "shape")
    ET.SubElement(shape, "ellipse", major=f"{ct.ellipse[0]:.1f}", minor=f"{ct.ellipse[1]:.1f}",
                  angle=f"{ct.ellipse[2]:.1f}")
    ET.SubElement(det, "__santinela", identity=ct.identity, sidc=ct.sidc, status=ct.status,
                  score=f"{ct.score:.1f}", sources=",".join(ct.sources),
                  cooperative=ct.cooperative_id or "", operator_in_loop=str(ct.operator_in_loop).lower())
    remarks = (f"{ct.status}; identitate {ct.identity}"
               + (f" ({ct.identity_author})" if ct.identity_author else "")
               + f"; surse: {len(ct.sources)}; clasa: {ct.drone_class or 'necunoscuta'}"
               + ("; posibil operator in bucla" if ct.operator_in_loop else ""))
    ET.SubElement(det, "remarks").text = remarks
    return ET.tostring(ev, encoding="unicode")


def delete_xml(uid: str, now_utc: datetime, target_type: str = "a-u-A") -> str:
    """Stergerea unui traseu din TAK: eveniment t-x-d-d cu legatura spre UID-ul traseului."""
    t0 = _iso(now_utc)
    ev = ET.Element("event", version="2.0", uid=f"{uid}-del", type="t-x-d-d", how="h-g-i-g-o",
                    time=t0, start=t0, stale=_iso(now_utc + timedelta(seconds=20)))
    ET.SubElement(ev, "point", lat="0", lon="0", hae=f"{UNKNOWN_ERR:.0f}", ce=f"{UNKNOWN_ERR:.0f}",
                  le=f"{UNKNOWN_ERR:.0f}")
    det = ET.SubElement(ev, "detail")
    ET.SubElement(det, "link", uid=uid, relation="none", type=target_type)
    ET.SubElement(det, "__forcedelete")
    return ET.tostring(ev, encoding="unicode")


def track_to_cot(track, frame, now_utc: datetime, stale_s: float = 60.0, uid_prefix: str = "SANTINELA") -> str:
    """Compatibilitate: traseu -> eveniment CoT prin modelul comun (identitate APP-6)."""
    from .trackmodel import from_track
    return common_to_xml(from_track(track, frame, uid_prefix), now_utc, stale_s)


# ------------------------------------------------------------------ TAK Protocol v1 (protobuf)
# Schema din descriptorii takproto 3.0.1 (MIT), citita ca date: TakMessage{takControl=1, cotEvent=2};
# CotEvent{type=1, access=2, qos=3, opex=4, uid=5, sendTime=6, startTime=7, staleTime=8 (uint64 ms),
# how=9, lat=10, lon=11, hae=12, ce=13, le=14 (double), detail=15}; Detail{xmlDetail=1, contact=2,
# group=3, precisionLocation=4, status=5, takv=6, track=7}; Contact{endpoint=1, callsign=2};
# Track{speed=1, course=2}.
TAK_SCHEMA = """
syntax = "proto3";
package atakmap.commoncommo.protobuf.v1;
message TakControl { uint32 minProtoVersion = 1; uint32 maxProtoVersion = 2; string contactUid = 3; }
message Contact { string endpoint = 1; string callsign = 2; }
message Group { string name = 1; string role = 2; }
message PrecisionLocation { string geopointsrc = 1; string altsrc = 2; }
message Status { uint32 battery = 1; }
message Takv { string device = 1; string platform = 2; string os = 3; string version = 4; }
message Track { double speed = 1; double course = 2; }
message Detail { string xmlDetail = 1; Contact contact = 2; Group group = 3; PrecisionLocation precisionLocation = 4;
                 Status status = 5; Takv takv = 6; Track track = 7; }
message CotEvent { string type = 1; string access = 2; string qos = 3; string opex = 4; string uid = 5;
                   uint64 sendTime = 6; uint64 startTime = 7; uint64 staleTime = 8; string how = 9;
                   double lat = 10; double lon = 11; double hae = 12; double ce = 13; double le = 14;
                   Detail detail = 15; }
message TakMessage { TakControl takControl = 1; CotEvent cotEvent = 2; }
"""
MESH_HEADER = b"\xbf\x01\xbf"
_tak = None


def _tak_schema():
    global _tak
    if _tak is None:
        from .protowire import ProtoSchema
        _tak = ProtoSchema.from_text(TAK_SCHEMA)
    return _tak


def _ms(iso: str) -> int:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return int(round(dt.timestamp() * 1000))


def xml_to_tak(xml: str, mode: str = "mesh") -> bytes:
    """Eveniment CoT XML -> TAK Protocol v1 ("mesh" pe UDP, "stream" pe TCP).

    Elementele <contact> si <track> trec in campurile tipizate; restul detaliilor in xmlDetail.
    """
    from .protowire import encode_varint
    ev = ET.fromstring(xml)
    pt = ev.find("point")
    det = ev.find("detail")
    detail: dict = {}
    if det is not None:
        rest = []
        for child in det:
            if child.tag == "contact":
                detail["contact"] = {k: v for k, v in (("endpoint", child.get("endpoint")),
                                                        ("callsign", child.get("callsign"))) if v}
            elif child.tag == "track":
                detail["track"] = {"speed": float(child.get("speed", 0)), "course": float(child.get("course", 0))}
            else:
                rest.append(ET.tostring(child, encoding="unicode"))
        if rest:
            detail["xmlDetail"] = "".join(rest)
    cot = {"type": ev.get("type"), "uid": ev.get("uid"), "how": ev.get("how", ""),
           "sendTime": _ms(ev.get("time")), "startTime": _ms(ev.get("start", ev.get("time"))),
           "staleTime": _ms(ev.get("stale")),
           "lat": float(pt.get("lat")), "lon": float(pt.get("lon")), "hae": float(pt.get("hae", UNKNOWN_ERR)),
           "ce": float(pt.get("ce", UNKNOWN_ERR)), "le": float(pt.get("le", UNKNOWN_ERR))}
    if detail:
        cot["detail"] = detail
    body = _tak_schema().encode("TakMessage", {"cotEvent": cot})
    if mode == "mesh":
        return MESH_HEADER + body
    if mode == "stream":
        return b"\xbf" + encode_varint(len(body)) + body
    raise ValueError(mode)


def tak_to_xml(data: bytes) -> str:
    """TAK Protocol v1 (mesh sau stream) -> eveniment CoT XML."""
    from .protowire import _varint
    if data[:3] == MESH_HEADER:
        body = data[3:]
    elif data[:1] == b"\xbf":
        n, i = _varint(data, 1)
        body = data[i:i + n]
    else:
        raise ValueError("nu e TAK Protocol v1")
    msg = _tak_schema().decode("TakMessage", body)["cotEvent"]

    def iso(ms):
        return _iso(datetime.fromtimestamp(ms / 1000.0, timezone.utc))

    ev = ET.Element("event", version="2.0", uid=msg.get("uid", ""), type=msg.get("type", ""), how=msg.get("how", ""),
                    time=iso(msg.get("sendTime", 0)), start=iso(msg.get("startTime", 0)),
                    stale=iso(msg.get("staleTime", 0)))
    ET.SubElement(ev, "point", lat=repr(msg.get("lat", 0.0)), lon=repr(msg.get("lon", 0.0)),
                  hae=repr(msg.get("hae", 0.0)), ce=repr(msg.get("ce", 0.0)), le=repr(msg.get("le", 0.0)))
    det = ET.SubElement(ev, "detail")
    d = msg.get("detail", {})
    if "track" in d:
        ET.SubElement(det, "track", speed=repr(d["track"].get("speed", 0.0)), course=repr(d["track"].get("course", 0.0)))
    if "contact" in d:
        ET.SubElement(det, "contact", **{k: v for k, v in d["contact"].items()})
    if d.get("xmlDetail"):
        for child in ET.fromstring(f"<x>{d['xmlDetail']}</x>"):
            det.append(child)
    return ET.tostring(ev, encoding="unicode")


# ------------------------------------------------------------------ trimitere in retea
class CotSender:
    """Trimite evenimente CoT: UDP (implicit multicast 239.2.3.1:6969, retea mesh TAK), TCP sau TLS
    catre un TAK Server. ``protocol``: "xml" (versiunea 0) sau "tak" (TAK Protocol v1)."""

    def __init__(self, transport: str = "udp", host: str = "239.2.3.1", port: int = 6969,
                 protocol: str = "xml", ttl: int = 1, ssl_context=None, timeout: float = 5.0):
        import socket
        self.transport, self.host, self.port, self.protocol = transport, host, port, protocol
        if transport == "udp":
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
        elif transport in ("tcp", "tls"):
            raw = socket.create_connection((host, port), timeout=timeout)
            if transport == "tls":
                if ssl_context is None:
                    raise ValueError("TLS cere ssl_context (certificatul clientului emis de TAK Server)")
                raw = ssl_context.wrap_socket(raw, server_hostname=host)
            self.sock = raw
        else:
            raise ValueError(transport)

    def _bytes(self, xml: str) -> bytes:
        if self.protocol == "tak":
            return xml_to_tak(xml, "mesh" if self.transport == "udp" else "stream")
        return xml.encode("utf-8")

    def send(self, xml: str) -> None:
        data = self._bytes(xml)
        if self.transport == "udp":
            self.sock.sendto(data, (self.host, self.port))
        else:
            self.sock.sendall(data)

    def close(self) -> None:
        self.sock.close()


class CotPublisher:
    """Publica periodic traseele: actualizari cu UID stabil, iar pentru traseele disparute
    (sterse, unite, terminate) trimite un eveniment de stergere o singura data."""

    def __init__(self, sender, frame, instance: str = "SANTINELA", stale_s: float = 60.0,
                 include_tentative: bool = False, context: str = "realitate"):
        self.sender, self.frame, self.instance = sender, frame, instance
        self.stale_s, self.include_tentative, self.context = stale_s, include_tentative, context
        self.published: dict = {}   # uid -> tip CoT

    def publish(self, tracker, now_utc: datetime) -> dict:
        from .trackmodel import from_track
        live = {}
        for tr in tracker.live_tracks(tracker.now):
            if tr.status != "confirmat" and not (self.include_tentative and tr.status == "tentativ"):
                continue
            ct = from_track(tr, self.frame, self.instance, self.context)
            live[ct.uid] = ct
        sent = {"actualizari": [], "stergeri": []}
        for uid, ct in sorted(live.items()):
            self.sender.send(common_to_xml(ct, now_utc, self.stale_s))
            self.published[uid] = ct.cot_type
            sent["actualizari"].append(uid)
        for uid in sorted(set(self.published) - set(live)):
            self.sender.send(delete_xml(uid, now_utc, self.published.pop(uid)))
            sent["stergeri"].append(uid)
        return sent


def utc_from_tod(tod: float, day: datetime | None = None) -> datetime:
    day = day or datetime.now(timezone.utc)
    base = day.replace(hour=0, minute=0, second=0, microsecond=0)
    return base + timedelta(seconds=tod)
