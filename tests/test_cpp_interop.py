"""Cross-implementation wire protocol tests: Python 1.4.x against the C++ port.

The Phase 2 exit criterion is that the two implementations complete a handshake
and exchange text in both roles. Doing that through a live I2P router is not
something CI can rely on, so these tests replace the I2P stream with a loopback
TCP socket and drive the Python side using the production ``i2pchat.crypto`` and
``i2pchat.protocol.protocol_codec`` modules.

Everything that a real SAM run would validate at the byte level is covered here:
the identity preface, the HS3-labelled signed transcript, HS4 key derivation,
directional keys, FINISHED confirmation, frame framing, the sequence counter,
the MAC over the header fields, and the I2PPAD1 padding envelope. What is *not*
covered is SAM itself and the router, which is why the manual interop run stays
on the release checklist.

Skipped unless the C++ ``interop_peer`` binary has been built.
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import struct
import subprocess
import time
from pathlib import Path
from typing import Optional, Tuple

import pytest

from i2pchat import crypto
from i2pchat.protocol.protocol_codec import (
    FLAG_ENCRYPTED,
    MAGIC,
    PROTOCOL_VERSION,
)

pytest.importorskip("nacl", reason="PyNaCl is required for the secure protocol")

REPO_ROOT = Path(__file__).resolve().parents[1]
PADDING_MAGIC = b"I2PPAD1"
PADDING_BLOCK = 128


def _find_interop_peer() -> Optional[Path]:
    for candidate in (
        REPO_ROOT / "cpp" / "build" / "tools" / "interop_peer",
        REPO_ROOT / "cpp" / "build" / "debug" / "tools" / "interop_peer",
        REPO_ROOT / "cpp" / "build" / "release" / "tools" / "interop_peer",
    ):
        if candidate.exists():
            return candidate
    return None


INTEROP_PEER = _find_interop_peer()

pytestmark = pytest.mark.skipif(
    INTEROP_PEER is None,
    reason="cpp/build/tools/interop_peer not built; run cmake --build cpp/build",
)


# --------------------------------------------------------------------------- #
# Destinations
# --------------------------------------------------------------------------- #


def _i2p_b64_encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii").replace("+", "-").replace("/", "~")


def _make_destination(filler: int) -> Tuple[bytes, str, str]:
    """A syntactically valid public destination and its base32 address.

    Only the byte layout matters: the session layer derives the address by
    hashing the destination and never interprets the key material.
    """
    data = bytes((filler + i) % 256 for i in range(385)) + b"\x00\x00"
    base64_text = _i2p_b64_encode(data)
    digest = hashlib.sha256(data).digest()
    base32 = base64.b32encode(digest).decode("ascii").lower().rstrip("=")[:52]
    return data, base64_text, base32


# --------------------------------------------------------------------------- #
# Framing, mirroring what the C++ codec does
# --------------------------------------------------------------------------- #


def _encode_frame(msg_type: str, payload: bytes, msg_id: int = 0, flags: int = 0) -> bytes:
    return (
        MAGIC
        + bytes([PROTOCOL_VERSION, ord(msg_type), flags])
        + struct.pack(">Q", msg_id)
        + struct.pack(">I", len(payload))
        + payload
    )


class FrameStream:
    """Incremental frame decoder over a blocking socket."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._buffer = bytearray()

    def _fill(self) -> None:
        chunk = self._sock.recv(65536)
        if not chunk:
            raise AssertionError("peer closed the connection")
        self._buffer.extend(chunk)

    def read_line(self) -> str:
        while b"\n" not in self._buffer:
            self._fill()
        index = self._buffer.index(b"\n")
        line = bytes(self._buffer[:index])
        del self._buffer[: index + 1]
        return line.decode("utf-8").strip()

    def read_frame(self) -> Tuple[str, int, int, bytes]:
        while True:
            if len(self._buffer) >= 19:
                assert bytes(self._buffer[:4]) == MAGIC, "frame magic mismatch"
                version = self._buffer[4]
                assert version == PROTOCOL_VERSION, f"unexpected version {version}"
                msg_type = chr(self._buffer[5])
                flags = self._buffer[6]
                msg_id = struct.unpack(">Q", bytes(self._buffer[7:15]))[0]
                length = struct.unpack(">I", bytes(self._buffer[15:19]))[0]
                if len(self._buffer) >= 19 + length:
                    payload = bytes(self._buffer[19 : 19 + length])
                    del self._buffer[: 19 + length]
                    return msg_type, flags, msg_id, payload
            self._fill()


