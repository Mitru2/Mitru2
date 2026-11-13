"""SANTINELA ca nod de fuziune SAPIENT (BSI Flex 335 v2.0), bidirectional.

Spre senzori (ASM-uri care se conecteaza la SANTINELA):
  Registration -> RegistrationAck; StatusReport / DetectionReport -> tracker;
  Alert -> AlertAck; Task (trimis de noi, ex. look_at spre un traseu) -> TaskAck urmarit;
  Error -> jurnal.
Spre un nod de fuziune superior (SANTINELA ca nod-copil, NODE_TYPE_FUSION_NODE):
  Registration proprie, apoi DetectionReport pentru fiecare traseu (object_id ULID stabil).

Transport: TCP, fiecare mesaj precedat de lungimea lui pe 4 octeti little-endian
(conventia 'sapient.framing'). Mesajele se codeaza/decodeaza cu schema oficiala
(``santinela.sapient_proto.load_schema``).

Politica de inregistrare: tolerant implicit (raportul de interoperabilitate arata ca
majoritatea mostrelor incalca macar o regula obligatorie): accepta si pastreaza
abaterile; cu ``strict=True`` respinge si trimite motivele in ack_response_reason.
Validarea finala se face cu Test Harness-ul Dstl (BSI-Flex-335-v2-Test-Harness).
"""
from __future__ import annotations

import asyncio
import math
import os
import threading
import time
import uuid
from dataclasses import dataclass, field


_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid(t_ms: int | None = None, rand: bytes | None = None) -> str:
    """ULID: 48 biti timp (ms) + 80 biti aleatori, in base32 Crockford (26 caractere)."""
    t_ms = int(time.time() * 1000) if t_ms is None else int(t_ms)
    rand = os.urandom(10) if rand is None else rand
    v = (t_ms << 80) | int.from_bytes(rand[:10].rjust(10, b"\x00"), "big")
    return "".join(_CROCKFORD[(v >> (5 * (25 - k))) & 0x1F] for k in range(26))


def frame(payload: bytes) -> bytes:
    return len(payload).to_bytes(4, "little") + payload


class FrameReader:
    """Reasambleaza mesajele dintr-un flux TCP (prefix de lungime pe 4 octeti, little-endian)."""

    def __init__(self, max_size: int = 1024 * 1024):
        self.buf = bytearray()
        self.max_size = max_size

    def feed(self, data: bytes) -> list[bytes]:
        self.buf += data
        out = []
        while len(self.buf) >= 4:
            n = int.from_bytes(self.buf[:4], "little")
            if n > self.max_size:
                raise ValueError(f"mesaj SAPIENT de {n} octeti depaseste limita {self.max_size}")
            if len(self.buf) < 4 + n:
                break
            out.append(bytes(self.buf[4:4 + n]))
            del self.buf[:4 + n]
        return out


def _ts(epoch: float) -> dict:
    return {"seconds": int(epoch), "nanos": int(round((epoch % 1) * 1e9)) % 1_000_000_000}


