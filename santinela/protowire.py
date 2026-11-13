"""Decodor protobuf minimal, fara dependente: citeste fisiere .proto si decodeaza mesaje binare.

Folosit pentru SAPIENT (BSI Flex 335 v2, adoptat de NATO pentru C-UAS): definitiile
oficiale .proto se pun intr-un director, iar mesajele binare se decodeaza intr-un
dictionar cu numele campurilor din .proto (snake_case), aceeasi forma pe care o
intelege adaptorul pentru SAPIENT JSON.

Acopera proto2/proto3: mesaje si enumerari imbricate, oneof, map<>, repeated
(inclusiv packed), toate tipurile scalare, google.protobuf.Timestamp.
Nu acopera: grupuri (proto2, depreciate), extensii, Any.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

SCALARS = {"double", "float", "int32", "int64", "uint32", "uint64", "sint32", "sint64", "fixed32", "fixed64",
           "sfixed32", "sfixed64", "bool", "string", "bytes"}
VARINT = {"int32", "int64", "uint32", "uint64", "sint32", "sint64", "bool"}


class ProtoError(ValueError):
    pass


@dataclass
class FieldDef:
    name: str
    number: int
    type: str
    repeated: bool = False
    map_kv: tuple | None = None   # (tip cheie, tip valoare) pentru map<>
    mandatory: bool = False       # optiuni SAPIENT: (field_options) = {is_mandatory, is_uuid, is_ulid}
    uuid: bool = False
    ulid: bool = False
    oneof: str | None = None


@dataclass
class MessageDef:
    full: str
    fields: dict = field(default_factory=dict)  # numar -> FieldDef
    oneofs: dict = field(default_factory=dict)  # nume -> obligatoriu (bool)


@dataclass
class ProtoSchema:
    messages: dict = field(default_factory=dict)   # nume complet -> MessageDef
    enums: dict = field(default_factory=dict)      # nume complet -> {numar: nume}

    def __post_init__(self):
        if "google.protobuf.Timestamp" not in self.messages:
            self.messages["google.protobuf.Timestamp"] = MessageDef("google.protobuf.Timestamp", {
                1: FieldDef("seconds", 1, "int64"), 2: FieldDef("nanos", 2, "int32")})

    # ---------- incarcare ----------
    @classmethod
    def from_dir(cls, path: str | Path) -> "ProtoSchema":
        sch = cls()
        files = sorted(Path(path).rglob("*.proto"))
        if not files:
            raise ProtoError(f"niciun fisier .proto in {path}")
        for f in files:
            sch.add_source(f.read_text(encoding="utf-8"))
        sch.resolve()
        return sch

    @classmethod
    def from_text(cls, *sources: str) -> "ProtoSchema":
        sch = cls()
        for s in sources:
            sch.add_source(s)
        sch.resolve()
        return sch

    def add_source(self, text: str) -> None:
        text = re.sub(r"//[^\n]*", "", text)
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        toks = re.findall(r'"[^"]*"|[A-Za-z_][\w.]*|\d+|[{}\[\]=;<>,()-]', text)
        pkg = ""
        i = 0
        while i < len(toks):
            t = toks[i]
            if t == "package":
                pkg = toks[i + 1]
                i += 3
            elif t in ("syntax", "import", "option", "edition"):
                i = self._skip_statement(toks, i)
            elif t in ("message", "enum"):
                i = self._block(toks, i, pkg)
            elif t in ("service", "extend"):
                i = self._skip_block(toks, i + 2)
            else:
                i += 1

    @staticmethod
    def _skip_statement(toks, i) -> int:
        """Pana la ';' de la acelasi nivel (optiunile pot contine { ... })."""
        depth = 0
        while i < len(toks):
            if toks[i] in ("{", "["):
                depth += 1
            elif toks[i] in ("}", "]"):
                depth -= 1
            elif toks[i] == ";" and depth <= 0:
                return i + 1
            i += 1
        return i

    def _skip_block(self, toks, i) -> int:
        depth = 0
        while i < len(toks):
            if toks[i] == "{":
                depth += 1
            elif toks[i] == "}":
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        return i

    def _block(self, toks, i, scope) -> int:
        kind, name = toks[i], toks[i + 1]
        full = f"{scope}.{name}" if scope else name
        i += 2
        if toks[i] != "{":
            raise ProtoError(f"asteptam {{ dupa {kind} {name}")
        i += 1
        if kind == "enum":
            vals = {}
            while toks[i] != "}":
                if toks[i] in ("option", "reserved"):
                    i = self._skip_statement(toks, i)
                    continue
                if toks[i] == ";":
                    i += 1
                    continue
                vname = toks[i]
                neg = toks[i + 2] == "-"
                num = int(toks[i + 3] if neg else toks[i + 2])
                vals.setdefault(-num if neg else num, vname)
                i = self._skip_statement(toks, i)
            self.enums[full] = vals
            return i + 1
        msg = MessageDef(full)
        self.messages[full] = msg
        while toks[i] != "}":
            t = toks[i]
            if t in ("message", "enum"):
                i = self._block(toks, i, full)
            elif t == "oneof":
                oname = toks[i + 1]
                msg.oneofs[oname] = False
                i += 3  # oneof nume {
                while toks[i] != "}":
                    if toks[i] == "option":
                        j = self._skip_statement(toks, i)
                        if "is_mandatory" in toks[i:j] and toks[toks.index("is_mandatory", i) + 1] == "true":
                            msg.oneofs[oname] = True
                        i = j
                        continue
                    if toks[i] == ";":
                        i += 1
                        continue
                    i = self._field(toks, i, msg, full, oneof=oname)
                i += 1
            elif t == ";":
                i += 1
            elif t in ("option", "reserved", "extensions"):
                i = self._skip_statement(toks, i)
            elif t == "extend":
                i = self._skip_block(toks, i + 2)
            else:
                i = self._field(toks, i, msg, full)
        return i + 1

    def _field(self, toks, i, msg: MessageDef, scope, oneof: str | None = None) -> int:
        repeated = False
        if toks[i] in ("optional", "required"):
            i += 1
        elif toks[i] == "repeated":
            repeated = True
            i += 1
        if toks[i] == "map":
            kt, vt = toks[i + 2], toks[i + 4]
            name, num = toks[i + 6], int(toks[i + 8])
            fd = FieldDef(name, num, f"@{scope}", True, (kt, vt))
        else:
            typ, name, num = toks[i], toks[i + 1], int(toks[i + 3])
            fd = FieldDef(name, num, typ if typ in SCALARS else f"@{scope}|{typ}", repeated)
        fd.oneof = oneof
        end = self._skip_statement(toks, i)
        opts = toks[i:end]
        for k in range(len(opts) - 1):
            if opts[k + 1] == "true":
                if opts[k] == "is_mandatory":
                    fd.mandatory = True
                elif opts[k] == "is_uuid":
                    fd.uuid = True
                elif opts[k] == "is_ulid":
                    fd.ulid = True
        msg.fields[fd.number] = fd
        return end

    def resolve(self) -> None:
        """Rezolva numele de tipuri relative (ca in protoc: din domeniul cel mai interior spre exterior)."""
        for msg in self.messages.values():
            for fd in msg.fields.values():
                if fd.map_kv:
                    scope = fd.type[1:]
                    vt = fd.map_kv[1]
                    fd.map_kv = (fd.map_kv[0], vt if vt in SCALARS else self._lookup(scope, vt))
                    fd.type = "map"
                elif fd.type.startswith("@"):
                    scope, name = fd.type[1:].split("|", 1)
                    fd.type = self._lookup(scope, name)

    def _lookup(self, scope: str, name: str) -> str:
        if name.startswith("."):
            name = name[1:]
            if name in self.messages or name in self.enums:
                return name
        parts = scope.split(".")
        while True:
            cand = ".".join(parts + [name]) if parts else name
            if cand in self.messages or cand in self.enums:
                return cand
            if not parts:
                break
            parts.pop()
        tail = [k for k in list(self.messages) + list(self.enums) if k == name or k.endswith("." + name)]
        if len(tail) == 1:
            return tail[0]
        raise ProtoError(f"tip necunoscut {name} (in {scope})")

    def find(self, simple: str) -> str:
        hits = [k for k in self.messages if k == simple or k.endswith("." + simple)]
        if not hits:
            raise ProtoError(f"mesajul {simple} nu exista in schema")
        return sorted(hits, key=len)[0]

    # ---------- decodare ----------
    def decode(self, message: str, data: bytes) -> dict:
        full = message if message in self.messages else self.find(message)
        return self._decode(self.messages[full], memoryview(data))

    def _decode(self, msg: MessageDef, buf) -> dict:
        out: dict = {}
        i, n = 0, len(buf)
        while i < n:
            key, i = _varint(buf, i)
            num, wt = key >> 3, key & 7
            fd = msg.fields.get(num)
            if wt == 0:
                raw, i = _varint(buf, i)
                payload = None
            elif wt == 1:
                raw, payload = None, bytes(buf[i:i + 8])
                i += 8
            elif wt == 2:
                ln, i = _varint(buf, i)
                raw, payload = None, buf[i:i + ln]
                i += ln
            elif wt == 5:
                raw, payload = None, bytes(buf[i:i + 4])
                i += 4
            else:
                raise ProtoError(f"tip de transmisie nesuportat {wt}")
            if i > n:
                raise ProtoError("mesaj protobuf trunchiat")
            if fd is None:
                continue  # camp necunoscut: ignorat, ca in protobuf
            if fd.type == "map":
                entry = self._decode(MessageDef("map", {1: FieldDef("key", 1, fd.map_kv[0]),
                                                        2: FieldDef("value", 2, fd.map_kv[1])}), payload)
                out.setdefault(fd.name, {})[entry.get("key")] = entry.get("value")
                continue
            if wt == 2 and fd.type not in ("string", "bytes") and fd.type not in self.messages:
                vals = self._packed(fd.type, payload)  # repeated scalar/enum "packed"
                if fd.repeated:
                    out.setdefault(fd.name, []).extend(vals)
                elif vals:
                    out[fd.name] = vals[-1]
                continue
            val = self._value(fd.type, wt, raw, payload)
            if fd.repeated:
                out.setdefault(fd.name, []).append(val)
            else:
                out[fd.name] = val
        return out

    def _value(self, typ, wt, raw, payload):
        if typ in self.messages:
            return self._decode(self.messages[typ], payload)
        if typ in self.enums:
            return self.enums[typ].get(_to_signed(raw, 32), raw)
        if typ == "string":
            return bytes(payload).decode("utf-8", "replace")
        if typ == "bytes":
            return bytes(payload)
        if typ == "bool":
            return bool(raw)
        if typ in ("int32", "int64"):
            return _to_signed(raw, 64)
        if typ in ("uint32", "uint64"):
            return raw
        if typ in ("sint32", "sint64"):
            return (raw >> 1) ^ -(raw & 1)
        if typ == "double":
            return struct.unpack("<d", payload)[0]
        if typ == "float":
            return struct.unpack("<f", payload)[0]
        if typ == "fixed64":
            return struct.unpack("<Q", payload)[0]
        if typ == "sfixed64":
            return struct.unpack("<q", payload)[0]
        if typ == "fixed32":
            return struct.unpack("<I", payload)[0]
        if typ == "sfixed32":
            return struct.unpack("<i", payload)[0]
        raise ProtoError(f"tip {typ}")

    def encode(self, message: str, d: dict) -> bytes:
        """Mesaj binar din dictionar (nume de campuri ca in .proto; enumerari prin nume sau numar)."""
        full = message if message in self.messages else self.find(message)
        return _schema_encode(self, self.messages[full], d)

    def validate(self, message: str, d: dict) -> list[str]:
        """Abaterile de la optiunile SAPIENT: campuri obligatorii, oneof obligatorii, UUID, ULID."""
        full = message if message in self.messages else self.find(message)
        return _schema_validate(self, self.messages[full], d, full.rsplit(".", 1)[-1])

    def _packed(self, typ, payload) -> list:
        out, i = [], 0
        b = memoryview(payload)
        size = {"double": 8, "fixed64": 8, "sfixed64": 8, "float": 4, "fixed32": 4, "sfixed32": 4}.get(typ)
        while i < len(b):
            if size:
                out.append(self._value(typ, 1 if size == 8 else 5, None, bytes(b[i:i + size])))
                i += size
            else:
                raw, i = _varint(b, i)
                out.append(self._value(typ, 0, raw, None))
        return out


# ------------------------------------------------------------------ codare si validare dupa schema
def _zigzag(v: int) -> int:
    return (v << 1) ^ (v >> 63)


def _enc_scalar(sch: "ProtoSchema", typ: str, v) -> tuple[int, bytes]:
    """(tip de transmisie, octeti) pentru o valoare scalara sau enumerare."""
    if typ in sch.enums:
        if isinstance(v, str):
            inv = {name: num for num, name in sch.enums[typ].items()}
            if v not in inv:
                raise ProtoError(f"valoare de enumerare necunoscuta {v} pentru {typ}")
            v = inv[v]
        return 0, encode_varint(int(v))
    if typ == "bool":
        return 0, encode_varint(1 if v else 0)
    if typ in ("int32", "int64", "uint32", "uint64"):
        return 0, encode_varint(int(v))
    if typ in ("sint32", "sint64"):
        return 0, encode_varint(_zigzag(int(v)))
    if typ == "double":
        return 1, struct.pack("<d", float(v))
    if typ == "float":
        return 5, struct.pack("<f", float(v))
    if typ == "fixed64":
        return 1, struct.pack("<Q", int(v))
    if typ == "sfixed64":
        return 1, struct.pack("<q", int(v))
    if typ == "fixed32":
        return 5, struct.pack("<I", int(v))
    if typ == "sfixed32":
        return 5, struct.pack("<i", int(v))
    if typ in ("string", "bytes"):
        b = v.encode("utf-8") if isinstance(v, str) else bytes(v)
        return 2, encode_varint(len(b)) + b
    raise ProtoError(f"tip {typ}")


def _schema_encode(sch: "ProtoSchema", msg: MessageDef, d: dict) -> bytes:
    by_name = {f.name: f for f in msg.fields.values()}
    out = bytearray()
    for key in d:
        if key not in by_name:
            raise ProtoError(f"{msg.full}: camp necunoscut {key}")
    for fd in sorted(msg.fields.values(), key=lambda f: f.number):
        if fd.name not in d or d[fd.name] is None:
            continue
        v = d[fd.name]
        if fd.type == "map":
            for k, val in v.items():
                entry = MessageDef("map", {1: FieldDef("key", 1, fd.map_kv[0]), 2: FieldDef("value", 2, fd.map_kv[1])})
                b = _schema_encode(sch, entry, {"key": k, "value": val})
                out += encode_varint(fd.number << 3 | 2) + encode_varint(len(b)) + b
            continue
        vals = v if fd.repeated else [v]
        if fd.type in sch.messages:
            for item in vals:
                b = _schema_encode(sch, sch.messages[fd.type], item)
                out += encode_varint(fd.number << 3 | 2) + encode_varint(len(b)) + b
        elif fd.repeated and fd.type not in ("string", "bytes"):
            body = b"".join(_enc_scalar(sch, fd.type, x)[1] for x in vals)  # "packed" (implicit in proto3)
            out += encode_varint(fd.number << 3 | 2) + encode_varint(len(body)) + body
        else:
            for item in vals:
                wt, b = _enc_scalar(sch, fd.type, item)
                out += encode_varint(fd.number << 3 | wt) + b
    return bytes(out)


_ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _schema_validate(sch: "ProtoSchema", msg: MessageDef, d: dict, path: str) -> list[str]:
    issues = []
    for fd in msg.fields.values():
        p = f"{path}.{fd.name}"
        present = fd.name in d and d[fd.name] not in (None, [], "")
        if fd.mandatory and not present and fd.oneof is None:
            issues.append(f"{p}: camp obligatoriu lipsa")
        if not present:
            continue
        vals = d[fd.name] if fd.repeated and isinstance(d[fd.name], list) else [d[fd.name]]
        for k, v in enumerate(vals):
            pk = f"{p}[{k}]" if fd.repeated else p
            if fd.ulid and not (isinstance(v, str) and _ULID.match(v)):
                issues.append(f"{pk}: nu e ULID valid ({v!r})")
            if fd.uuid and not (isinstance(v, str) and _UUID.match(v)):
                issues.append(f"{pk}: nu e UUID valid ({v!r})")
            if fd.type in sch.messages and isinstance(v, dict):
                issues += _schema_validate(sch, sch.messages[fd.type], v, pk)
    for oname, mandatory in msg.oneofs.items():
        if mandatory and not any(f.oneof == oname and f.name in d for f in msg.fields.values()):
            issues.append(f"{path}: niciun camp din oneof obligatoriu '{oname}'")
    return issues


def _varint(buf, i: int) -> tuple[int, int]:
    shift = result = 0
    while True:
        if i >= len(buf):
            raise ProtoError("varint trunchiat")
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7
        if shift > 70:
            raise ProtoError("varint prea lung")


def _to_signed(v: int, bits: int) -> int:
    v &= (1 << 64) - 1
    return v - (1 << 64) if v >= 1 << 63 else v


# ------------------------------------------------------------------ codare (teste/simulare)
def encode_varint(v: int) -> bytes:
    if v < 0:
        v += 1 << 64
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def encode_field(num: int, value, typ: str = "auto") -> bytes:
    """Codare simpla pentru teste: int (varint), float (double), str/bytes/dict-codat (len)."""
    if typ == "double" or (typ == "auto" and isinstance(value, float)):
        return encode_varint(num << 3 | 1) + struct.pack("<d", value)
    if typ == "float":
        return encode_varint(num << 3 | 5) + struct.pack("<f", value)
    if typ == "sint":
        return encode_varint(num << 3) + encode_varint((value << 1) ^ (value >> 63))
    if isinstance(value, bool) or isinstance(value, int):
        return encode_varint(num << 3) + encode_varint(int(value))
    if isinstance(value, str):
        value = value.encode()
    return encode_varint(num << 3 | 2) + encode_varint(len(value)) + bytes(value)