# --------------------------------------------------------------------------- #
# Padding
# --------------------------------------------------------------------------- #


def _apply_padding(body: bytes) -> bytes:
    wrapped = PADDING_MAGIC + struct.pack(">I", len(body)) + body
    target = ((len(wrapped) + PADDING_BLOCK - 1) // PADDING_BLOCK) * PADDING_BLOCK
    return wrapped + os.urandom(target - len(wrapped))


def _remove_padding(payload: bytes) -> bytes:
    if not payload.startswith(PADDING_MAGIC):
        return payload
    original_len = struct.unpack(">I", payload[7:11])[0]
    return payload[11 : 11 + original_len]


# --------------------------------------------------------------------------- #
# Signed transcripts. Note the HS3 label: the transcript uses HS3 while the KDF
# uses HS4, and that inconsistency is part of the deployed protocol.
# --------------------------------------------------------------------------- #


def _init_sig_payload(signer: str, remote: str, nonce_hex: str, eph_hex: str,
                      sign_hex: str) -> bytes:
    return f"I2PCHAT-HS3|INIT|{signer}|{remote}|{nonce_hex}|{eph_hex}|{sign_hex}".encode()


def _resp_sig_payload(signer: str, remote: str, init_nonce: str, init_eph: str,
                      init_sign: str, resp_nonce: str, resp_eph: str,
                      resp_sign: str) -> bytes:
    return (
        f"I2PCHAT-HS3|RESP|{signer}|{remote}|{init_nonce}|{init_eph}|{init_sign}"
        f"|{resp_nonce}|{resp_eph}|{resp_sign}"
    ).encode()


class PythonPeer:
    """The Python end of the conversation, built from production crypto calls."""

    def __init__(self, sock: socket.socket, local_addr: str, local_dest_b64: str,
                 seed: bytes, peer_addr: str = "") -> None:
        self.sock = sock
        self.stream = FrameStream(sock)
        self.local_addr = local_addr
        self.local_dest_b64 = local_dest_b64
        self.seed = seed
        self.sign_pub = crypto.get_verify_key_from_seed(seed)
        self.peer_addr = peer_addr
        self.send_seq = 0
        self.recv_seq = 0
        self.send_enc = b""
        self.send_mac = b""
        self.recv_enc = b""
        self.recv_mac = b""

    # -- handshake ------------------------------------------------------- #

    def send_preface(self, *, with_line: bool) -> None:
        if with_line:
            self.sock.sendall(self.local_dest_b64.encode() + b"\n")
        self.sock.sendall(_encode_frame("S", self.local_dest_b64.encode()))

    def send_init(self) -> None:
        self.nonce = crypto.generate_nonce()
        self.eph_priv, self.eph_pub = crypto.generate_ephemeral_keypair()
        nonce_hex, eph_hex = self.nonce.hex(), self.eph_pub.hex()
        sign_hex = self.sign_pub.hex()
        signature = crypto.sign_data(
            self.seed,
            _init_sig_payload(self.local_addr, self.peer_addr, nonce_hex, eph_hex,
                              sign_hex),
        )
        body = f"INIT:{nonce_hex}:{eph_hex}:{sign_hex}:{signature.hex()}"
        self.sock.sendall(_encode_frame("H", body.encode()))
        self.init_fields = (nonce_hex, eph_hex, sign_hex)

    def handle_resp(self, body: str) -> None:
        assert body.startswith("RESP:"), f"expected RESP, got {body[:16]!r}"
        nonce_hex, eph_hex, sign_hex, sig_hex = body[5:].split(":")
        init_nonce, init_eph, init_sign = self.init_fields

        payload = _resp_sig_payload(self.peer_addr, self.local_addr, init_nonce,
                                    init_eph, init_sign, nonce_hex, eph_hex, sign_hex)
        assert crypto.verify_signature(
            bytes.fromhex(sign_hex), payload, bytes.fromhex(sig_hex)
        ), "C++ RESP signature did not verify against the Python implementation"

        shared = crypto.compute_dh_shared_secret(self.eph_priv, bytes.fromhex(eph_hex))
        enc_i2r, mac_i2r, enc_r2i, mac_r2i = crypto.derive_handshake_subkeys(
            shared, bytes.fromhex(init_nonce), bytes.fromhex(nonce_hex)
        )
        # Python is the initiator here, so its send direction is i2r.
        self.send_enc, self.send_mac = enc_i2r, mac_i2r
        self.recv_enc, self.recv_mac = enc_r2i, mac_r2i
        self.transcript = crypto.compute_handshake_transcript_hash(payload)

    def send_finished(self) -> None:
        tag = crypto.compute_handshake_finished(self.send_mac, self.transcript)
        self.sock.sendall(_encode_frame("H", f"FINISHED:{tag.hex()}".encode()))

    def verify_finished(self, body: str) -> None:
        assert body.startswith("FINISHED:"), f"expected FINISHED, got {body[:16]!r}"
        assert crypto.verify_handshake_finished(
            self.recv_mac, self.transcript, bytes.fromhex(body[9:])
        ), "C++ key confirmation did not verify against the Python implementation"

    def handle_init(self, body: str) -> str:
        """Respond to a C++ INIT and return the RESP body we sent."""
        assert body.startswith("INIT:"), f"expected INIT, got {body[:16]!r}"
        init_nonce, init_eph, init_sign, init_sig = body[5:].split(":")

        payload = _init_sig_payload(self.peer_addr, self.local_addr, init_nonce,
                                    init_eph, init_sign)
        assert crypto.verify_signature(
            bytes.fromhex(init_sign), payload, bytes.fromhex(init_sig)
        ), "C++ INIT signature did not verify against the Python implementation"

        self.nonce = crypto.generate_nonce()
        self.eph_priv, self.eph_pub = crypto.generate_ephemeral_keypair()
        resp_nonce, resp_eph = self.nonce.hex(), self.eph_pub.hex()
        resp_sign = self.sign_pub.hex()

        resp_payload = _resp_sig_payload(self.local_addr, self.peer_addr, init_nonce,
                                         init_eph, init_sign, resp_nonce, resp_eph,
                                         resp_sign)
        signature = crypto.sign_data(self.seed, resp_payload)

        shared = crypto.compute_dh_shared_secret(self.eph_priv, bytes.fromhex(init_eph))
        enc_i2r, mac_i2r, enc_r2i, mac_r2i = crypto.derive_handshake_subkeys(
            shared, bytes.fromhex(init_nonce), bytes.fromhex(resp_nonce)
        )
        # Python is the responder here, so its send direction is r2i.
        self.send_enc, self.send_mac = enc_r2i, mac_r2i
        self.recv_enc, self.recv_mac = enc_i2r, mac_i2r
        self.transcript = crypto.compute_handshake_transcript_hash(resp_payload)

        resp_body = f"RESP:{resp_nonce}:{resp_eph}:{resp_sign}:{signature.hex()}"
        self.sock.sendall(_encode_frame("H", resp_body.encode()))
        return resp_body

    # -- secure channel -------------------------------------------------- #

    def send_encrypted(self, msg_type: str, plaintext: bytes, msg_id: int = 1) -> None:
        self.send_seq += 1
        sealed = crypto.encrypt_message(self.send_enc, _apply_padding(plaintext))
        mac = crypto.compute_mac(
            self.send_mac, msg_type, sealed, seq=self.send_seq, msg_id=msg_id,
            flags=FLAG_ENCRYPTED,
        )
        payload = struct.pack(">Q", self.send_seq) + sealed + mac
        self.sock.sendall(_encode_frame(msg_type, payload, msg_id, FLAG_ENCRYPTED))

    def read_encrypted(self) -> Tuple[str, int, bytes]:
        msg_type, flags, msg_id, payload = self.stream.read_frame()
        assert flags & FLAG_ENCRYPTED, f"C++ sent a plaintext {msg_type} frame"

        seq = struct.unpack(">Q", payload[:8])[0]
        sealed = payload[8 : -crypto.HMAC_SIZE]
        mac = payload[-crypto.HMAC_SIZE :]

        assert crypto.verify_mac(
            self.recv_mac, msg_type, sealed, mac, seq=seq, msg_id=msg_id, flags=flags
        ), "C++ frame MAC did not verify against the Python implementation"
        assert seq == self.recv_seq + 1, f"seq {seq}, expected {self.recv_seq + 1}"
        self.recv_seq = seq

        decrypted = crypto.decrypt_message(self.recv_enc, sealed)
        assert decrypted is not None, "could not decrypt a C++ frame"
        return msg_type, msg_id, _remove_padding(decrypted)

    def read_handshake_body(self) -> str:
        msg_type, flags, _msg_id, payload = self.stream.read_frame()
        assert msg_type == "H", f"expected an H frame, got {msg_type}"
        assert not flags & FLAG_ENCRYPTED
        return payload.decode()


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _spawn_peer(role: str, port: int, local_dest: str, seed: bytes,
                peer_dest: str = "") -> subprocess.Popen:
    command = [
        str(INTEROP_PEER),
        "--role", role,
        "--port", str(port),
        "--local-dest", local_dest,
        "--seed", seed.hex(),
    ]
    if peer_dest:
        command += ["--peer-dest", peer_dest]
    return subprocess.Popen(command, stderr=subprocess.PIPE)


def _wait_for_exit(process: subprocess.Popen, expected_code: int) -> str:
    try:
        _stdout, stderr = process.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        raise AssertionError("the C++ peer did not exit")
    diagnostics = stderr.decode(errors="replace")
    assert process.returncode == expected_code, (
        f"the C++ peer exited with {process.returncode}, expected {expected_code}: "
        + diagnostics
    )
    return diagnostics


def _connect(port: int) -> socket.socket:
    """Connect to the C++ peer, which may still be binding its listener."""
    for _attempt in range(200):
        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=5)
            sock.settimeout(15)
            return sock
        except OSError:
            time.sleep(0.05)
    raise AssertionError("the C++ peer never started listening")


