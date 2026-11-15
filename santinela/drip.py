"""Autentificarea Remote ID: mesajul Authentication (ASTM F3411) si DRIP (RFC 9575 / RFC 9374).

Fara dependente: Keccak-f[1600] si cSHAKE128 (FIPS 202, NIST SP 800-185) si verificarea
Ed25519 (RFC 8032) sunt implementate aici. Verificate pe:

* exemplele NIST pentru cSHAKE128 (Sample #1 si #2) si pe SHAKE128 din hashlib (permutarea);
* vectorii de test Ed25519 din RFC 8032 sectiunea 7.1;
* exemplul complet din RFC 9575 anexa B.2.1: DET-ul si cheia dronei, cele 8 mesaje ASTM,
  DRIP Link, Wrapper si Manifest cu FEC (semnaturi, hash-urile mesajelor, legatura DET-cheie).

Ce face:

* reasambleaza paginile mesajului Authentication (pagina 0: LastPageIndex, Length, Timestamp;
  17 + 23 x n octeti) si reface o pagina pierduta din paritatea XOR (RFC 9575 sectiunea 5);
* DRIP Link (aviz de la o autoritate parinte: DET + cheia copilului), Wrapper (pana la 4 mesaje
  ASTM semnate de drona) si Manifest (hash-uri de 8 octeti ale mesajelor trimise), Frame;
* verifica legatura DET <-> cheie (hash cSHAKE128 din DET, suita 5 = EdDSA/cSHAKE128);
* starea pe emitator dupa anexa A din RFC 9575 (Partial, Unsupported, Unverifiable, Verified,
  Trusted, Unverified, Questionable, Conflicting).

Atentie: o semnatura valida arata doar ca emitatorul are cheia. Ca drona observata e chiar
emitatorul se stabileste prin corelarea pozitiei semnate cu senzorii (RFC 9575 sectiunea 6.4.2):
``UniversalAdapter.annotate_tracks`` face asta. Autentificarile ASTM de tip 1-4 depind de un
serviciu extern si sunt raportate ca nesuportate.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

# ================================================================== Keccak / cSHAKE128
_MASK = (1 << 64) - 1


def _round_constants() -> list[int]:
    """FIPS 202, algoritmii 5 si 6: constantele de runda din LFSR-ul rc(t)."""
    def rc(t: int) -> int:
        if t % 255 == 0:
            return 1
        r = 1
        for _ in range(t % 255):
            r <<= 1
            if r & 0x100:
                r ^= 0x171
        return r & 1
    out = []
    for ir in range(24):
        c = 0
        for j in range(7):
            if rc(j + 7 * ir):
                c |= 1 << ((1 << j) - 1)
        out.append(c)
    return out


def _rotations() -> list[list[int]]:
    """FIPS 202, pasul rho: decalajele (t+1)(t+2)/2 pe drumul (x, y) -> (y, 2x + 3y)."""
    r = [[0] * 5 for _ in range(5)]
    x, y = 1, 0
    for t in range(24):
        r[x][y] = ((t + 1) * (t + 2) // 2) % 64
        x, y = y, (2 * x + 3 * y) % 5
    return r


_RC = _round_constants()
_ROT = _rotations()


def _rol(v: int, n: int) -> int:
    return ((v << n) | (v >> (64 - n))) & _MASK if n else v


def _keccak_f(a: list[int]) -> None:
    """Permutarea Keccak-f[1600] pe 25 de benzi de 64 de biti (a[x + 5y])."""
    for rc in _RC:
        c = [a[x] ^ a[x + 5] ^ a[x + 10] ^ a[x + 15] ^ a[x + 20] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        a[:] = [a[i] ^ d[i % 5] for i in range(25)]
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                b[y + 5 * ((2 * x + 3 * y) % 5)] = _rol(a[x + 5 * y], _ROT[x][y])
        a[:] = [b[i] ^ ((~b[(i % 5 + 1) % 5 + 5 * (i // 5)]) & b[(i % 5 + 2) % 5 + 5 * (i // 5)]) for i in range(25)]
        a[0] ^= rc


def _sponge(rate: int, data: bytes, suffix: int, out_len: int) -> bytes:
    a = [0] * 25
    buf = bytearray(data)
    buf.append(suffix)
    buf.extend(b"\x00" * ((-len(buf)) % rate))
    buf[-1] |= 0x80
    for off in range(0, len(buf), rate):
        block = buf[off:off + rate]
        for i in range(rate // 8):
            a[i] ^= int.from_bytes(block[8 * i:8 * i + 8], "little")
        _keccak_f(a)
    out = bytearray()
    while True:
        out += b"".join(a[i].to_bytes(8, "little") for i in range(rate // 8))
        if len(out) >= out_len:
            return bytes(out[:out_len])
        _keccak_f(a)


def shake128(data: bytes, n: int) -> bytes:
    """SHAKE128 (pentru verificarea permutarii fata de hashlib)."""
    return _sponge(168, data, 0x1F, n)


def _left_encode(x: int) -> bytes:
    n = max(1, (x.bit_length() + 7) // 8)
    return bytes([n]) + x.to_bytes(n, "big")


def _encode_string(s: bytes) -> bytes:
    return _left_encode(8 * len(s)) + s


def _bytepad(x: bytes, w: int) -> bytes:
    z = _left_encode(w) + x
    return z + b"\x00" * ((-len(z)) % w)


def cshake128(data: bytes, nbits: int, n: bytes = b"", s: bytes = b"") -> bytes:
    """cSHAKE128(X, L, N, S) dupa NIST SP 800-185."""
    if not n and not s:
        return shake128(data, nbits // 8)
    return _sponge(168, _bytepad(_encode_string(n) + _encode_string(s), 168) + data, 0x04, nbits // 8)


# ================================================================== Ed25519 (RFC 8032)
_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _P:
        return None
    u, v = (y * y - 1) % _P, (_D * y * y + 1) % _P
    x = u * pow(v, 3, _P) * pow(u * pow(v, 7, _P), (_P - 5) // 8, _P) % _P
    vx2 = v * x * x % _P
    if vx2 == (-u) % _P:
        x = x * _SQRT_M1 % _P
    elif vx2 != u:
        return None
    if x == 0 and sign:
        return None
    if x & 1 != sign:
        x = _P - x
    return x


_BY = 4 * pow(5, _P - 2, _P) % _P
_BX = _recover_x(_BY, 0)
_B = (_BX, _BY, 1, _BX * _BY % _P)
_ZERO = (0, 1, 1, 0)


def _add(p, q):
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = 2 * p[3] * q[3] * _D % _P
    d = 2 * p[2] * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(k: int, p):
    q = _ZERO
    while k:
        if k & 1:
            q = _add(q, p)
        p = _add(p, p)
        k >>= 1
    return q


def _equal(p, q) -> bool:
    return (p[0] * q[2] - q[0] * p[2]) % _P == 0 and (p[1] * q[2] - q[1] * p[2]) % _P == 0


def _decode_point(b: bytes):
    if len(b) != 32:
        return None
    y = int.from_bytes(b, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _encode_point(p) -> bytes:
    zi = pow(p[2], _P - 2, _P)
    x, y = p[0] * zi % _P, p[1] * zi % _P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def ed25519_verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """Verificare Ed25519 (RFC 8032 sectiunea 5.1.7, cu ecuatia cu cofactor)."""
    if len(signature) != 64:
        return False
    a = _decode_point(bytes(public_key))
    r = _decode_point(signature[:32])
    s = int.from_bytes(signature[32:], "little")
    if a is None or r is None or s >= _L:
        return False
    k = int.from_bytes(hashlib.sha512(signature[:32] + bytes(public_key) + message).digest(), "little") % _L
    lhs = _mul(8 * s, _B)
    rhs = _add(_mul(8, r), _mul(8 * k, a))
    return _equal(lhs, rhs)


def _secret_scalar(secret: bytes) -> tuple[int, bytes]:
    h = hashlib.sha512(secret).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def ed25519_public_key(secret: bytes) -> bytes:
    """Cheia publica dintr-o cheie secreta de 32 de octeti (pentru teste si simulare)."""
    return _encode_point(_mul(_secret_scalar(secret)[0], _B))


def ed25519_sign(secret: bytes, message: bytes) -> bytes:
    """Semnare Ed25519 (RFC 8032 sectiunea 5.1.6) - doar pentru teste si simulare."""
    a, prefix = _secret_scalar(secret)
    pub = _encode_point(_mul(a, _B))
    r = int.from_bytes(hashlib.sha512(prefix + message).digest(), "little") % _L
    rb = _encode_point(_mul(r, _B))
    k = int.from_bytes(hashlib.sha512(rb + pub + message).digest(), "little") % _L
    return rb + ((r + k * a) % _L).to_bytes(32, "little")


# ================================================================== DET (RFC 9374)
DET_PREFIX = 0x2001003                      # 2001:30::/28
CONTEXT_ID = bytes.fromhex("00B5A69C795DF5D5F0087F56843F2C40")
SUITE_EDDSA_CSHAKE128 = 5


@dataclass(frozen=True)
class Det:
    raw: bytes
    raa: int
    hda: int
    suite: int
    hash: bytes

    @property
    def is_det(self) -> bool:
        return int.from_bytes(self.raw[:4], "big") >> 4 == DET_PREFIX

    def __str__(self) -> str:
        h = self.raw.hex()
        return ":".join(h[i:i + 4] for i in range(0, 32, 4))


def parse_det(raw: bytes) -> Det:
    v = int.from_bytes(raw, "big")
    hid = (v >> 72) & ((1 << 28) - 1)
    return Det(bytes(raw), hid >> 14, hid & 0x3FFF, (v >> 64) & 0xFF, raw[8:])


def det_hash(det_upper: bytes, public_key: bytes) -> bytes:
    """Hash-ul ORCHID: cSHAKE128(Prefix | HID | Suite | HOST_ID, 64, "", Context ID).

    HOST_ID = cheia publica Ed25519 de 32 de octeti, fara eticheta curbei: varianta care
    reproduce DET-ul din exemplul RFC 9575 B.2.1 (cu eticheta, hash-ul nu coincide).
    """
    return cshake128(bytes(det_upper[:8]) + bytes(public_key), 64, b"", CONTEXT_ID)


def det_matches_key(det: bytes, public_key: bytes) -> bool:
    d = parse_det(det)
    return d.is_det and d.suite == SUITE_EDDSA_CSHAKE128 and det_hash(det, public_key) == d.hash


def make_det(raa: int, hda: int, public_key: bytes) -> bytes:
    """DET pentru o cheie (pentru teste si simulare)."""
    upper = ((DET_PREFIX << 36) | (raa << 22) | (hda << 8) | SUITE_EDDSA_CSHAKE128).to_bytes(8, "big")
    return upper + det_hash(upper, public_key)


# ================================================================== mesajul Authentication (ASTM F3411)
F3411_EPOCH = 1546300800                    # 2019-01-01 00:00:00 UTC
PAGE0_DATA, PAGE_DATA, MAX_PAGES = 17, 23, 16
AUTH_TYPES = {0: "fara", 1: "semnatura UAS ID", 2: "semnatura Operator ID", 3: "semnatura set de mesaje",
              4: "Network Remote ID", 5: "metoda specifica (SAM)"}
SAM_LINK, SAM_WRAPPER, SAM_MANIFEST, SAM_FRAME = 1, 2, 3, 4
SAM_NAMES = {SAM_LINK: "DRIP Link", SAM_WRAPPER: "DRIP Wrapper", SAM_MANIFEST: "DRIP Manifest",
             SAM_FRAME: "DRIP Frame"}


@dataclass
class AuthMessage:
    auth_type: int
    timestamp: int                 # secunde de la 2019-01-01 (F3411)
    data: bytes                    # campul Authentication Data / Signature (Length octeti)
    pages: list                    # mesajele de 25 de octeti, in ordinea paginilor (pentru Manifest)
    fec: bool = False
    recovered_page: int | None = None


def _payloads_xor(payloads) -> bytes:
    out = bytearray(PAGE_DATA)
    for p in payloads:
        for i, b in enumerate(p):
            out[i] ^= b
    return bytes(out)


def _needed_pages(length: int) -> int:
    """Indexul ultimei pagini care contine date de autentificare."""
    return 0 if length <= PAGE0_DATA else (length - PAGE0_DATA + PAGE_DATA - 1) // PAGE_DATA


def _assemble(pages: dict) -> AuthMessage | None:
    p0 = pages[0]
    lpi, length = p0[2], p0[3]
    if lpi >= MAX_PAGES or length > PAGE0_DATA + lpi * PAGE_DATA:
        return None
    if any(k not in pages for k in range(lpi + 1)):
        return None
    payload = b"".join(pages[k][2:] for k in range(lpi + 1))
    data = payload[6:6 + length]
    fec = lpi > _needed_pages(length)
    return AuthMessage(p0[1] >> 4, int.from_bytes(p0[4:8], "little"), data,
                       [pages[k] for k in range(lpi + 1)], fec)


def _decode_check(page0: bytes, last_seen: int) -> bool:
    """RFC 9575 figura 12: pagina 0 refacuta din paritate trebuie sa fie coerenta."""
    lpi, length = page0[2], page0[3]
    if lpi >= MAX_PAGES or length > 201 or lpi != last_seen:
        return False
    return lpi > _needed_pages(length)


class PageAssembler:
    """Reasambleaza mesajele Authentication pe emitator (pagini 0..LastPageIndex, cu FEC XOR).

    Emitatorul se identifica prin ``sender`` (adresa MAC Bluetooth/Wi-Fi data ca sursa).
    Un mesaj nou incepe la o pagina deja primita cu alt continut. O pagina de date pierduta se
    reface din paritate cand sosete ultima pagina (paritatea); un mesaj caruia ii lipseste doar
    paritatea se incheie cand incepe mesajul urmator.
    """

    def __init__(self):
        self.pending: dict = {}

    @staticmethod
    def _try(cur: dict, final: bool = False) -> AuthMessage | None:
        if 0 in cur:
            done = _assemble(cur)
            if done is not None:
                return done
            p0 = cur[0]
            lpi = p0[2]
            missing = [k for k in range(lpi + 1) if k not in cur]
            fec = lpi > _needed_pages(p0[3])
            if len(missing) != 1 or not fec or (lpi not in cur and not final):
                return None
            k = missing[0]
            rec = p0[:1] + bytes([(p0[1] & 0xF0) | k]) + _payloads_xor(cur[j][2:] for j in cur)
            done = _assemble({**cur, k: rec})
            if done is not None:
                done.recovered_page = k
            return done
        # pagina 0 lipsa: refacuta din paritate daca paginile 1..ultima au sosit si e coerenta
        last = max(cur)
        if last >= 2 and all(k in cur for k in range(1, last + 1)):
            any_page = cur[last]
            rec = any_page[:1] + bytes([any_page[1] & 0xF0]) + _payloads_xor(cur[j][2:] for j in cur)
            if _decode_check(rec, last):
                done = _assemble({**cur, 0: rec})
                if done is not None:
                    done.recovered_page = 0
                return done
        return None

    def add(self, sender, msg: bytes) -> list[AuthMessage]:
        msg = bytes(msg)
        if len(msg) != 25 or msg[0] >> 4 != 2:
            return []
        out = []
        page = msg[1] & 0x0F
        cur = self.pending.setdefault(sender, {})
        if page in cur and cur[page] != msg:
            old = self._try(cur, final=True)      # mesajul anterior, poate fara pagina de paritate
            if old is not None:
                out.append(old)
            cur.clear()
        cur[page] = msg
        done = self._try(cur)
        if done is not None:
            out.append(done)
            self.pending.pop(sender, None)
        return out

    def partial(self, sender) -> bool:
        return bool(self.pending.get(sender))


def encode_auth_pages(auth_type: int, data: bytes, timestamp: int, fec: bool = False,
                      proto: int = 2) -> list[bytes]:
    """Paginile unui mesaj Authentication (pentru teste si simulare), cu FEC optional."""
    body = bytes([0, len(data)]) + int(timestamp).to_bytes(4, "little") + data
    if fec:
        pad = (-(len(body) + 1)) % PAGE_DATA
        body += bytes([pad + PAGE_DATA]) + b"\x00" * pad
    body += b"\x00" * ((-len(body)) % PAGE_DATA)
    chunks = [body[i:i + PAGE_DATA] for i in range(0, len(body), PAGE_DATA)]
    if fec:
        chunks.append(b"")                                        # loc pentru paritate
    lpi = len(chunks) - 1
    chunks[0] = bytes([lpi]) + chunks[0][1:]
    if fec:
        chunks[-1] = _payloads_xor(chunks[:-1])
    head = (2 << 4) | proto
    return [bytes([head, (auth_type << 4) | k]) + c for k, c in enumerate(chunks)]


# ================================================================== formatele DRIP (RFC 9575)
@dataclass
class DripLink:
    vnb: int
    vna: int
    child_det: bytes
    child_key: bytes
    parent_det: bytes
    signature: bytes
    signed: bytes


@dataclass
class DripEvidence:
    sam: int
    vnb: int
    vna: int
    evidence: bytes
    ua_det: bytes
    signature: bytes

    @property
    def signed(self) -> bytes:
        return (self.vnb.to_bytes(4, "little") + self.vna.to_bytes(4, "little") + self.evidence
                + self.ua_det)


class DripError(ValueError):
    pass


def parse_drip(auth: AuthMessage):
    if auth.auth_type != 5 or not auth.data:
        raise DripError("nu e SAM (tipul de autentificare 5)")
    d = auth.data
    sam = d[0]
    if sam == SAM_FRAME and len(d) == 137 and det_matches_key(d[9:25], d[25:57]):
        # exemplul RFC 9575 B.2.1 trimite Link-ul cu SAM 0x04 (numerotarea dintr-o versiune
        # de lucru); structura e recunoscuta sigur: DET-ul copilului e hash-ul cheiei lui
        sam = SAM_LINK
    if sam == SAM_LINK:
        if len(d) != 137:
            raise DripError(f"DRIP Link: {len(d)} octeti in loc de 137")
        return DripLink(int.from_bytes(d[1:5], "little"), int.from_bytes(d[5:9], "little"), d[9:25], d[25:57],
                        d[57:73], d[73:137], d[1:73])
    if sam in (SAM_WRAPPER, SAM_MANIFEST, SAM_FRAME):
        if len(d) < 89:
            raise DripError("structura semnata de drona prea scurta")
        ev = DripEvidence(sam, int.from_bytes(d[1:5], "little"), int.from_bytes(d[5:9], "little"), d[9:-80],
                          d[-80:-64], d[-64:])
        if sam == SAM_WRAPPER and (len(ev.evidence) % 25 or len(ev.evidence) > 100):
            raise DripError("DRIP Wrapper: dovada nu e un multiplu de 25 de octeti (max. 4 mesaje)")
        if sam == SAM_MANIFEST and (len(ev.evidence) % 8 or len(ev.evidence) < 24):
            raise DripError("DRIP Manifest: dovada nu e un multiplu de 8 octeti (min. 3 hash-uri)")
        return ev
    raise DripError(f"tip SAM nesuportat: {sam}")


def message_hash(msg: bytes) -> bytes:
    """Hash-ul de 8 octeti al unui mesaj ASTM (fara contorul de mesaj), pentru Manifest."""
    return cshake128(bytes(msg), 64, b"", b"Remote ID Auth Hash")


def auth_message_hash(auth: AuthMessage) -> bytes:
    """Mesajele paginate se concateneaza in ordinea paginilor si se hash-uiesc ca un obiect."""
    return message_hash(b"".join(auth.pages))


def link_hashes(auth: AuthMessage) -> set[bytes]:
    """Hash-urile acceptate pentru un DRIP Link in Manifest: peste pagini (RFC 9575 sectiunea
    4.4.3.1) si peste avizul semnat, fara octetul SAM (cum e calculat in exemplul B.2.1)."""
    return {auth_message_hash(auth), message_hash(auth.data[1:])}


# ================================================================== starea de autentificare
STATES = ("None", "Partial", "Unsupported", "Unverifiable", "Verified", "Trusted", "Unverified",
          "Questionable", "Conflicting")
STATE_RO = {"None": "fara autentificare", "Partial": "pagini lipsa", "Unsupported": "tip nesuportat",
            "Unverifiable": "neverificabil (lipseste cheia)", "Verified": "semnatura valida",
            "Trusted": "semnatura valida, cheie de incredere", "Unverified": "semnatura sau continut invalid",
            "Questionable": "rezultate amestecate (valid si invalid)",
            "Conflicting": "conflict: cheie de incredere si mesaje invalide"}


@dataclass
class KeyInfo:
    public_key: bytes
    trusted: bool
    via: str


@dataclass
class SenderState:
    ok: int = 0
    ok_trusted: int = 0
    bad: int = 0
    unverifiable: int = 0
    unsupported: int = 0
    det: bytes | None = None
    last: dict = field(default_factory=dict)
    signed_positions: list = field(default_factory=list)   # mesaje Location semnate (Wrapper)
    authenticated_hashes: set = field(default_factory=set)
    link_hashes: set = field(default_factory=set)

    def state(self) -> str:
        if self.ok_trusted and self.bad:
            return "Conflicting"
        if self.ok and self.bad:
            return "Questionable"
        if self.bad:
            return "Unverified"
        if self.ok_trusted:
            return "Trusted"
        if self.ok:
            return "Verified"
        if self.unverifiable:
            return "Unverifiable"
        if self.unsupported:
            return "Unsupported"
        return "None"


class DripVerifier:
    """Verifica autentificarile DRIP pe emitator, cu un depozit de chei (DET -> cheie publica).

    ``trust_anchor(det, cheie)`` adauga o autoritate de incredere (ex. RAA/HDA nationala); cheile
    copiilor avizati de ea prin DRIP Link devin si ele de incredere. Un Link de la un parinte
    necunoscut da cheia copilului (legata criptografic de DET prin hash), dar nu si increderea.
    ``epoch`` e epoca marcajelor VNB/VNA (F3411: 2019-01-01); fereastra se verifica doar daca
    se da ora curenta (``now_unix``).
    """

    def __init__(self, epoch: int = F3411_EPOCH, clock_slack: float = 5.0):
        self.keys: dict[bytes, KeyInfo] = {}
        self.epoch = epoch
        self.clock_slack = clock_slack
        self.assembler = PageAssembler()
        self.senders: dict = {}
        self.seen_hashes: dict = {}          # sender -> {hash: mesaj}
        self.log: list = []

    def trust_anchor(self, det: bytes, public_key: bytes, name: str = "configurare") -> None:
        self.keys[bytes(det)] = KeyInfo(bytes(public_key), True, name)

    def _sender(self, sender) -> SenderState:
        return self.senders.setdefault(sender, SenderState())

    def _window_ok(self, vnb: int, vna: int, now_unix) -> bool | None:
        if now_unix is None:
            return None
        return self.epoch + vnb - self.clock_slack <= now_unix <= self.epoch + vna + self.clock_slack

    def observe(self, sender, msg: bytes) -> None:
        """Un mesaj ASTM primit in clar (fara contor): retinut pentru Manifest si Wrapper."""
        self.seen_hashes.setdefault(sender, {})[message_hash(msg)] = bytes(msg)

    def feed_page(self, sender, msg: bytes, now_unix=None, pack_messages=None) -> list[dict]:
        """O pagina Authentication; intoarce rezultatele mesajelor complete (de obicei 0 sau 1)."""
        return [self.process(sender, auth, now_unix, pack_messages) for auth in self.assembler.add(sender, msg)]

    def process(self, sender, auth: AuthMessage, now_unix=None, pack_messages=None) -> dict:
        s = self._sender(sender)
        res = {"tip": AUTH_TYPES.get(auth.auth_type, f"privat/rezervat {auth.auth_type}"),
               "fec": auth.fec, "pagina_refacuta": auth.recovered_page}
        if auth.auth_type != 5:
            s.unsupported += 1
            res["rezultat"] = "Unsupported"
            res["motiv"] = "autentificarea ASTM de tip 1-4 cere serviciul extern (validator)"
            return self._done(sender, s, res)
        try:
            drip = parse_drip(auth)
        except DripError as e:
            unsupported = "nesuportat" in str(e)
            if unsupported:
                s.unsupported += 1
            else:
                s.bad += 1
            res.update(rezultat="Unsupported" if unsupported else "Unverified", motiv=str(e))
            return self._done(sender, s, res)
        res["format"] = "DRIP Link" if isinstance(drip, DripLink) else SAM_NAMES[drip.sam]
        if isinstance(drip, DripLink) and auth.data[0] != SAM_LINK:
            res["nota"] = f"Link trimis cu SAM {auth.data[0]:#04x} (ca in exemplul RFC 9575 B.2.1), nu 0x01"
        if isinstance(drip, DripLink):
            return self._done(sender, s, self._link(drip, s, res, now_unix, auth))
        return self._done(sender, s, self._evidence(sender, drip, s, res, now_unix, pack_messages))

    def _done(self, sender, s: SenderState, res: dict) -> dict:
        res["stare_emitator"] = s.state()
        s.last = res
        self.log.append({"emitator": sender, **res})
        return res

    def _link(self, link: DripLink, s: SenderState, res: dict, now_unix, auth) -> dict:
        res["det_copil"] = str(parse_det(link.child_det))
        res["det_parinte"] = str(parse_det(link.parent_det))
        if not det_matches_key(link.child_det, link.child_key):
            s.bad += 1
            res.update(rezultat="Unverified", motiv="cheia copilului nu corespunde DET-ului (hash cSHAKE128)")
            return res
        s.link_hashes |= link_hashes(auth)
        parent = self.keys.get(bytes(link.parent_det))
        if parent is None:
            # cheia e legata criptografic de DET, dar avizul parintelui nu se poate verifica
            self.keys.setdefault(bytes(link.child_det), KeyInfo(link.child_key, False, "DRIP Link neverificat"))
            s.unverifiable += 1
            res.update(rezultat="Unverifiable", motiv="cheia parintelui (DIME) nu e cunoscuta")
            return res
        if not ed25519_verify(parent.public_key, link.signed, link.signature):
            s.bad += 1
            res.update(rezultat="Unverified", motiv="semnatura parintelui invalida")
            return res
        if self._window_ok(link.vnb, link.vna, now_unix) is False:
            s.bad += 1
            res.update(rezultat="Unverified", motiv="avizul nu e in fereastra VNB-VNA")
            return res
        self.keys[bytes(link.child_det)] = KeyInfo(link.child_key, parent.trusted, f"aviz {res['det_parinte']}")
        if parent.trusted:
            s.ok_trusted += 1
        s.ok += 1
        res["rezultat"] = "Trusted" if parent.trusted else "Verified"
        return res

    def _evidence(self, sender, ev: DripEvidence, s: SenderState, res: dict, now_unix, pack_messages) -> dict:
        res["det_drona"] = str(parse_det(ev.ua_det))
        s.det = bytes(ev.ua_det)
        key = self.keys.get(bytes(ev.ua_det))
        if key is None:
            s.unverifiable += 1
            res.update(rezultat="Unverifiable", motiv="cheia dronei nu e cunoscuta (lipseste DRIP Link)")
            return res
        evidence = ev.evidence
        if ev.sam == SAM_WRAPPER and not evidence and pack_messages:
            # Wrapper pe transport extins: dovada = mesajele din acelasi pachet, in ordinea tipului
            evidence = b"".join(sorted((m for m in pack_messages if m[0] >> 4 != 2), key=lambda m: m[0] >> 4))
            ev = DripEvidence(ev.sam, ev.vnb, ev.vna, evidence, ev.ua_det, ev.signature)
        if not ed25519_verify(key.public_key, ev.signed, ev.signature):
            s.bad += 1
            res.update(rezultat="Unverified", motiv="semnatura dronei invalida")
            return res
        if self._window_ok(ev.vnb, ev.vna, now_unix) is False:
            s.bad += 1
            res.update(rezultat="Unverified", motiv="semnatura nu e in fereastra VNB-VNA (posibila reluare)")
            return res
        seen = self.seen_hashes.get(sender, {})
        if ev.sam == SAM_WRAPPER:
            msgs = [evidence[i:i + 25] for i in range(0, len(evidence), 25)]
            res["mesaje_semnate"] = len(msgs)
            res["mesaje_confirmate_in_clar"] = sum(message_hash(m) in seen for m in msgs)
            s.signed_positions += [m for m in msgs if m[0] >> 4 == 1]
        elif ev.sam == SAM_MANIFEST:
            hashes = [evidence[i:i + 8] for i in range(0, len(evidence), 8)]
            prev, cur, link_h, msg_h = hashes[0], hashes[1], hashes[2], hashes[3:]
            # textul (sectiunea 4.4.3, pasul 4) omite hash-ul Link-ului; exemplul B.2.1 il include
            if message_hash(prev + b"\x00" * 8 + b"".join(msg_h)) == cur:
                res["hash_curent"] = "conform textului RFC 9575"
            elif message_hash(prev + b"\x00" * 8 + link_h + b"".join(msg_h)) == cur:
                res["hash_curent"] = "conform exemplului RFC 9575 B.2.1 (include hash-ul Link)"
            else:
                s.bad += 1
                res.update(rezultat="Unverified", motiv="hash-ul manifestului curent nu corespunde")
                return res
            res["mesaje_in_manifest"] = len(msg_h)
            res["mesaje_autentificate"] = sum(h in seen for h in msg_h)
            res["link_confirmat"] = link_h in s.link_hashes
            s.authenticated_hashes.update(h for h in msg_h if h in seen)
        if key.trusted:
            s.ok_trusted += 1
        s.ok += 1
        res["rezultat"] = "Trusted" if key.trusted else "Verified"
        return res

    def state(self, sender) -> str:
        if sender in self.senders:
            return self.senders[sender].state()
        return "Partial" if self.assembler.partial(sender) else "None"
