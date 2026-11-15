"""Autentificarea Remote ID (ASTM F3411 mesajul 2, DRIP RFC 9575 / RFC 9374).

Vectori oficiali: NIST SP 800-185 (exemplele cSHAKE128 #1 si #2), RFC 8032 sectiunea 7.1
(Ed25519) si exemplul complet din RFC 9575 anexa B.2.1 (DET, cheie, mesaje, Link, Wrapper,
Manifest, cu FEC). Permutarea Keccak e comparata si cu SHAKE128 din hashlib.
"""
import hashlib
import os
from types import SimpleNamespace

import pytest

from santinela import drip
from santinela import remoteid as rid
from santinela.trackmodel import identity_of
from santinela.universal import UniversalAdapter

# ---------------------------------------------------------------- RFC 9575 anexa B.2.1
DET = bytes.fromhex("2001003ffe000105a29b3ff42226c04e")
KEY = bytes.fromhex("b5fef530d450dedb59ebafa18b00d7f5ed0ac08a81975034297bea2b00041813")
MSGS = [bytes.fromhex(x) for x in """
0240012001003ffe000105a29b3ff42226c04e000000000000
12000000000000000000000000000000000000000060220000
32004578616d706c652053656c662049440000000000000000
420000000000000000000100000000000000000010ea510900
52004578616d706c65204f70657261746f7220494400000000
0240012001003ffe000105a29b3ff42226c04e000000000000
12000000000000000000000000000000000000000060220000
420000000000000000000100000000000000000010ea510900""".split()]
LINK = [bytes.fromhex(x) for x in """
2250078910ea510904314b8564b17e66662001003ffe000105
2251a29b3ff42226c04eb5fef530d450dedb59ebafa18b00d7
2252f5ed0ac08a81975034297bea2b000418132001003ffe00
22530105b82bf1c99d87273103fc83f6ecd9b91842f205c222
2254dd71d8e165ad18ca91daf9299a73eec850c756a7e9be46
2255f51dddfa0f09db7bfdde14eec07c7a6dd1061c1d5ace94
2256d9ad97940d280000000000000000000000000000000000
2257a03b0f7a6feb0d198167045058cfc49f73129917024d22""".split()]
WRAPPER = [bytes.fromhex(x) for x in """
2250078b10ea510902e0dd7c6560115e671200000000000000
22510000000000000000000000000060220000420000000000
2252000000000100000000000000000010ea5109002001003f
2253fe000105a29b3ff42226c04ef0ecad581a030ca790152a
22542f08df5762a463e24a742d1c530ec977bbe0d113697e2b
2255b909d6c7557bdaf1227ce86154b030daadda4a6b8474de
22569a62f6c375020826000000000000000000000000000000
2257f5e8eebcb04f8c2197526053e66c010d5d7297ff7c1fe0""".split()]
MANIFEST = [bytes.fromhex(x) for x in """
225008b110ea510903e0dd7c6560115e670000000000000000
2251d57594875f8608b4d61dc9224ecf8b842bd4862734ed01
22522ca2e5f2b8a3e61547b81704766ba3eeb651be7eafc928
22538884e3e28a24fd5529bc2bd4862734ed012ca2e5f2b8a3
2254e61547b81704766ba3eeb62001003ffe000105a29b3ff4
22552226c04efb729846e7d110903797066fd96f49a77c5a48
2256c4c3b330be05bc4a958e9641718aaa31aeabad368386a2
22579ed2dce2769120da83edbcdc0858dd1e357755e7860317
2258e7c06a5918ea62a937391cbfe0983539de1b2e688b7c83""".split()]

# RFC 8032 sectiunea 7.1: (cheie secreta, cheie publica, mesaj, semnatura)
ED25519 = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46b"
     "d25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c"
     "387b2eaeb4302aeeb00d291612bb0c00"),
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc659"
     "4a7c15e9716ed28dc027beceea1ec40a"),
    ("833fe62409237b9d62ec77587520911e9a759cec1d19755b7da901b96dca3d42",
     "ec172b93ad5e563bf4932c70e1245034c35467ef2efd4d64ebf819683467e2bf",
     "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a2192992a274fc1a836ba3c23a3feebbd"
     "454d4423643ce80e2a9ac94fa54ca49f",
     "dc2a4459e7369633a52b1bf277839a00201009a3efbf3ecb69bea2186c26b58909351fc9ac90b3ecfdfbc7c66431e030"
     "3dca179c138ac17ad9bef1177331a704"),
]