def _drive_python_initiator(sock: socket.socket, py_addr: str, py_b64: str,
                            py_seed: bytes, cpp_addr: str, cpp_b64: str) -> "PythonPeer":
    """Complete a handshake with a C++ peer that is accepting the connection."""
    peer = PythonPeer(sock, py_addr, py_b64, py_seed, peer_addr=cpp_addr)
    peer.send_preface(with_line=True)
    peer.send_init()

    msg_type, _flags, _msg_id, payload = peer.stream.read_frame()
    assert msg_type == "S"
    assert payload.decode() == cpp_b64

    peer.handle_resp(peer.read_handshake_body())
    peer.send_finished()
    peer.verify_finished(peer.read_handshake_body())
    return peer


@pytest.fixture
def identities():
    _a_data, a_b64, a_addr = _make_destination(7)
    _b_data, b_b64, b_addr = _make_destination(101)
    return (a_b64, a_addr), (b_b64, b_addr)


def test_python_initiator_against_cpp_responder(identities):
    """Python dials, C++ accepts: the inbound half of the Phase 2 gate."""
    (py_b64, py_addr), (cpp_b64, cpp_addr) = identities
    port = _free_port()
    process = _spawn_peer("inbound", port, cpp_b64, os.urandom(32))
    try:
        with _connect(port) as sock:
            peer = _drive_python_initiator(
                sock, py_addr, py_b64, os.urandom(32), cpp_addr, cpp_b64
            )
            peer.send_encrypted("U", "привет из Python 🌍".encode())
            reply_type, _reply_id, reply = peer.read_encrypted()
            assert reply_type == "U"
            assert reply.decode() == "echo:привет из Python 🌍"
        _wait_for_exit(process, 0)
    finally:
        if process.poll() is None:
            process.kill()