@dataclass
class SapientFusionNode:
    schema: object                       # ProtoSchema cu definitiile oficiale
    adapter: object                      # UniversalAdapter (conversie spre tracker)
    node_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    strict: bool = False
    clock: object = time.time            # sursa de timp (injectabila in teste)
    rand: object = None                  # callable() -> 10 octeti, pentru ULID deterministe in teste
    name: str = "SANTINELA TCDE"

    def __post_init__(self):
        self.nodes: dict = {}            # node_id -> {"registered", "issues", "types"}
        self.tasks: dict = {}            # task_id -> {"dest", "status", "reasons"}
        self.items: list = []            # intrari pentru tracker (de preluat cu take_items)
        self.log: list = []
        self._object_ids: dict = {}      # track_id -> object_id ULID stabil

    # ---------- utilitare ----------
    def _ulid(self) -> str:
        return ulid(int(self.clock() * 1000), self.rand() if self.rand else None)

    def envelope(self, content_key: str, content: dict, destination: str | None = None) -> dict:
        msg = {"timestamp": _ts(self.clock()), "node_id": self.node_id, content_key: content}
        if destination:
            msg["destination_id"] = destination
        return msg

    def encode(self, msg: dict) -> bytes:
        return frame(self.schema.encode("SapientMessage", msg))

    def take_items(self) -> list:
        items, self.items = self.items, []
        return items

    # ---------- primire ----------
    def handle_bytes(self, payload: bytes) -> list[bytes]:
        msg = self.schema.decode("SapientMessage", payload)
        return [self.encode(r) for r in self.handle(msg, payload)]

    def handle(self, msg: dict, raw: bytes = b"") -> list[dict]:
        sender = msg.get("node_id", "?")
        responses = []
        if "registration" in msg:
            reg = msg["registration"]
            issues = self.schema.validate("SapientMessage", msg)
            accept = not (self.strict and issues)
            self.nodes[sender] = {"registered": accept, "issues": issues,
                                  "types": [d.get("node_type") for d in reg.get("node_definition", []) or []]}
            self.adapter._sapient([msg], None)
            ack = {"acceptance": accept}
            if not accept:
                ack["ack_response_reason"] = issues[:20]
            self.log.append(("registration", sender, accept, len(issues)))
            responses.append(self.envelope("registration_ack", ack, sender))
            return responses
        if sender not in self.nodes:
            self.log.append(("neinregistrat", sender))
            responses.append(self.envelope("error", {"packet": raw[:256],
                                                     "error_message": ["nod neinregistrat: trimiteti Registration"]},
                                           sender))
        if "detection_report" in msg or "status_report" in msg:
            self.items.extend(self.adapter._sapient([msg], None))
        if "alert" in msg:
            self.adapter._sapient([msg], None)
            alert_id = msg["alert"].get("alert_id")
            responses.append(self.envelope("alert_ack", {"alert_id": alert_id,
                                                         "alert_ack_status": "ALERT_ACK_STATUS_ACCEPTED"}, sender))
        if "task_ack" in msg:
            ta = msg["task_ack"]
            t = self.tasks.setdefault(ta.get("task_id"), {"dest": sender})
            t["status"] = ta.get("task_status")
            t["reasons"] = ta.get("reason", [])
            self.log.append(("task_ack", sender, ta.get("task_id"), t["status"]))
        if "error" in msg:
            self.log.append(("error", sender, msg["error"].get("error_message")))
        return responses

    # ---------- sarcini spre senzori ----------
    def task_look_at(self, dest: str, lat: float, lon: float, alt: float | None = None,
                     name: str = "look_at traseu") -> dict:
        """Indreapta un senzor orientabil (camera, radar de tragere) spre o pozitie."""
        loc = {"x": lon, "y": lat, "coordinate_system": "LOCATION_COORDINATE_SYSTEM_LAT_LNG_DEG_M",
               "datum": "LOCATION_DATUM_WGS84_E"}
        if alt is not None:
            loc["z"] = alt
        task_id = self._ulid()
        task = {"task_id": task_id, "task_name": name, "control": "CONTROL_START",
                "command": {"look_at": {"location_list": {"locations": [loc]}}}}
        self.tasks[task_id] = {"dest": dest, "status": "trimis", "reasons": []}
        return self.envelope("task", task, dest)

    # ---------- spre nodul superior ----------
    def registration(self, region: list | None = None) -> dict:
        """Inregistrarea SANTINELA ca nod de fuziune copil (campurile obligatorii completate)."""
        sec = {"units": "TIME_UNITS_SECONDS", "value": 1.0}
        loc_type = {"location_units": "LOCATION_COORDINATE_SYSTEM_LAT_LNG_DEG_M",
                    "location_datum": "LOCATION_DATUM_WGS84_E"}
        reg = {
            "node_definition": [{"node_type": "NODE_TYPE_FUSION_NODE", "node_sub_type": ["C-UAS", "observatori"]}],
            "icd_version": "BSI Flex 335 v2.0",
            "name": self.name, "short_name": "SANTINELA",
            "capabilities": [{"category": "Fusion", "type": "Tracking", "value": "IMM+GNN+SPRT"}],
            "status_definition": {"status_interval": {"units": "TIME_UNITS_SECONDS", "value": 10.0}},
            "mode_definition": [{
                "mode_name": "Default", "mode_type": "MODE_TYPE_PERMANENT", "settle_time": sec,
                "tracking_type": "TRACKING_TYPE_TRACK",
                "detection_definition": [{
                    "location_type": loc_type,
                    "detection_report": [{"category": "DETECTION_REPORT_CATEGORY_TRACK", "type": "covariance_xx",
                                          "units": "m^2"}],
                    "velocity_type": {"enu_velocity_units": {"east_north_rate_units": "SPEED_UNITS_MS"},
                                      "location_datum": "LOCATION_DATUM_WGS84_E"}}],
                "task": {"region_definition": {"region_type": ["REGION_TYPE_AREA_OF_INTEREST"],
                                               "region_area": [loc_type]}}}],
            "config_data": [{"manufacturer": "SANTINELA TCDE", "model": "nucleu de fuziune",
                             "software_version": "0.2"}],
        }
        if region:
            reg["reporting_region"] = region
        return self.envelope("registration", reg)

    def track_detection(self, track, frame, destination: str | None = None) -> dict:
        """Traseul SANTINELA ca DetectionReport (WGS-84, x = longitudine, covarianta in object_info)."""
        s = track.state
        lat, lon = frame.to_latlon(float(s.x[0]), float(s.x[1]))
        P = s.P[:2, :2]
        oid = self._object_ids.setdefault(track.track_id, self._ulid())
        m_per_deg_lat = 111320.0
        m_per_deg_lon = 111320.0 * math.cos(math.radians(lat))
        loc = {"x": lon, "y": lat, "x_error": math.sqrt(P[0, 0]) / m_per_deg_lon,
               "y_error": math.sqrt(P[1, 1]) / m_per_deg_lat,
               "coordinate_system": "LOCATION_COORDINATE_SYSTEM_LAT_LNG_DEG_M", "datum": "LOCATION_DATUM_WGS84_E"}
        if track.altitude_floor is not None:
            loc["z"] = float(track.altitude_floor)
        conf = 1.0 / (1.0 + math.exp(-track.score / 5.0))
        det = {
            "report_id": self._ulid(), "object_id": oid, "location": loc,
            "detection_confidence": round(conf, 3),
            "state": track.status,
            "object_info": [{"type": "covariance_xx", "value": f"{P[0, 0]:.2f}"},
                            {"type": "covariance_xy", "value": f"{P[0, 1]:.2f}"},
                            {"type": "covariance_yy", "value": f"{P[1, 1]:.2f}"},
                            {"type": "sources", "value": ",".join(sorted(track.sources))}],
            "classification": [{"type": "UAV", "confidence": round(conf, 3)}],
            "enu_velocity": {"east_rate": float(s.x[2]), "north_rate": float(s.x[3]),
                             "east_rate_error": float(math.sqrt(max(s.P[2, 2], 0.0))),
                             "north_rate_error": float(math.sqrt(max(s.P[3, 3], 0.0)))},
        }
        if track.cooperative_id:
            det["id"] = track.cooperative_id
        return self.envelope("detection_report", det, destination)