# ---------------------------------------------------------------- primitive
def test_keccak_permutation_matches_hashlib_shake128():
    for n in (0, 1, 135, 167, 168, 169, 336, 1000):
        d = os.urandom(n)
        assert drip.shake128(d, 300) == hashlib.shake_128(d).digest(300)


def test_cshake128_nist_samples():
    assert drip.cshake128(bytes(range(4)), 256, b"", b"Email Signature").hex() == \
        "c1c36925b6409a04f1b504fcbca9d82b4017277cb5ed2b2065fc1d3814d5aaf5"
    assert drip.cshake128(bytes(range(200)), 256, b"", b"Email Signature").hex() == \
        "c5221d50e4f822d96a2e8881a961420f294b7b24fe3d2094baed2c6524cc166b"


@pytest.mark.parametrize("sk,pk,msg,sig", ED25519)
def test_ed25519_rfc8032_vectors(sk, pk, msg, sig):
    sk, pk, msg, sig = map(bytes.fromhex, (sk, pk, msg, sig))
    assert drip.ed25519_public_key(sk) == pk
    assert drip.ed25519_sign(sk, msg) == sig
    assert drip.ed25519_verify(pk, msg, sig)
    assert not drip.ed25519_verify(pk, msg + b"\x00", sig)
    bad = bytearray(sig)
    bad[10] ^= 1
    assert not drip.ed25519_verify(pk, msg, bytes(bad))
    # S >= L respins (maleabilitate)
    s = int.from_bytes(sig[32:], "little") + drip._L
    assert not drip.ed25519_verify(pk, msg, sig[:32] + s.to_bytes(32, "little"))


def test_ed25519_against_independent_implementation():
    ed = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    from cryptography.hazmat.primitives import serialization
    for k in range(5):
        sk = os.urandom(32)
        msg = os.urandom(k * 37)
        priv = ed.Ed25519PrivateKey.from_private_bytes(sk)
        pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        assert drip.ed25519_public_key(sk) == pub
        assert drip.ed25519_verify(pub, msg, priv.sign(msg))
        priv.public_key().verify(drip.ed25519_sign(sk, msg), msg)


# ---------------------------------------------------------------- exemplul RFC 9575
def test_rfc9575_det_and_key_binding():
    d = drip.parse_det(DET)
    assert (d.is_det, d.raa, d.hda, d.suite) == (True, 16376, 1, 5)
    assert str(d) == "2001:003f:fe00:0105:a29b:3ff4:2226:c04e"
    assert drip.det_matches_key(DET, KEY)
    assert not drip.det_matches_key(DET, bytes(32))


def _feed(pages, sender="s", v=None, **kw):
    v = v or drip.DripVerifier()
    return [r for p in pages for r in v.feed_page(sender, p, **kw)][0]


def test_rfc9575_message_hashes_are_the_manifest_entries():
    ev = _assemble(MANIFEST).data[9:-80]
    hashes = [ev[i:i + 8] for i in range(0, len(ev), 8)][3:]
    assert sorted(hashes) == sorted(drip.message_hash(m) for m in MSGS)


def _assemble(pages, sender="x"):
    a = drip.PageAssembler()
    out = []
    for p in pages:
        out += a.add(sender, p)
    assert len(out) == 1
    return out[0]


def test_rfc9575_pages_and_fec_reproduced_byte_for_byte():
    for pages in (LINK, WRAPPER, MANIFEST):
        auth = _assemble(pages)
        assert auth.fec and auth.auth_type == 5 and auth.timestamp == 0x0951EA10
        assert drip.encode_auth_pages(5, auth.data, auth.timestamp, fec=True) == pages


@pytest.mark.parametrize("lost", range(8))
def test_fec_recovers_any_single_lost_page(lost):
    full = _assemble(WRAPPER)
    pages = [p for k, p in enumerate(WRAPPER) if k != lost]
    if lost == 7:  # lipseste chiar paritatea: mesajul se incheie la inceputul urmatorului
        a = drip.PageAssembler()
        out = []
        for p in pages + [MANIFEST[0]]:
            out += a.add("x", p)
        assert len(out) == 1 and out[0].data == full.data and out[0].recovered_page == 7
        return
    got = _assemble(pages)
    assert got.data == full.data and got.recovered_page == lost