def test_cpp_initiator_against_python_responder(identities):
    """C++ dials, Python accepts: the outbound half of the Phase 2 gate."""
    (py_b64, py_addr), (cpp_b64, cpp_addr) = identities
    port = _free_port()

    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(1)
    listener.settimeout(15)

    process = _spawn_peer("outbound", port, cpp_b64, os.urandom(32), peer_dest=py_b64)
    try:
        sock, _addr = listener.accept()
        with sock:
            sock.settimeout(15)
            peer = PythonPeer(sock, py_addr, py_b64, os.urandom(32), peer_addr=cpp_addr)

            # The caller announces itself with a bare line, then an S frame.
            assert peer.stream.read_line() == cpp_b64
            msg_type, _flags, _msg_id, payload = peer.stream.read_frame()
            assert msg_type == "S"
            assert payload.decode() == cpp_b64

            peer.send_preface(with_line=False)
            peer.handle_init(peer.read_handshake_body())
            peer.send_finished()
            peer.verify_finished(peer.read_handshake_body())

            peer.send_encrypted("U", b"hello from Python")
            reply_type, _reply_id, reply = peer.read_encrypted()
            assert reply_type == "U"
            assert reply.decode() == "echo:hello from Python"
        _wait_for_exit(process, 0)
    finally:
        listener.close()
        if process.poll() is None:
            process.kill()