# ------------------------------------------------------------------ transport TCP
class SapientServer:
    """Server TCP pentru senzori SAPIENT; ruleaza intr-un fir propriu (``start``/``stop``)."""

    def __init__(self, node: SapientFusionNode, host: str = "0.0.0.0", port: int = 5020):
        self.node, self.host, self.port = node, host, port
        self._loop = asyncio.new_event_loop()
        self._server = None
        self._thread = None
        self._writers: dict = {}          # node_id -> StreamWriter
        self._lock = threading.Lock()

    async def _client(self, reader, writer):
        fr = FrameReader()
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                for payload in fr.feed(data):
                    with self._lock:
                        msg = self.node.schema.decode("SapientMessage", payload)
                        self._writers[msg.get("node_id")] = writer
                        responses = self.node.handle(msg, payload)
                    for r in responses:
                        writer.write(self.node.encode(r))
                    await writer.drain()
        except (ConnectionError, ValueError) as e:
            self.node.log.append(("conexiune", str(e)))
        finally:
            writer.close()

    def send(self, dest: str, msg: dict) -> bool:
        """Trimite un mesaj (ex. Task) unui nod conectat."""
        w = self._writers.get(dest)
        if w is None:
            return False
        data = self.node.encode(msg)
        self._loop.call_soon_threadsafe(w.write, data)
        return True

    def start(self) -> int:
        async def boot():
            self._server = await asyncio.start_server(self._client, self.host, self.port)
            return self._server.sockets[0].getsockname()[1]

        def run():
            asyncio.set_event_loop(self._loop)
            self._loop.run_forever()

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        self.port = asyncio.run_coroutine_threadsafe(boot(), self._loop).result(5)
        return self.port

    def stop(self) -> None:
        async def down():
            self._server.close()
            await self._server.wait_closed()
        asyncio.run_coroutine_threadsafe(down(), self._loop).result(5)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(5)


def send_upstream(host: str, port: int, messages: list[bytes], timeout: float = 5.0) -> list[bytes]:
    """Client simplu spre un nod superior: trimite mesaje incadrate si citeste raspunsurile disponibile."""
    import socket
    out = []
    with socket.create_connection((host, port), timeout=timeout) as s:
        for m in messages:
            s.sendall(m)
        s.settimeout(0.5)
        fr = FrameReader()
        try:
            while True:
                data = s.recv(65536)
                if not data:
                    break
                out.extend(fr.feed(data))
        except (TimeoutError, OSError):
            pass
    return out