def test_rfc9575_example_verifies_wrapper_and_manifest():
    v = drip.DripVerifier()
    for m in MSGS:
        v.observe("ua", m)
    res = [r for p in LINK for r in v.feed_page("ua", p)]
    assert res[0]["format"] == "DRIP Link" and res[0]["rezultat"] == "Unverifiable"   # cheia HDA lipseste
    assert "0x04" in res[0]["nota"]
    w = [r for p in WRAPPER for r in v.feed_page("ua", p)][0]
    assert (w["format"], w["rezultat"], w["mesaje_semnate"], w["mesaje_confirmate_in_clar"]) == \
        ("DRIP Wrapper", "Verified", 2, 2)
    m = [r for p in MANIFEST for r in v.feed_page("ua", p)][0]
    assert (m["rezultat"], m["mesaje_in_manifest"], m["mesaje_autentificate"]) == ("Verified", 8, 8)
    assert m["hash_curent"].startswith("conform exemplului") and m["link_confirmat"]
    assert v.state("ua") == "Verified"
    assert v.senders["ua"].signed_positions == [MSGS[1]]


def test_tampered_wrapper_is_unverified_and_state_becomes_questionable():
    v = drip.DripVerifier()
    v.keys[DET] = drip.KeyInfo(KEY, False, "test")
    assert [r for p in WRAPPER for r in v.feed_page("ua", p)][0]["rezultat"] == "Verified"
    forged = list(WRAPPER)
    forged[1] = forged[1][:10] + bytes([forged[1][10] ^ 0x40]) + forged[1][11:]   # pozitia schimbata
    forged = drip.encode_auth_pages(5, _assemble(forged).data, 0x0951EA10, fec=True)
    r = [r for p in forged for r in v.feed_page("ua", p)][0]
    assert r["rezultat"] == "Unverified" and "semnatura" in r["motiv"]
    assert v.state("ua") == "Questionable"


# ---------------------------------------------------------------- lant de incredere sintetic
def _keys(seed):
    sk = hashlib.sha256(seed).digest()
    return sk, drip.ed25519_public_key(sk)


def _link_data(parent_sk, parent_det, child_det, child_key, vnb=100, vna=200):
    body = vnb.to_bytes(4, "little") + vna.to_bytes(4, "little") + child_det + child_key + parent_det
    return bytes([drip.SAM_LINK]) + body + drip.ed25519_sign(parent_sk, body)


def _wrapper_data(ua_sk, ua_det, msgs, vnb=100, vna=200):
    body = vnb.to_bytes(4, "little") + vna.to_bytes(4, "little") + b"".join(msgs) + ua_det
    return bytes([drip.SAM_WRAPPER]) + body + drip.ed25519_sign(ua_sk, body)


HDA_SK, HDA_PK = _keys(b"hda")
UA_SK, UA_PK = _keys(b"ua")
HDA_DET = drip.make_det(10, 20, HDA_PK)
UA_DET = drip.make_det(10, 20, UA_PK)


def test_trusted_chain_and_rejections():
    assert drip.det_matches_key(HDA_DET, HDA_PK) and drip.parse_det(UA_DET).hda == 20
    v = drip.DripVerifier()
    v.trust_anchor(HDA_DET, HDA_PK, "HDA nationala")
    loc = rid.encode_pack("RO-UAS-1", 45.3, 28.5, 12.0, 90.0, 120.0, 100.0, 100)[28:53]
    link = drip.encode_auth_pages(5, _link_data(HDA_SK, HDA_DET, UA_DET, UA_PK), 0)
    assert [r for p in link for r in v.feed_page("ua", p)][0]["rezultat"] == "Trusted"
    v.observe("ua", loc)
    w = drip.encode_auth_pages(5, _wrapper_data(UA_SK, UA_DET, [loc]), 0, fec=True)
    r = [r for p in w for r in v.feed_page("ua", p)][0]
    assert r["rezultat"] == "Trusted" and v.state("ua") == "Trusted"
    # aviz semnat de alta cheie decat a parintelui
    other_sk, _ = _keys(b"intrus")
    bad = drip.encode_auth_pages(5, _link_data(other_sk, HDA_DET, UA_DET, UA_PK), 0)
    v2 = drip.DripVerifier()
    v2.trust_anchor(HDA_DET, HDA_PK)
    r = [r for p in bad for r in v2.feed_page("x", p)][0]
    assert r["rezultat"] == "Unverified" and "parintelui" in r["motiv"]
    # DET care nu e hash-ul cheiei
    r = _feed(drip.encode_auth_pages(5, _link_data(HDA_SK, HDA_DET, UA_DET, HDA_PK), 0))
    assert r["rezultat"] == "Unverified" and "DET" in r["motiv"]