def test_many_messages_keep_the_sequence_in_step(identities):
    """A long exchange must not drift: every frame is seq = previous + 1."""
    (py_b64, py_addr), (cpp_b64, cpp_addr) = identities
    port = _free_port()
    process = _spawn_peer("inbound", port, cpp_b64, os.urandom(32))
    try:
        with _connect(port) as sock:
            peer = _drive_python_initiator(
                sock, py_addr, py_b64, os.urandom(32), cpp_addr, cpp_b64
            )
            for index in range(25):
                peer.send_encrypted("U", f"message {index}".encode(), msg_id=index)
                _reply_type, reply_id, reply = peer.read_encrypted()
                assert reply_id == index
                assert reply.decode() == f"echo:message {index}"
            assert peer.send_seq == 25
            assert peer.recv_seq == 25
        _wait_for_exit(process, 0)
    finally:
        if process.poll() is None:
            process.kill()


def test_a_long_message_survives_the_padding_envelope(identities):
    """Payloads larger than one padding block must round trip unchanged."""
    (py_b64, py_addr), (cpp_b64, cpp_addr) = identities
    port = _free_port()
    process = _spawn_peer("inbound", port, cpp_b64, os.urandom(32))
    try:
        with _connect(port) as sock:
            peer = _drive_python_initiator(
                sock, py_addr, py_b64, os.urandom(32), cpp_addr, cpp_b64
            )
            text = "ю" * 5000
            peer.send_encrypted("U", text.encode())
            _reply_type, _reply_id, reply = peer.read_encrypted()
            assert reply.decode() == "echo:" + text
        _wait_for_exit(process, 0)
    finally:
        if process.poll() is None:
            process.kill()


def test_cpp_rejects_a_replayed_frame(identities):
    """A frame replayed at the C++ peer must tear the session down."""
    (py_b64, py_addr), (cpp_b64, cpp_addr) = identities
    port = _free_port()
    process = _spawn_peer("inbound", port, cpp_b64, os.urandom(32))
    try:
        with _connect(port) as sock:
            peer = _drive_python_initiator(
                sock, py_addr, py_b64, os.urandom(32), cpp_addr, cpp_b64
            )

            peer.send_seq += 1
            sealed = crypto.encrypt_message(peer.send_enc, _apply_padding(b"once"))
            mac = crypto.compute_mac(peer.send_mac, "U", sealed, seq=peer.send_seq,
                                     msg_id=1, flags=FLAG_ENCRYPTED)
            frame = _encode_frame(
                "U", struct.pack(">Q", peer.send_seq) + sealed + mac, 1, FLAG_ENCRYPTED
            )
            sock.sendall(frame)
            peer.read_encrypted()  # the echo of the legitimate frame
            sock.sendall(frame)

        diagnostics = _wait_for_exit(process, 3)
        assert "Replay protection triggered" in diagnostics
    finally:
        if process.poll() is None:
            process.kill()


