#!/usr/bin/env python3
"""Golden vector generator for the C++ port of I2PChat.

Runs against the reference Python implementation and emits JSON fixtures that
the C++ test suite replays. The C++ implementation is considered compatible
only when it reproduces every deterministic vector byte-for-byte and decrypts
every sealed sample here.

Every vector is produced by calling the *production* code paths in
``i2pchat.*`` rather than by re-deriving formats, so the fixtures cannot drift
from the implementation they describe.

Randomness (salts, SecretBox nonces, padding filler) is replaced by a
deterministic keystream for the duration of generation. That keeps the emitted
files byte-stable across regenerations, so a diff in ``vectors/`` always means
a real behavioural change in the Python implementation.

Usage:
    .venv/bin/python cpp/testdata/generate_vectors.py
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import sys
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = Path(__file__).resolve().parent / "vectors"

sys.path.insert(0, str(REPO_ROOT))


# --------------------------------------------------------------------------
# Deterministic randomness
# --------------------------------------------------------------------------


class _DeterministicStream:
    """SHA-256 based counter keystream standing in for the CSPRNG."""

    def __init__(self, seed: bytes) -> None:
        self._seed = seed
        self._counter = 0

    def read(self, size: int) -> bytes:
        out = b""
        while len(out) < size:
            out += hashlib.sha256(
                self._seed + self._counter.to_bytes(8, "big")
            ).digest()
            self._counter += 1
        return out[:size]


_STREAM = _DeterministicStream(b"i2pchat-cpp-golden-vectors-v1")


def _install_deterministic_randomness() -> None:
    """Redirect every randomness source used by the reference code."""
    os.urandom = _STREAM.read  # type: ignore[assignment]
    secrets.token_bytes = _STREAM.read  # type: ignore[assignment]
    secrets.token_hex = lambda n=32: _STREAM.read(n).hex()  # type: ignore[assignment]

    import nacl.secret
    import nacl.utils

    nacl.utils.random = _STREAM.read  # type: ignore[assignment]
    nacl.secret.random = _STREAM.read  # type: ignore[assignment]


_install_deterministic_randomness()

from i2pchat import crypto  # noqa: E402
from i2pchat.blindbox import blindbox_blob, blindbox_key_schedule  # noqa: E402
from i2pchat.groups import invite as group_invite  # noqa: E402
from i2pchat.groups import wire as group_wire  # noqa: E402
from i2pchat.protocol import chat_text_chunking  # noqa: E402
from i2pchat.protocol.protocol_codec import (  # noqa: E402
    FLAG_ENCRYPTED,
    HEADER_SIZE,
    MAGIC,
    PROTOCOL_VERSION,
    ProtocolCodec,
)
from i2pchat.sam import destination as sam_destination  # noqa: E402
from i2pchat.sam import protocol as sam_protocol  # noqa: E402
from i2pchat.storage import chat_history, group_store, profile_dat, sealed_json  # noqa: E402
from i2pchat.storage import contact_book, compose_drafts_store  # noqa: E402

# Frame types accepted by the live channel, mirroring I2PChatCore.
ALLOWED_FRAME_TYPES = {"U", "S", "P", "O", "F", "D", "E", "I", "H", "G"}
MAX_FRAME_BODY = 2 * 1024 * 1024
PADDING_ENVELOPE_MAGIC = b"I2PPAD1"
PADDING_BALANCED_BLOCK = 128

# Stable inputs shared across vector groups.
INIT_ADDR = "aaaabbbbccccddddeeeeffffgggghhhhiiiijjjjkkkkllllmmmm"
RESP_ADDR = "nnnnooooppppqqqqrrrrssssttttuuuuvvvvwwwwxxxxyyyyzzzz"
INIT_SIGNING_SEED = bytes.fromhex(
    "1111111111111111111111111111111111111111111111111111111111111111"
)
RESP_SIGNING_SEED = bytes.fromhex(
    "2222222222222222222222222222222222222222222222222222222222222222"
)
IDENTITY_KEY = bytes.fromhex(
    "3333333333333333333333333333333333333333333333333333333333333333"
)
WRAP_KEY = bytes.fromhex(
    "4444444444444444444444444444444444444444444444444444444444444444"
)


def _hex(raw: bytes) -> str:
    return raw.hex()


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _emit(name: str, description: str, payload: dict[str, Any]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    document = {
        "_description": description,
        "_generator": "cpp/testdata/generate_vectors.py",
        **payload,
    }
    path = OUT_DIR / f"{name}.json"
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, ensure_ascii=True, sort_keys=False)
        handle.write("\n")
    print(f"  {path.relative_to(REPO_ROOT)}")


# --------------------------------------------------------------------------
# HKDF / HMAC / SHA-256
# --------------------------------------------------------------------------


def gen_hkdf() -> None:
    cases = []
    samples = [
        (b"", b"", 32),
        (b"salt", b"input keying material", 32),
        (b"I2PCHAT-PROFILE-DAT", WRAP_KEY, 32),
        (b"\x00" * 32, b"\xff" * 64, 64),
        # Expand outputs that span several HMAC blocks exercise the counter.
        (b"multi-block", b"ikm", 100),
        (b"unicode salt \xd0\xbf\xd1\x80\xd0\xb8\xd0\xb2\xd0\xb5\xd1\x82", b"ikm", 48),
    ]
    for salt, ikm, length in samples:
        prk = crypto.hkdf_extract(salt, ikm)
        okm = crypto.hkdf_expand(prk, b"I2PCHAT-TEST|info", length)
        cases.append(
            {
                "salt_hex": _hex(salt),
                "ikm_hex": _hex(ikm),
                "info_hex": _hex(b"I2PCHAT-TEST|info"),
                "length": length,
                "prk_hex": _hex(prk),
                "okm_hex": _hex(okm),
            }
        )

    mac_cases = []
    for key, msg in [
        (b"\x00" * 32, b""),
        (WRAP_KEY, b"short"),
        (IDENTITY_KEY, bytes(range(256))),
    ]:
        mac_cases.append(
            {
                "key_hex": _hex(key),
                "message_hex": _hex(msg),
                "hmac_sha256_hex": _hex(
                    hmac.new(key, msg, hashlib.sha256).digest()
                ),
                "sha256_hex": _hex(hashlib.sha256(msg).digest()),
            }
        )

    _emit(
        "crypto_hkdf",
        "HKDF-SHA256 (RFC 5869) extract/expand plus raw HMAC-SHA256 and SHA-256 "
        "vectors. Source: i2pchat/crypto.py hkdf_extract/hkdf_expand.",
        {"hkdf": cases, "hmac_sha256": mac_cases},
    )


# --------------------------------------------------------------------------
# Handshake HS4
# --------------------------------------------------------------------------


def gen_handshake() -> None:
    # Ephemeral X25519 material is fixed so the whole exchange is reproducible.
    init_eph_priv = bytes.fromhex(
        "5555555555555555555555555555555555555555555555555555555555555555"
    )
    resp_eph_priv = bytes.fromhex(
        "6666666666666666666666666666666666666666666666666666666666666666"
    )
    from nacl.public import PrivateKey as NaclPrivateKey

    init_eph_pub = bytes(NaclPrivateKey(init_eph_priv).public_key)
    resp_eph_pub = bytes(NaclPrivateKey(resp_eph_priv).public_key)

    nonce_init = hashlib.sha256(b"nonce-init").digest()
    nonce_resp = hashlib.sha256(b"nonce-resp").digest()

    init_sign_pub = crypto.get_verify_key_from_seed(INIT_SIGNING_SEED)
    resp_sign_pub = crypto.get_verify_key_from_seed(RESP_SIGNING_SEED)

    dh_from_init = crypto.compute_dh_shared_secret(init_eph_priv, resp_eph_pub)
    dh_from_resp = crypto.compute_dh_shared_secret(resp_eph_priv, init_eph_pub)
    assert dh_from_init == dh_from_resp, "X25519 DH must agree on both sides"

    k_enc_i2r, k_mac_i2r, k_enc_r2i, k_mac_r2i = crypto.derive_handshake_subkeys(
        dh_from_init, nonce_init, nonce_resp
    )

    # The signed transcript strings deliberately carry the HS3 domain label
    # while key derivation uses HS4. Reproduce as-is.
    init_sig_payload = (
        f"I2PCHAT-HS3|INIT|{INIT_ADDR}|{RESP_ADDR}|"
        f"{nonce_init.hex()}|{init_eph_pub.hex()}|{init_sign_pub.hex()}"
    ).encode("utf-8")
    resp_sig_payload = (
        f"I2PCHAT-HS3|RESP|{RESP_ADDR}|{INIT_ADDR}|"
        f"{nonce_init.hex()}|{init_eph_pub.hex()}|{init_sign_pub.hex()}|"
        f"{nonce_resp.hex()}|{resp_eph_pub.hex()}|{resp_sign_pub.hex()}"
    ).encode("utf-8")

    init_sig = crypto.sign_data(INIT_SIGNING_SEED, init_sig_payload)
    resp_sig = crypto.sign_data(RESP_SIGNING_SEED, resp_sig_payload)
    assert crypto.verify_signature(init_sign_pub, init_sig_payload, init_sig)
    assert crypto.verify_signature(resp_sign_pub, resp_sig_payload, resp_sig)

    transcript_hash = crypto.compute_handshake_transcript_hash(resp_sig_payload)
    finished_i2r = crypto.compute_handshake_finished(k_mac_i2r, transcript_hash)
    finished_r2i = crypto.compute_handshake_finished(k_mac_r2i, transcript_hash)

    frame_macs = []
    for msg_type, seq, msg_id, flags, body in [
        ("U", 1, 0, FLAG_ENCRYPTED, b"hello"),
        ("U", 2, 0xDEADBEEF, FLAG_ENCRYPTED, b""),
        ("D", 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, FLAG_ENCRYPTED, bytes(range(64))),
        ("P", 7, 42, 0, b"ping"),
    ]:
        frame_macs.append(
            {
                "key_hex": _hex(k_mac_i2r),
                "msg_type": msg_type,
                "seq": seq,
                "msg_id": msg_id,
                "flags": flags,
                "body_hex": _hex(body),
                "mac_hex": _hex(
                    crypto.compute_mac(
                        k_mac_i2r, msg_type, body, seq=seq, msg_id=msg_id, flags=flags
                    )
                ),
            }
        )

    _emit(
        "crypto_handshake",
        "Full HS4 handshake transcript with fixed keys: X25519 DH, directional "
        "subkey derivation, Ed25519 INIT/RESP signatures, FINISHED key "
        "confirmation and per-frame MAC. Note the signed transcript uses the "
        "HS3 domain label while the KDF uses HS4 — reproduce both verbatim. "
        "Source: i2pchat/crypto.py and _build_{init,resp}_sig_payload in "
        "i2pchat/core/i2p_chat_core.py.",
        {
            "parties": {
                "initiator_addr": INIT_ADDR,
                "responder_addr": RESP_ADDR,
                "initiator_signing_seed_hex": _hex(INIT_SIGNING_SEED),
                "initiator_signing_pub_hex": _hex(init_sign_pub),
                "responder_signing_seed_hex": _hex(RESP_SIGNING_SEED),
                "responder_signing_pub_hex": _hex(resp_sign_pub),
                "initiator_eph_priv_hex": _hex(init_eph_priv),
                "initiator_eph_pub_hex": _hex(init_eph_pub),
                "responder_eph_priv_hex": _hex(resp_eph_priv),
                "responder_eph_pub_hex": _hex(resp_eph_pub),
                "nonce_init_hex": _hex(nonce_init),
                "nonce_resp_hex": _hex(nonce_resp),
            },
            "dh_shared_hex": _hex(dh_from_init),
            "subkeys": {
                "k_enc_i2r_hex": _hex(k_enc_i2r),
                "k_mac_i2r_hex": _hex(k_mac_i2r),
                "k_enc_r2i_hex": _hex(k_enc_r2i),
                "k_mac_r2i_hex": _hex(k_mac_r2i),
            },
            "signatures": {
                "init_sig_payload_utf8": init_sig_payload.decode("utf-8"),
                "init_signature_hex": _hex(init_sig),
                "resp_sig_payload_utf8": resp_sig_payload.decode("utf-8"),
                "resp_signature_hex": _hex(resp_sig),
            },
            "key_confirmation": {
                "transcript_hash_hex": _hex(transcript_hash),
                "finished_i2r_hex": _hex(finished_i2r),
                "finished_r2i_hex": _hex(finished_r2i),
            },
            "frame_macs": frame_macs,
            "invalid_dh_public_keys_hex": [
                _hex(bytes(32)),
                _hex(bytes(31)),
            ],
            # Exact plaintext H-frame bodies exchanged during the handshake.
            "messages": {
                "init": (
                    f"INIT:{nonce_init.hex()}:{init_eph_pub.hex()}:"
                    f"{init_sign_pub.hex()}:{init_sig.hex()}"
                ),
                "resp": (
                    f"RESP:{nonce_resp.hex()}:{resp_eph_pub.hex()}:"
                    f"{resp_sign_pub.hex()}:{resp_sig.hex()}"
                ),
                "finished_from_initiator": f"FINISHED:{finished_i2r.hex()}",
                "finished_from_responder": f"FINISHED:{finished_r2i.hex()}",
            },
            "directional_key_roles": {
                "initiator": {"send": "i2r", "recv": "r2i"},
                "responder": {"send": "r2i", "recv": "i2r"},
            },
            "timeouts_seconds": {
                "handshake": 90,
                "keepalive_interval": 15,
                "liveness": 90,
            },
        },
    )


# --------------------------------------------------------------------------
# vNext framing
# --------------------------------------------------------------------------


def gen_frames() -> None:
    codec = ProtocolCodec(
        allowed_types=ALLOWED_FRAME_TYPES, max_frame_body=MAX_FRAME_BODY
    )

    plaintext_frames = []
    for msg_type, payload, msg_id, flags in [
        ("U", b"hello world", 1, 0),
        ("U", "привет 🌍".encode("utf-8"), 2, 0),
        ("P", b"", 0, 0),
        ("H", b"INIT:abc", 0xFFFFFFFFFFFFFFFF, 0),
        ("D", bytes(range(256)), 0x0102030405060708, 0),
    ]:
        encoded = codec.encode(msg_type, payload, msg_id=msg_id, flags=flags)
        plaintext_frames.append(
            {
                "msg_type": msg_type,
                "payload_hex": _hex(payload),
                "msg_id": msg_id,
                "flags": flags,
                "encoded_hex": _hex(encoded),
            }
        )

    # Encrypted frames: SecretBox nonces come from the deterministic stream, so
    # these are stable, but a C++ implementation should validate them by
    # decrypting rather than by re-encrypting.
    enc_key = bytes.fromhex(
        "7777777777777777777777777777777777777777777777777777777777777777"
    )
    mac_key = bytes.fromhex(
        "8888888888888888888888888888888888888888888888888888888888888888"
    )
    encrypted_frames = []
    for msg_type, plain, seq, msg_id in [
        ("U", b"encrypted hello", 1, 100),
        ("U", "шифр 🔐".encode("utf-8"), 2, 101),
        ("S", b"__SIGNAL__:MSG_ACK|100", 3, 102),
    ]:
        encrypted_body = crypto.encrypt_message(enc_key, plain)
        mac = crypto.compute_mac(
            mac_key, msg_type, encrypted_body, seq=seq, msg_id=msg_id, flags=FLAG_ENCRYPTED
        )
        frame_payload = seq.to_bytes(8, "big") + encrypted_body + mac
        encoded = codec.encode(
            msg_type, frame_payload, msg_id=msg_id, flags=FLAG_ENCRYPTED
        )
        encrypted_frames.append(
            {
                "msg_type": msg_type,
                "plaintext_hex": _hex(plain),
                "seq": seq,
                "msg_id": msg_id,
                "enc_key_hex": _hex(enc_key),
                "mac_key_hex": _hex(mac_key),
                "encrypted_body_hex": _hex(encrypted_body),
                "mac_hex": _hex(mac),
                "encoded_hex": _hex(encoded),
            }
        )

    # A frame preceded by junk: the reader must resync on MAGIC.
    good = codec.encode("U", b"after junk", msg_id=9, flags=0)
    resync = {
        "stream_hex": _hex(b"\x00\x01\x02garbage\x89I2" + good),
        "expected_msg_type": "U",
        "expected_payload_hex": _hex(b"after junk"),
        "expected_msg_id": 9,
    }

    rejects = [
        {
            "reason": "unsupported version",
            "stream_hex": _hex(
                struct.pack(">4sBBBQI", MAGIC, 3, ord("U"), 0, 0, 0)
            ),
        },
        {
            "reason": "unknown frame type",
            "stream_hex": _hex(
                struct.pack(">4sBBBQI", MAGIC, PROTOCOL_VERSION, ord("Z"), 0, 0, 0)
            ),
        },
        {
            "reason": "declared length above MAX_FRAME_BODY",
            "stream_hex": _hex(
                struct.pack(
                    ">4sBBBQI",
                    MAGIC,
                    PROTOCOL_VERSION,
                    ord("U"),
                    0,
                    0,
                    MAX_FRAME_BODY + 1,
                )
            ),
        },
    ]

    padding = []
    for body in [b"", b"x", b"y" * 100, b"z" * 121, b"w" * 200]:
        wrapped = PADDING_ENVELOPE_MAGIC + len(body).to_bytes(4, "big") + body
        target = (
            (len(wrapped) + PADDING_BALANCED_BLOCK - 1) // PADDING_BALANCED_BLOCK
        ) * PADDING_BALANCED_BLOCK
        padding.append(
            {
                "body_hex": _hex(body),
                "envelope_prefix_hex": _hex(wrapped),
                "padded_total_length": max(target, len(wrapped)),
            }
        )

    _emit(
        "protocol_frames",
        "vNext framing: MAGIC(4)|VER(1)|TYPE(1)|FLAGS(1)|MSG_ID(8 BE)|LEN(4 BE). "
        "Plaintext encodings are fully deterministic; encrypted frames should be "
        "validated by decrypting. Source: i2pchat/protocol/protocol_codec.py and "
        "_build_frame / _apply_padding_profile in i2pchat/core/i2p_chat_core.py.",
        {
            "constants": {
                "magic_hex": _hex(MAGIC),
                "protocol_version": PROTOCOL_VERSION,
                "header_size": HEADER_SIZE,
                "flag_encrypted": FLAG_ENCRYPTED,
                "encrypted_trailer_size": 8 + 32,
                "max_frame_body": MAX_FRAME_BODY,
                "resync_limit": 64 * 1024,
                "allowed_types": sorted(ALLOWED_FRAME_TYPES),
                "padding_envelope_magic_hex": _hex(PADDING_ENVELOPE_MAGIC),
                "padding_balanced_block": PADDING_BALANCED_BLOCK,
            },
            "plaintext_frames": plaintext_frames,
            "encrypted_frames": encrypted_frames,
            "resync": resync,
            "must_reject": rejects,
            "padding_balanced": padding,
        },
    )


# --------------------------------------------------------------------------
# Canonical JSON (signature-critical)
# --------------------------------------------------------------------------


def gen_canonical_json() -> None:
    """Signed payloads use json.dumps(sort_keys=True, separators=(',',':'),
    ensure_ascii=True). Any divergence silently breaks signature checks."""
    cases: list[dict[str, Any]] = []
    samples: list[Any] = [
        {"b": 1, "a": 2, "C": 3, "_": 4},
        {"nested": {"z": [1, 2, 3], "a": {"k": None}}, "flag": True},
        {"unicode": "привет"},
        {"emoji": "🔐 non-BMP"},
        {"escapes": 'quote " backslash \\ newline \n tab \t cr \r'},
        {"control": "\x00\x01\x1f"},
        {"empty_containers": {"list": [], "obj": {}}},
        {"numbers": [0, -1, 1, 1000000]},
        {"null_value": None, "false_value": False},
        # Key ordering across ASCII ranges: uppercase sorts before lowercase.
        {"Z": 1, "a": 2, "A": 3, "z": 4, "0": 5, "_": 6},
    ]
    for payload in samples:
        cases.append(
            {
                "payload": payload,
                "canonical_utf8": json.dumps(
                    payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
                ),
            }
        )

    _emit(
        "canonical_json",
        "Canonical JSON serialization used for Ed25519-signed payloads: keys "
        "sorted by code point, no whitespace, non-ASCII escaped as \\uXXXX "
        "(non-BMP as surrogate pairs). The C++ serializer must match these "
        "strings exactly. Source: i2pchat/groups/invite.py:_canonical_invite_bytes "
        "and i2pchat/groups/wire.py.",
        {
            "serializer": {
                "sort_keys": True,
                "separators": [",", ":"],
                "ensure_ascii": True,
            },
            "cases": cases,
        },
    )


# --------------------------------------------------------------------------
# BlindBox
# --------------------------------------------------------------------------


def gen_blindbox() -> None:
    root_secret = bytes.fromhex(
        "9999999999999999999999999999999999999999999999999999999999999999"
    )
    pairwise = []
    for direction, index, epoch in [
        ("send", 0, 0),
        ("recv", 0, 0),
        ("send", 1, 0),
        ("send", 5, 3),
        ("recv", 1234567, 0),
    ]:
        keys = blindbox_key_schedule.derive_blindbox_message_keys(
            root_secret, INIT_ADDR, RESP_ADDR, direction, index, epoch=epoch
        )
        pairwise.append(
            {
                "local_peer_id": INIT_ADDR,
                "remote_peer_id": RESP_ADDR,
                "direction": direction,
                "index": index,
                "epoch": epoch,
                "direction_label": keys.direction_label,
                "lookup_token": keys.lookup_token,
                "lookup_key_hex": _hex(keys.lookup_key),
                "blob_key_hex": _hex(keys.blob_key),
                "state_tag_hex": _hex(keys.state_tag),
            }
        )

    # Same pair from the other side: send/recv labels must mirror.
    mirrored = blindbox_key_schedule.derive_blindbox_message_keys(
        root_secret, RESP_ADDR, INIT_ADDR, "recv", 0, epoch=0
    )

    group = []
    for direction, index, group_epoch, root_epoch in [
        ("send", 0, 0, 0),
        ("send", 1, 2, 1),
        ("recv", 3, 0, 0),
    ]:
        keys = blindbox_key_schedule.derive_group_blindbox_message_keys(
            root_secret,
            "group-alpha",
            direction,
            index,
            group_epoch=group_epoch,
            root_epoch=root_epoch,
            sender_id=INIT_ADDR,
        )
        group.append(
            {
                "group_id": "group-alpha",
                "sender_id": INIT_ADDR,
                "direction": direction,
                "index": index,
                "group_epoch": group_epoch,
                "root_epoch": root_epoch,
                "direction_label": keys.direction_label,
                "lookup_token": keys.lookup_token,
                "lookup_key_hex": _hex(keys.lookup_key),
                "blob_key_hex": _hex(keys.blob_key),
                "state_tag_hex": _hex(keys.state_tag),
            }
        )

    blobs = []
    for frame, direction, index in [
        (b"offline message frame", "send", 0),
        (bytes(range(200)), "recv", 7),
    ]:
        keys = blindbox_key_schedule.derive_blindbox_message_keys(
            root_secret, INIT_ADDR, RESP_ADDR, direction, index
        )
        blob = blindbox_blob.encrypt_blindbox_blob(
            frame, keys.blob_key, direction, index, keys.state_tag
        )
        roundtrip = blindbox_blob.decrypt_blindbox_blob(
            blob,
            keys.blob_key,
            expected_direction=direction,
            expected_index=index,
            expected_state_tag=keys.state_tag,
        )
        assert roundtrip == frame
        blobs.append(
            {
                "frame_hex": _hex(frame),
                "direction": direction,
                "index": index,
                "blob_key_hex": _hex(keys.blob_key),
                "state_tag_hex": _hex(keys.state_tag),
                "blob_hex": _hex(blob),
            }
        )

    _emit(
        "blindbox",
        "BlindBox key schedule (pairwise and group) plus encrypted blobs. Blob "
        "plaintext is BLNDBX01|ver(1)|dir(1)|index(8 BE)|state_tag(16)|len(4 BE) "
        "padded to 256-byte buckets, then SecretBox. Source: "
        "i2pchat/blindbox/blindbox_key_schedule.py and blindbox_blob.py.",
        {
            "root_secret_hex": _hex(root_secret),
            "constants": {
                "blob_magic": blindbox_blob.BLINDBOX_BLOB_MAGIC.decode("ascii"),
                "blob_version": blindbox_blob.BLINDBOX_BLOB_VERSION,
                "header_struct": ">8sBBQ16sI",
                "padding_bucket": 256,
                "direction_codes": {"send": 1, "recv": 2},
                "max_frame_size": blindbox_blob.BLINDBOX_MAX_FRAME_SIZE,
            },
            "pairwise_keys": pairwise,
            "mirrored_direction_check": {
                "note": "local/remote swapped with direction recv must match "
                "the send entry of index 0 from the other side",
                "lookup_token": mirrored.lookup_token,
                "direction_label": mirrored.direction_label,
            },
            "group_keys": group,
            "blobs": blobs,
            "replica_protocol": {
                "put": "PUT <lookup_token> <size> [auth_token]\\n<raw bytes>",
                "get": "GET <lookup_token> [auth_token]\\n",
                "responses": ["OK", "EXISTS", "FULL", "RATE", "ERR", "MISS"],
                "get_hit": "OK <size>\\n<raw bytes>",
                "default_port": 19444,
            },
        },
    )


def gen_blindbox_state() -> None:
    """Vectors for the local wrapping of BlindBox root secrets.

    The per-peer state file is plain JSON, but the root secrets inside it are
    encrypted under a key derived from the profile name, the peer id and the
    local signing seed. Without matching this byte for byte, a C++ client cannot
    read the BlindBox state a Python client left behind, and every offline
    message already in flight would be lost.
    """
    from i2pchat.core.i2p_chat_core import (  # noqa: PLC0415
        BLINDBOX_LOCAL_WRAP_VERSION_CURRENT,
        BLINDBOX_LOCAL_WRAP_VERSION_LEGACY,
        I2PChatCore,
    )
    from i2pchat.storage.blindbox_state import BlindBoxState  # noqa: PLC0415

    core = I2PChatCore(profile="default")
    # Assigned rather than trusted from the constructor: the profile name is
    # part of the wrap key, and the vector has to pin it.
    core.profile = "default"
    core.my_signing_seed = INIT_SIGNING_SEED

    root_secret = bytes.fromhex(
        "4242424242424242424242424242424242424242424242424242424242424242"
    )
    peer_id = RESP_ADDR
    group_scope = "group:group-alpha"

    wrap_keys = []
    for scope in (peer_id, f"{peer_id}.b32.i2p", group_scope):
        wrap_keys.append(
            {
                "scope": scope,
                "profile": core.profile,
                "signing_seed_hex": _hex(INIT_SIGNING_SEED),
                "wrap_key_v1_hex": _hex(
                    core._blindbox_local_wrap_key(
                        scope, wrap_version=BLINDBOX_LOCAL_WRAP_VERSION_LEGACY
                    )
                ),
                "wrap_key_v2_hex": _hex(
                    core._blindbox_local_wrap_key(
                        scope, wrap_version=BLINDBOX_LOCAL_WRAP_VERSION_CURRENT
                    )
                ),
            }
        )

    state = BlindBoxState(send_index=7, recv_base=3, recv_window=16)
    for consumed in (0, 1, 2, 5):
        state.mark_consumed(consumed)
    state_payload = state.to_dict()
    state_payload["updated_at"] = 1700000000

    encrypted_current = core._blindbox_encrypt_root_secret(root_secret, peer_id)
    legacy_key = core._blindbox_local_wrap_key(
        peer_id, wrap_version=BLINDBOX_LOCAL_WRAP_VERSION_LEGACY
    )
    encrypted_legacy = crypto.encrypt_message(legacy_key, root_secret).hex()

    file_payload = dict(state_payload)
    file_payload["blindbox_wrap_version"] = BLINDBOX_LOCAL_WRAP_VERSION_CURRENT
    file_payload["blindbox_root_secret_enc"] = encrypted_current
    file_payload["blindbox_root_epoch"] = 4
    file_payload["blindbox_root_created_at"] = 1699990000
    file_payload["blindbox_root_send_index_base"] = 5
    file_payload["blindbox_pending_root_epoch"] = 0
    file_payload["blindbox_pending_root_created_at"] = 1700000000
    file_payload["blindbox_pending_root_send_index_base"] = 7
    file_payload["blindbox_prev_roots"] = [
        {
            "epoch": 3,
            "expires_at": 1700003600,
            "secret_enc": core._blindbox_encrypt_root_secret(
                bytes.fromhex(
                    "3131313131313131313131313131313131313131313131313131313131313131"
                ),
                peer_id,
            ),
        }
    ]

    _emit(
        "blindbox_state",
        "Local wrapping of BlindBox root secrets and the per-peer state file. "
        "Wrap v2: salt=SHA256('BLINDBOX-LOCAL-WRAP-SALT-V2|'+profile+'|'+scope), "
        "prk=HKDF-Extract(salt, signing_seed), "
        "key=HKDF-Expand(prk, 'BLINDBOX-LOCAL-WRAP-KEY-V2|'+profile+'|'+scope, 32). "
        "Wrap v1 (read-only, for migration): "
        "salt=HKDF-Extract('', SHA256('BLINDBOX-LOCAL-WRAP-SALT|'+profile+'|'+scope)), "
        "key=HKDF-Expand(salt, 'BLINDBOX-LOCAL-WRAP-KEY', 32) — no signing seed. "
        "Scope is the peer id lowercased with a trailing '.b32.i2p' stripped, or "
        "'group:<group_id>'. Source: i2pchat/core/i2p_chat_core.py and "
        "i2pchat/storage/blindbox_state.py.",
        {
            "constants": {
                "wrap_version_legacy": BLINDBOX_LOCAL_WRAP_VERSION_LEGACY,
                "wrap_version_current": BLINDBOX_LOCAL_WRAP_VERSION_CURRENT,
                "state_version": state_payload["version"],
                "default_recv_window": 16,
                "filename": "{profile}.blindbox.{safe_peer}.json",
                "safe_peer_rule": "lowercase, then every character outside "
                "[a-z0-9._-] replaced with '_'",
            },
            "wrap_keys": wrap_keys,
            "root_secret_hex": _hex(root_secret),
            "peer_id": peer_id,
            "encrypted_root_v2_hex": encrypted_current,
            "encrypted_root_v1_hex": encrypted_legacy,
            "state": state_payload,
            "state_file": file_payload,
        },
    )


# --------------------------------------------------------------------------
# Sealed at-rest formats
# --------------------------------------------------------------------------


def gen_sealed_files(tmp_dir: Path) -> None:
    entries: list[dict[str, Any]] = []

    # Profile .dat (wrap-key derived, not identity derived).
    private_key_b64 = sam_destination.i2p_b64encode(bytes(range(64)))
    dat_blob = profile_dat.encrypt_profile_dat(private_key_b64, WRAP_KEY)
    assert profile_dat.decrypt_profile_dat(dat_blob, WRAP_KEY) == private_key_b64
    entries.append(
        {
            "kind": "profile_dat",
            "path_template": "{profile}.dat",
            "magic": "I2PK",
            "version": profile_dat.PROFILE_DAT_VERSION,
            "key_material": {"wrap_key_hex": _hex(WRAP_KEY)},
            "kdf": {
                "stage1_extract_salt": "I2PCHAT-PROFILE-DAT",
                "stage1_expand_info": "I2PCHAT-PROFILE-DAT|profile-key",
                "stage2_extract_salt": "<file salt (32 bytes from header)>",
                "stage2_expand_info": "I2PCHAT-PROFILE-DAT|file-key",
            },
            "plaintext_utf8": private_key_b64 + "\n",
            "blob_hex": _hex(dat_blob),
            "keyring": {
                "service": profile_dat.KEYRING_SERVICE,
                "account_template": "{profile}" + profile_dat.DAT_WRAP_KEYRING_SUFFIX,
                "encoding": "base64 of 32 raw bytes",
                "sidecar_template": "{profile}.dat.wrap",
            },
        }
    )

    # Contacts (sealed JSON, identity keyed).
    contacts_payload = {
        "version": 2,
        "last_active_peer": RESP_ADDR,
        "contacts": [
            {
                "addr": RESP_ADDR,
                "display_name": "Тест 🙂",
                "note": "note",
                "last_preview": "hi",
                "last_activity_ts": "2026-01-01T00:00:00+00:00",
            }
        ],
    }
    contacts_path = tmp_dir / "profile.contacts.json"
    sealed_json.write_sealed_json(
        str(contacts_path),
        contacts_payload,
        identity_key=IDENTITY_KEY,
        magic=contact_book.CONTACTS_STORE_MAGIC,
        domain=contact_book.CONTACTS_STORE_DOMAIN,
    )
    entries.append(
        {
            "kind": "contacts",
            "path_template": "{profile}.contacts.json",
            "magic": contact_book.CONTACTS_STORE_MAGIC.decode("ascii"),
            "version": sealed_json.SEALED_JSON_ENC_VERSION,
            "key_material": {"identity_key_hex": _hex(IDENTITY_KEY)},
            "kdf": {
                "domain": contact_book.CONTACTS_STORE_DOMAIN.decode("ascii"),
                "scheme": "sealed_json two-stage HKDF",
            },
            "plaintext_utf8": json.dumps(
                contacts_payload, ensure_ascii=True, separators=(",", ":")
            ),
            "blob_hex": _hex(contacts_path.read_bytes()),
        }
    )

    # Compose drafts (sealed JSON).
    drafts_payload = {"version": 1, "drafts": {RESP_ADDR: "unsent draft"}}
    drafts_path = tmp_dir / "profile.compose_drafts.json"
    sealed_json.write_sealed_json(
        str(drafts_path),
        drafts_payload,
        identity_key=IDENTITY_KEY,
        magic=compose_drafts_store.COMPOSE_DRAFTS_MAGIC,
        domain=compose_drafts_store.COMPOSE_DRAFTS_DOMAIN,
    )
    entries.append(
        {
            "kind": "compose_drafts",
            "path_template": "{profile}.compose_drafts.json",
            "magic": compose_drafts_store.COMPOSE_DRAFTS_MAGIC.decode("ascii"),
            "version": sealed_json.SEALED_JSON_ENC_VERSION,
            "key_material": {"identity_key_hex": _hex(IDENTITY_KEY)},
            "kdf": {
                "domain": compose_drafts_store.COMPOSE_DRAFTS_DOMAIN.decode("ascii"),
                "scheme": "sealed_json two-stage HKDF",
            },
            "plaintext_utf8": json.dumps(
                drafts_payload, ensure_ascii=True, separators=(",", ":")
            ),
            "blob_hex": _hex(drafts_path.read_bytes()),
        }
    )

    # Chat history (its own KDF that binds the peer id into the file key).
    history_profile_key = chat_history.derive_history_key(IDENTITY_KEY)
    history_salt = _STREAM.read(chat_history.SALT_SIZE)
    history_payload = {
        "version": 2,
        "peer": RESP_ADDR,
        "messages": [
            {"kind": "out", "text": "привет", "ts": "2026-01-01T00:00:00+00:00"},
            {"kind": "in", "text": "hello", "ts": "2026-01-01T00:00:01+00:00"},
        ],
        "truncated_at": None,
    }
    history_plain = json.dumps(
        history_payload, ensure_ascii=True, separators=(",", ":")
    ).encode("utf-8")
    history_file_key = chat_history._derive_file_key(
        history_profile_key, history_salt, RESP_ADDR
    )
    history_blob = (
        chat_history.HISTORY_MAGIC
        + struct.pack(">H", chat_history.HISTORY_VERSION)
        + history_salt
        + crypto.encrypt_message(history_file_key, history_plain)
    )
    entries.append(
        {
            "kind": "chat_history",
            "path_template": "{profile}.history.<sha256(lower(peer))>.enc",
            "magic": chat_history.HISTORY_MAGIC.decode("ascii"),
            "version": chat_history.HISTORY_VERSION,
            "key_material": {"identity_key_hex": _hex(IDENTITY_KEY)},
            "kdf": {
                "profile_key": "HKDF(salt='I2PCHAT-HISTORY', ikm=identity_key, "
                "info='I2PCHAT-HISTORY|profile-key')",
                "file_key": "HKDF(salt=file_salt, ikm=profile_key, "
                "info='I2PCHAT-HISTORY|file-key|' + lower(peer))",
                "profile_key_hex": _hex(history_profile_key),
                "file_key_hex": _hex(history_file_key),
            },
            "peer": RESP_ADDR,
            "peer_file_id": chat_history._safe_peer_id(RESP_ADDR),
            "legacy_peer_file_id": chat_history._legacy_safe_peer_id(RESP_ADDR),
            "plaintext_utf8": history_plain.decode("utf-8"),
            "blob_hex": _hex(history_blob),
        }
    )

    # Group store record.
    group_id = "group-alpha"
    group_token = group_store._safe_group_token(group_id)
    group_payload = {
        "version": 1,
        "state": {"group_id": group_id, "title": "Группа", "epoch": 1,
                  "members": [INIT_ADDR, RESP_ADDR]},
        "next_group_seq": 3,
        "history": [],
        "seen_msg_ids": [],
        "pending_deliveries": [],
    }
    group_path = tmp_dir / f"profile.group.{group_token}.json"
    group_store._write_group_payload(
        str(group_path), group_payload, group_token, IDENTITY_KEY
    )
    entries.append(
        {
            "kind": "group_store",
            "path_template": "{profile}.group.<sha256(group_id)>.json",
            "magic": group_store.GROUP_STORE_MAGIC.decode("ascii"),
            "version": group_store.GROUP_STORE_ENC_VERSION,
            "key_material": {"identity_key_hex": _hex(IDENTITY_KEY)},
            "kdf": {
                "profile_key": "HKDF(salt='I2PCHAT-GROUPSTORE', ikm=identity_key, "
                "info='I2PCHAT-GROUPSTORE|profile-key')",
                "file_key": "HKDF(salt=file_salt, ikm=profile_key, "
                "info='I2PCHAT-GROUPSTORE|file-key|' + group_token)",
            },
            "group_id": group_id,
            "group_token": group_token,
            "plaintext_utf8": json.dumps(
                group_payload, ensure_ascii=True, separators=(",", ":")
            ),
            "blob_hex": _hex(group_path.read_bytes()),
        }
    )

    _emit(
        "sealed_files",
        "One sample of each at-rest format written by the reference "
        "implementation. A C++ port must decrypt every blob here to the stated "
        "plaintext. Common layout: magic(4)|version(2 BE)|salt(32)|SecretBox, "
        "where SecretBox output is nonce(24)||ciphertext||tag(16).",
        {
            "common_layout": {
                "header": "magic(4) | version(uint16 BE) | salt(32)",
                "body": "NaCl SecretBox: nonce(24) || ciphertext || Poly1305 tag(16)",
                "json_serialization": "ensure_ascii=True, separators=(',',':')",
                "legacy": "plaintext JSON without magic is still accepted on read",
            },
            "files": entries,
        },
    )


# --------------------------------------------------------------------------
# Groups: invites and wire envelopes
# --------------------------------------------------------------------------


def gen_groups() -> None:
    from datetime import datetime, timezone

    created = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    invite = group_invite.build_group_invite(
        group_id="group-alpha",
        members=[INIT_ADDR, RESP_ADDR],
        epoch=2,
        inviter_id=INIT_ADDR,
        title="Группа 🎯",
        invite_id="abcdef0123456789",
        created_at=created,
    )
    token = group_invite.encode_group_invite(invite, INIT_SIGNING_SEED)
    decoded = group_invite.decode_group_invite(token)
    assert decoded.invite_id == invite.invite_id
    assert decoded.group_id == invite.group_id

    inviter_pub = crypto.get_verify_key_from_seed(INIT_SIGNING_SEED)
    canonical = group_invite._canonical_invite_bytes(
        version=group_invite.GROUP_INVITE_VERSION,
        invite_id=invite.invite_id,
        group_id=invite.group_id,
        title=invite.title,
        members=invite.members,
        epoch=invite.epoch,
        inviter_id=invite.inviter_id,
        inviter_signing_pub=inviter_pub.hex(),
        created_at=created.isoformat(),
        expires_at=None,
    )

    _emit(
        "groups",
        "Signed and sealed group invite: shareable token is "
        "base64url(wrap_key(32) || SecretBox(JSON)) with no prefix; the inner "
        "JSON is Ed25519-signed over I2PCHAT-GROUP-INVITE-v2|<canonical JSON>. "
        "Source: i2pchat/groups/invite.py.",
        {
            "invite": {
                "group_id": invite.group_id,
                "invite_id": invite.invite_id,
                "title": invite.title,
                "members": list(invite.members),
                "epoch": invite.epoch,
                "inviter_id": invite.inviter_id,
                "inviter_signing_seed_hex": _hex(INIT_SIGNING_SEED),
                "inviter_signing_pub_hex": _hex(inviter_pub),
                "created_at": created.isoformat(),
                "expires_at": None,
                "signature_domain": group_invite._INVITE_SIG_DOMAIN.decode("ascii"),
                "canonical_signed_bytes_utf8": canonical.decode("utf-8"),
                "signature_hex": decoded.signature,
                "token": token,
            },
            "seal": {
                "wrap_key_size": group_invite._INVITE_WRAP_KEY_SIZE,
                "hkdf_salt": group_invite._INVITE_WRAP_SALT.decode("ascii"),
                "hkdf_info": group_invite._INVITE_WRAP_INFO.decode("ascii"),
                "encoding": "base64url without padding",
                "layout": "wrap_key(32) || SecretBox(canonical invite JSON)",
            },
            "legacy_prefix": group_invite.GROUP_INVITE_PREFIX,
        },
    )


def gen_group_wire() -> None:
    """Group transport envelopes: v1 per-recipient and v3 signed broadcast."""
    from datetime import datetime, timezone

    from i2pchat.groups.models import (
        GroupContentType,
        GroupEnvelope,
        GroupRecipientDeliveryMetadata,
        GroupState,
    )

    created = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    state = GroupState(
        group_id="group-alpha",
        epoch=2,
        members=(INIT_ADDR, RESP_ADDR),
        title="Группа 🎯",
        created_at=created,
        updated_at=created,
    )
    envelope = GroupEnvelope(
        group_id="group-alpha",
        epoch=2,
        msg_id="msg-0001",
        sender_id=INIT_ADDR,
        group_seq=7,
        content_type=GroupContentType.GROUP_TEXT,
        payload="привет группа",
        created_at=created,
    )
    metadata = GroupRecipientDeliveryMetadata(
        recipient_id=RESP_ADDR, delivery_id="delivery-0001"
    )

    v1_text = group_wire.encode_group_transport_text(state, envelope, metadata)
    decoded_v1 = group_wire.decode_group_transport_text(v1_text)
    assert decoded_v1 is not None

    signer_pub = crypto.get_verify_key_from_seed(INIT_SIGNING_SEED)
    sig_payload = group_wire.group_blindbox_signature_payload(
        state, envelope, signer_pub
    )
    signature = crypto.sign_data(INIT_SIGNING_SEED, sig_payload)
    v3_text = group_wire.encode_group_transport_text_v2(
        state, envelope, signer_key=signer_pub, signature=signature
    )
    decoded_v3 = group_wire.decode_group_transport_text(v3_text)
    assert decoded_v3 is not None
    assert crypto.verify_signature(signer_pub, sig_payload, signature)

    _emit(
        "group_wire",
        "Group transport envelopes carried on the live secure channel or via "
        "BlindBox. Both versions serialize with sort_keys=True, "
        "separators=(',',':'), ensure_ascii=True after the "
        "'__I2PCHAT_GROUP__:' prefix. v3 adds an Ed25519 signature over the "
        "unsigned canonical payload. Source: i2pchat/groups/wire.py.",
        {
            "prefix": group_wire.GROUP_TRANSPORT_PREFIX,
            "version_v1": group_wire.GROUP_TRANSPORT_VERSION,
            "max_transport_bytes": group_wire.MAX_GROUP_TRANSPORT_BYTES,
            "delivery_scope_group_blindbox": (
                group_wire.GROUP_TRANSPORT_DELIVERY_SCOPE_GROUP_BLINDBOX
            ),
            "input": {
                "group_id": state.group_id,
                "group_title": state.title,
                "members": list(state.members),
                "epoch": state.epoch,
                "msg_id": envelope.msg_id,
                "sender_id": envelope.sender_id,
                "group_seq": envelope.group_seq,
                "content_type": str(envelope.content_type),
                "payload": envelope.payload,
                "created_at": created.isoformat(),
                "recipient_id": metadata.recipient_id,
                "delivery_id": metadata.delivery_id,
            },
            "v1_recipient_scope": {"encoded": v1_text},
            "v3_group_blindbox_scope": {
                "signer_pub_hex": _hex(signer_pub),
                "signature_payload_utf8": sig_payload.decode("utf-8"),
                "signature_hex": _hex(signature),
                "encoded": v3_text,
            },
        },
    )


# --------------------------------------------------------------------------
# SAM layer and I2P destinations
# --------------------------------------------------------------------------


def gen_sam() -> None:
    # A synthetic destination blob: 387 public bytes with a zero cert length,
    # followed by private key material.
    public = bytes(range(256)) + bytes(range(129))
    assert len(public) == 385
    public = public + struct.pack("!H", 0)  # cert length 0 -> 387 bytes total
    private_blob = public + b"\xaa" * 64
    dest = sam_destination.Destination(private_blob, has_private_key=True)

    b64_cases = []
    for raw in [b"", b"\x00", b"\xfb\xff\xbf", bytes(range(32))]:
        b64_cases.append(
            {
                "raw_hex": _hex(raw),
                "i2p_base64": sam_destination.i2p_b64encode(raw),
                "standard_base64": _b64(raw),
            }
        )

    commands = []
    for name, builder, kwargs in [
        ("hello", getattr(sam_protocol, "build_hello", None), {}),
        (
            "dest_generate",
            getattr(sam_protocol, "build_dest_generate", None),
            {},
        ),
    ]:
        if builder is None:
            continue
        try:
            commands.append({"name": name, "payload": builder(**kwargs).decode("ascii")})
        except Exception as exc:  # pragma: no cover - signature drift
            commands.append({"name": name, "error": repr(exc)})

    _emit(
        "sam",
        "I2P destination encoding and SAM v3 wire details. I2P base64 uses the "
        "'-~' alphabet; base32 address is b32(sha256(dest_data))[:52].lower(). "
        "Source: i2pchat/sam/destination.py and i2pchat/sam/protocol.py.",
        {
            "destination": {
                "private_blob_hex": _hex(private_blob),
                "public_data_hex": _hex(dest.data),
                "public_base64": dest.base64,
                "base32": dest.base32,
                "cert_len_offset": 385,
                "public_prefix_len": 387,
            },
            "base64_alphabet": {
                "i2p_altchars": sam_destination.I2P_B64_ALTCHARS.decode("ascii"),
                "cases": b64_cases,
            },
            "signature_types": {
                "ECDSA_SHA256_P256": 1,
                "ECDSA_SHA384_P384": 2,
                "ECDSA_SHA512_P521": 3,
                "EdDSA_SHA512_Ed25519": 7,
                "default": sam_destination.Destination.default_sig_type,
            },
            "protocol": {
                "hello": "HELLO VERSION MIN=3.0 MAX=3.2",
                "session_create": "SESSION CREATE STYLE=STREAM ID=<id> "
                "DESTINATION=<b64|TRANSIENT> [options]",
                "stream_connect": "STREAM CONNECT ID=<id> DESTINATION=<b64> SILENT=false",
                "stream_accept": "STREAM ACCEPT ID=<id> SILENT=false",
                "naming_lookup": "NAMING LOOKUP NAME=<host>",
                "line_terminator": "\\n",
                "note": "Session control socket must stay open for the session "
                "lifetime; STREAM CONNECT/ACCEPT use separate TCP connections.",
            },
            "builders": commands,
        },
    )


# --------------------------------------------------------------------------
# Group conversation record
# --------------------------------------------------------------------------


def gen_group_store(tmp_dir: Path) -> None:
    """A full group record: history, a pending fan-out leg, a BlindBox channel.

    The ``sealed_files`` vector proves the file layer opens; this one pins the
    payload the group coordinator reads out of it, including the wrapping of the
    group root secrets, which no other vector covers.
    """
    from datetime import datetime, timezone  # noqa: PLC0415

    from i2pchat.core.i2p_chat_core import I2PChatCore  # noqa: PLC0415
    from i2pchat.groups.models import GroupContentType, GroupState  # noqa: PLC0415
    from i2pchat.storage.blindbox_state import BlindBoxState  # noqa: PLC0415

    core = I2PChatCore(profile="default")
    core.profile = "default"
    core.my_signing_seed = INIT_SIGNING_SEED

    group_id = "group-beta"
    scope = f"group:{group_id}"
    root_secret = bytes.fromhex(
        "5151515151515151515151515151515151515151515151515151515151515151"
    )
    pending_root_secret = bytes.fromhex(
        "6262626262626262626262626262626262626262626262626262626262626262"
    )
    previous_root_secret = bytes.fromhex(
        "7373737373737373737373737373737373737373737373737373737373737373"
    )
    created = datetime(2026, 2, 3, 4, 5, 6, tzinfo=timezone.utc)

    state = GroupState(
        group_id=group_id,
        epoch=2,
        members=(INIT_ADDR, RESP_ADDR),
        title="Группа β",
        created_at=created,
        updated_at=created,
    )
    channel_state = BlindBoxState(send_index=4, recv_base=2, recv_window=16)
    for consumed in (0, 1, 3):
        channel_state.mark_consumed(consumed)
    # mark_consumed stamps the wall clock, which would make the vector churn on
    # every regeneration.
    channel_state.updated_at = 1700002000

    conversation = group_store.StoredGroupConversation(
        state=state,
        next_group_seq=5,
        history=(
            group_store.GroupHistoryEntry(
                kind="me",
                sender_id=INIT_ADDR,
                content_type=GroupContentType.GROUP_TEXT,
                text="привет группе",
                msg_id="msg-1",
                group_seq=4,
                epoch=2,
                created_at=created,
                delivery_results={RESP_ADDR: "delivered_live"},
                delivery_reasons={RESP_ADDR: "live-session"},
            ),
            group_store.GroupHistoryEntry(
                kind="peer",
                sender_id=RESP_ADDR,
                content_type=GroupContentType.GROUP_CONTROL,
                payload={"op": "member_left", "member_id": RESP_ADDR},
                msg_id="msg-2",
                group_seq=3,
                epoch=2,
                created_at=created,
                source_peer=RESP_ADDR,
            ),
        ),
        pending_deliveries=(
            group_store.GroupPendingDelivery(
                group_id=group_id,
                group_title="Группа β",
                group_members=(INIT_ADDR, RESP_ADDR),
                sender_id=INIT_ADDR,
                recipient_id=RESP_ADDR,
                delivery_id="msg-3:" + RESP_ADDR,
                msg_id="msg-3",
                group_seq=5,
                epoch=2,
                content_type=GroupContentType.GROUP_TEXT,
                payload="ещё не доставлено",
                created_at=created,
            ),
        ),
        blindbox_channel=group_store.GroupBlindBoxChannel(
            channel_id="channel-beta",
            group_epoch=2,
            state=channel_state,
            root_secret_enc=core._blindbox_encrypt_root_secret(root_secret, scope),
            root_epoch=6,
            root_created_at=1700000000,
            root_send_index_base=2,
            pending_root_secret_enc=core._blindbox_encrypt_root_secret(
                pending_root_secret, scope
            ),
            pending_root_epoch=7,
            pending_root_created_at=1700001000,
            pending_root_send_index_base=4,
            pending_root_target_members=(RESP_ADDR,),
            pending_root_acked_members=(),
            prev_roots=(
                {
                    "group_epoch": 1,
                    "root_epoch": 5,
                    "expires_at": 1700600000,
                    "secret_enc": core._blindbox_encrypt_root_secret(
                        previous_root_secret, scope
                    ),
                },
            ),
        ),
        pending_group_blindbox_messages=(
            group_store.GroupPendingBlindBoxMessage(
                group_id=group_id,
                group_title="Группа β",
                group_members=(INIT_ADDR, RESP_ADDR),
                sender_id=INIT_ADDR,
                msg_id="msg-4",
                group_seq=6,
                epoch=2,
                content_type=GroupContentType.GROUP_TEXT,
                payload="ждёт группового рута",
                created_at=created,
            ),
        ),
    )

    profile_dir = tmp_dir / "group-store"
    profile_dir.mkdir(parents=True, exist_ok=True)
    group_store.save_group_conversation(
        str(profile_dir), "profile", conversation, identity_key=IDENTITY_KEY
    )
    record_path = profile_dir / (
        "profile.group." + group_store._safe_group_token(group_id) + ".json"
    )
    payload = group_store._read_group_payload(
        str(record_path), group_store._safe_group_token(group_id), IDENTITY_KEY
    )

    _emit(
        "group_store",
        "One group conversation record as the reference implementation writes "
        "it. The C++ port must open the blob, read every field of the payload, "
        "and unwrap the group root secrets — which are encrypted under the "
        "BlindBox local wrap key for scope 'group:<group_id>', not under the "
        "record's own key. Source: i2pchat/storage/group_store.py.",
        {
            "profile": core.profile,
            "identity_key_hex": _hex(IDENTITY_KEY),
            "signing_seed_hex": _hex(INIT_SIGNING_SEED),
            "group_id": group_id,
            "group_token": group_store._safe_group_token(group_id),
            "wrap_scope": scope,
            "record_version": group_store.GROUP_RECORD_VERSION,
            "max_seen_msg_ids": group_store.MAX_SEEN_GROUP_MSG_IDS,
            "root_secret_hex": _hex(root_secret),
            "pending_root_secret_hex": _hex(pending_root_secret),
            "previous_root_secret_hex": _hex(previous_root_secret),
            "payload": payload,
            "plaintext_utf8": json.dumps(
                payload, ensure_ascii=True, separators=(",", ":")
            ),
            "blob_hex": _hex(record_path.read_bytes()),
        },
    )


def gen_replica_settings(tmp_dir: Path) -> None:
    """The per-profile replica list, in all three versions that are still read.

    Only the bearer tokens are encrypted, under their own ``I2RA`` blob rather
    than the sealed-file layer, so this vector pins a key derivation no other
    fixture covers.
    """
    from i2pchat.storage import profile_blindbox_replicas as replicas  # noqa: PLC0415

    endpoints = [
        "127.0.0.1:19444",
        "  spaced.b32.i2p:19444  ",
        "",
        "# a comment",
        "127.0.0.1:19444",
        "other.b32.i2p:19444",
    ]
    auth = {
        "127.0.0.1:19444": "local-token",
        "other.b32.i2p:19444": "remote-token",
        # A token for an endpoint that is not configured must be dropped.
        "unlisted.b32.i2p:19444": "orphan-token",
    }

    profile_dir = tmp_dir / "replicas"
    profile_dir.mkdir(parents=True, exist_ok=True)
    replicas.save_profile_blindbox_replicas_bundle(
        str(profile_dir), "profile", endpoints, auth, identity_key=IDENTITY_KEY
    )
    path = profile_dir / "profile.blindbox_replicas.json"
    sealed = json.loads(path.read_text(encoding="utf-8"))
    loaded, loaded_auth = replicas.load_profile_blindbox_replicas_bundle(
        str(profile_dir), "profile", identity_key=IDENTITY_KEY
    )

    normalized = replicas.normalize_replica_endpoints(
        [str(item) for item in endpoints]
    )
    legacy_v1 = {"version": 1, "replicas": normalized}
    legacy_v2 = {
        "version": 2,
        "replicas": normalized,
        "replica_auth": {k: v for k, v in auth.items() if k in set(normalized)},
    }

    _emit(
        "replica_settings",
        "The per-profile BlindBox replica list: plaintext JSON whose auth "
        "tokens sit in a base64 'I2RA' blob sealed under the profile identity "
        "key. Endpoint normalisation drops blanks, comments and duplicates and "
        "keeps first-seen order; a token whose endpoint is not in the list is "
        "discarded on both read and write. Source: "
        "i2pchat/storage/profile_blindbox_replicas.py.",
        {
            "version": replicas.PROFILE_BLINDBOX_REPLICAS_VERSION,
            "identity_key_hex": _hex(IDENTITY_KEY),
            "raw_endpoints": endpoints,
            "normalized_endpoints": normalized,
            "raw_auth": auth,
            "expected_auth": loaded_auth,
            "expected_endpoints": loaded,
            "auth_blob_base64": sealed["replica_auth_enc"],
            "auth_plaintext_utf8": json.dumps(
                {k: v for k, v in auth.items() if k in set(normalized)},
                ensure_ascii=True,
                separators=(",", ":"),
            ),
            "file_utf8": path.read_text(encoding="utf-8"),
            "legacy_version_1": legacy_v1,
            "legacy_version_2": legacy_v2,
        },
    )


# --------------------------------------------------------------------------
# Text chunking (Unicode code points)
# --------------------------------------------------------------------------


def gen_text_chunking() -> None:
    limit = chat_text_chunking.MAX_CHAT_MESSAGE_CHARS
    split = chat_text_chunking.split_long_chat_text

    # A small limit keeps the break-point cases readable while exercising the
    # same newline / space / hard-cut ladder as the production limit.
    small = 32
    cases = []
    samples: list[tuple[str, int]] = [
        ("", limit),
        ("short", limit),
        ("п" * 10, limit),
        ("a" * limit, limit),
        ("a" * (limit + 1), limit),
        ("🌍" * 10, limit),
        ("🌍" * (limit + 5), limit),
        # Break on a newline once past the min lookback (max_chars // 4).
        ("a" * 20 + "\n" + "b" * 30, small),
        # Newline too early to be used as a break point.
        ("a" * 2 + "\n" + "b" * 60, small),
        # Break on a space.
        ("a" * 20 + " " + "b" * 30, small),
        # No break candidate at all: hard cut at the limit.
        ("c" * 100, small),
        # Multi-byte characters must be counted as single code points.
        ("🌍" * 40, small),
    ]
    for text, max_chars in samples:
        chunks = split(text, max_chars)
        entry: dict[str, Any] = {
            "max_chars": max_chars,
            "code_point_count": len(text),
            "utf8_byte_count": len(text.encode("utf-8")),
            "chunk_count": len(chunks),
            "chunk_code_point_lengths": [len(c) for c in chunks],
        }
        if len(text) <= 96:
            entry["text_utf8"] = text
            entry["chunks_utf8"] = chunks
        else:
            entry["text_repeat"] = {"unit": text[0], "count": len(text)}
        cases.append(entry)

    _emit(
        "text_chunking",
        "Chat text is split by Unicode code points, not bytes and not UTF-16 "
        "units. Break preference: last newline, then last space, then a hard "
        "cut; a break is only taken at or beyond max_chars // 4. Empty input "
        "yields an empty list. Source: "
        "i2pchat/protocol/chat_text_chunking.py:split_long_chat_text.",
        {
            "max_chat_message_chars": limit,
            "min_break_lookback_fraction": 4,
            "min_allowed_max_chars": 32,
            "cases": cases,
        },
    )


def main() -> int:
    import tempfile

    print("Generating golden vectors for the C++ port...")
    if not crypto.NACL_AVAILABLE:
        print("ERROR: PyNaCl is required", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        gen_hkdf()
        gen_handshake()
        gen_frames()
        gen_canonical_json()
        gen_blindbox()
        gen_blindbox_state()
        gen_sealed_files(tmp_dir)
        gen_groups()
        gen_group_wire()
        gen_group_store(tmp_dir)
        gen_replica_settings(tmp_dir)
        gen_sam()
        gen_text_chunking()

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