def test_validity_window_rejects_replay():
    v = drip.DripVerifier()
    v.keys[UA_DET] = drip.KeyInfo(UA_PK, False, "test")
    pages = drip.encode_auth_pages(5, _wrapper_data(UA_SK, UA_DET, [], vnb=1000, vna=1120), 0)
    now_ok = drip.F3411_EPOCH + 1060
    assert [r for p in pages for r in v.feed_page("a", p, now_unix=now_ok)][0]["rezultat"] == "Verified"
    r = _feed(pages)
    assert r["rezultat"] == "Unverifiable"
    v3 = drip.DripVerifier()
    v3.keys[UA_DET] = drip.KeyInfo(UA_PK, False, "test")
    r = [r for p in pages for r in v3.feed_page("c", p, now_unix=now_ok + 3600)][0]
    assert r["rezultat"] == "Unverified" and "reluare" in r["motiv"]


def test_astm_external_auth_types_are_unsupported():
    pages = drip.encode_auth_pages(1, os.urandom(40), 0)
    r = _feed(pages)
    assert r["rezultat"] == "Unsupported" and "serviciul extern" in r["motiv"]


# ---------------------------------------------------------------- adaptorul
def test_adapter_legacy_bluetooth_pages_and_track_identity():
    ad = UniversalAdapter.from_config({"sources": [], "drip_trust_anchors": [
        {"det": str(drip.parse_det(HDA_DET)), "key": HDA_PK.hex(), "name": "HDA"}]})
    pack = rid.encode_pack("RO-UAS-1", 45.3, 28.5, 12.0, 90.0, 120.0, 100.0, 100)
    basic, loc = pack[3:28], pack[28:53]
    mac = "AA:BB:CC:00:00:01"
    frames = [rid.wrap_bluetooth(basic, 1), rid.wrap_bluetooth(loc, 2)]
    frames += [rid.wrap_bluetooth(p, 3) for p in drip.encode_auth_pages(5, _link_data(HDA_SK, HDA_DET, UA_DET,
                                                                                       UA_PK), 0, fec=True)]
    frames += [rid.wrap_bluetooth(p, 4) for p in drip.encode_auth_pages(5, _wrapper_data(UA_SK, UA_DET, [loc]), 0,
                                                                         fec=True)]
    frames += [rid.wrap_bluetooth(loc, 5)]
    for f in frames:
        ad.feed(f, received_at=1.0, source=mac)
    assert ad.stats["autentificari_remoteid"] == 2
    assert ad.drip.state(mac) == "Trusted" and ad.cooperative[-1].auth == "Trusted"
    notes = ad.sources[mac].notes
    assert any("DRIP Wrapper: Trusted" in n for n in notes)
    tr = SimpleNamespace(identity_override=None, cooperative_id="RemoteID:RO-UAS-1", cooperative_auth="Trusted",
                         status="confirmat")
    assert identity_of(tr) == ("neutru", "automat (Remote ID autentificat DRIP, cheie de incredere)")
    tr.cooperative_auth = "Unverified"
    assert identity_of(tr)[0] == "suspect"


def test_adapter_extended_transport_wrapper_in_message_pack():
    ad = UniversalAdapter()
    ad.drip.keys[UA_DET] = drip.KeyInfo(UA_PK, False, "test")
    pack = rid.encode_pack("RO-UAS-2", 45.31, 28.52, 10.0, 45.0, 90.0, 80.0, 200)
    basic, loc = pack[3:28], pack[28:53]
    body = (100).to_bytes(4, "little") + (200).to_bytes(4, "little") + basic + loc + UA_DET
    data = bytes([drip.SAM_WRAPPER]) + body[:8] + UA_DET + drip.ed25519_sign(UA_SK, body)
    pages = drip.encode_auth_pages(5, data, 0)
    ad.feed(rid.pack(basic, loc, *pages), received_at=2.0, source="wifi:01")
    assert ad.drip.state("wifi:01") == "Verified"
    assert ad.cooperative[-1].auth == "Verified" and ad.cooperative[-1].icao == "RO-UAS-2"


def test_adapter_flags_forged_remote_id():
    ad = UniversalAdapter()
    ad.drip.keys[UA_DET] = drip.KeyInfo(UA_PK, False, "test")
    loc = rid.encode_pack("RO-UAS-3", 45.3, 28.5, 12.0, 90.0, 120.0, 100.0, 100)[28:53]
    data = bytearray(_wrapper_data(UA_SK, UA_DET, [loc]))
    data[20] ^= 0x01                                     # mesajul Location modificat dupa semnare
    for p in drip.encode_auth_pages(5, bytes(data), 0):
        ad.feed(rid.wrap_bluetooth(p), source="BT:2")
    assert any("posibila falsificare" in e[1] for e in ad.errors)
    assert ad.drip.state("BT:2") == "Unverified"