def test_cpp_rejects_plaintext_after_handshake(identities):
    """Clearing the encrypted flag must not downgrade an established channel."""
    (py_b64, py_addr), (cpp_b64, cpp_addr) = identities
    port = _free_port()
    process = _spawn_peer("inbound", port, cpp_b64, os.urandom(32))
    try:
        with _connect(port) as sock:
            peer = _drive_python_initiator(
                sock, py_addr, py_b64, os.urandom(32), cpp_addr, cpp_b64
            )
            del peer
            sock.sendall(_encode_frame("U", b"cleartext", 1, 0))

        diagnostics = _wait_for_exit(process, 3)
        assert "Plaintext application frame" in diagnostics
    finally:
        if process.poll() is None:
            process.kill()


def test_cpp_rejects_a_tampered_mac(identities):
    """Flipping a ciphertext bit must fail the MAC, not merely fail to decrypt."""
    (py_b64, py_addr), (cpp_b64, cpp_addr) = identities
    port = _free_port()
    process = _spawn_peer("inbound", port, cpp_b64, os.urandom(32))
    try:
        with _connect(port) as sock:
            peer = _drive_python_initiator(
                sock, py_addr, py_b64, os.urandom(32), cpp_addr, cpp_b64
            )

            peer.send_seq += 1
            sealed = crypto.encrypt_message(peer.send_enc, _apply_padding(b"tampered"))
            mac = crypto.compute_mac(peer.send_mac, "U", sealed, seq=peer.send_seq,
                                     msg_id=1, flags=FLAG_ENCRYPTED)
            corrupted = bytearray(sealed)
            corrupted[-1] ^= 0x01
            sock.sendall(
                _encode_frame(
                    "U",
                    struct.pack(">Q", peer.send_seq) + bytes(corrupted) + mac,
                    1,
                    FLAG_ENCRYPTED,
                )
            )

        diagnostics = _wait_for_exit(process, 3)
        assert "MAC verification failed" in diagnostics
    finally:
        if process.poll() is None:
            process.kill()


def test_cpp_rejects_data_before_the_handshake(identities):
    """Application data must not be accepted before there is a channel."""
    (py_b64, _py_addr), (cpp_b64, _cpp_addr) = identities
    port = _free_port()
    process = _spawn_peer("inbound", port, cpp_b64, os.urandom(32))
    try:
        with _connect(port) as sock:
            sock.sendall(py_b64.encode() + b"\n")
            sock.sendall(_encode_frame("U", b"too early", 1, 0))

        diagnostics = _wait_for_exit(process, 3)
        assert "data before secure handshake" in diagnostics
    finally:
        if process.poll() is None:
            process.kill()


def test_cpp_rejects_a_forged_handshake_signature(identities):
    """An INIT signed with the wrong key must not establish a session."""
    (py_b64, py_addr), (cpp_b64, cpp_addr) = identities
    port = _free_port()
    process = _spawn_peer("inbound", port, cpp_b64, os.urandom(32))
    try:
        with _connect(port) as sock:
            peer = PythonPeer(sock, py_addr, py_b64, os.urandom(32), peer_addr=cpp_addr)
            peer.send_preface(with_line=True)

            # Sign the transcript with a key other than the one announced.
            nonce = crypto.generate_nonce()
            _eph_priv, eph_pub = crypto.generate_ephemeral_keypair()
            wrong_seed = os.urandom(32)
            signature = crypto.sign_data(
                wrong_seed,
                _init_sig_payload(py_addr, cpp_addr, nonce.hex(), eph_pub.hex(),
                                  peer.sign_pub.hex()),
            )
            body = (
                f"INIT:{nonce.hex()}:{eph_pub.hex()}:{peer.sign_pub.hex()}:"
                f"{signature.hex()}"
            )
            sock.sendall(_encode_frame("H", body.encode()))

        diagnostics = _wait_for_exit(process, 3)
        assert "signature verification failed" in diagnostics
    finally:
        if process.poll() is None:
            process.kill()
