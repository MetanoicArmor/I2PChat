"""Cross-implementation storage tests: Python 1.4.x profiles against the C++ port.

The Phase 3 exit criterion is that the C++ client opens a profile written by the
Python one and the other way round. These tests drive the Python side through the
production storage modules and the C++ side through the ``interop_storage``
helper binary, so every byte of the sealed formats — magic, header version, salt,
the two-stage HKDF domains and the ASCII-escaped compact JSON inside — is
exercised by both implementations against the same files on disk.

Skipped unless the C++ ``interop_storage`` binary has been built.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Optional

import pytest

pytest.importorskip("nacl", reason="PyNaCl is required for at-rest encryption")

from i2pchat.storage import (  # noqa: E402
    chat_history,
    compose_drafts_store,
    contact_book,
    group_store,
    profile_dat,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

IDENTITY_KEY = bytes(range(32))
IDENTITY_HEX = IDENTITY_KEY.hex()
PROFILE = "interop"
ALICE = "aaaabbbbccccddddeeeeffffgggghhhhiiiijjjjkkkkllllmmmm"
BOB = "nnnnooooppppqqqqrrrrssssttttuuuuvvvvwwwwxxxxyyyyzzzz"


def _find_tool() -> Optional[Path]:
    for candidate in (
        REPO_ROOT / "cpp" / "build" / "tools" / "interop_storage",
        REPO_ROOT / "cpp" / "build" / "debug" / "tools" / "interop_storage",
        REPO_ROOT / "cpp" / "build" / "release" / "tools" / "interop_storage",
    ):
        if candidate.exists():
            return candidate
    return None


INTEROP_STORAGE = _find_tool()

pytestmark = pytest.mark.skipif(
    INTEROP_STORAGE is None,
    reason="cpp/build/tools/interop_storage not built; run cmake --build cpp/build",
)


def _run(*args: str, payload: Any = None) -> str:
    """Invoke the C++ helper, failing the test with its stderr on error."""
    completed = subprocess.run(
        [str(INTEROP_STORAGE), *args],
        input=json.dumps(payload) if payload is not None else None,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, (
        f"interop_storage {args[0]} failed with {completed.returncode}: "
        f"{completed.stderr.strip()}"
    )
    return completed.stdout


def _read(command: str, directory: Path, *extra: str) -> Any:
    return json.loads(_run(command, str(directory), PROFILE, IDENTITY_HEX, *extra))


def _write(command: str, directory: Path, payload: Any, *extra: str) -> None:
    _run(command, str(directory), PROFILE, IDENTITY_HEX, *extra, payload=payload)


# --------------------------------------------------------------------------- #
# Contacts
# --------------------------------------------------------------------------- #


def test_cpp_reads_a_python_contact_book(tmp_path: Path) -> None:
    book = contact_book.ContactBook()
    contact_book.remember_peer(book, ALICE)
    contact_book.remember_peer(book, BOB)
    # Non-ASCII and an emoji, because the payload is ASCII-escaped on disk and a
    # mistake in the escaping would only show up on characters like these.
    contact_book.set_peer_profile(book, BOB, display_name="Тест 🙂", note="заметка")
    contact_book.touch_peer_message_meta(book, BOB, "привет", "2026-01-01T00:00:00+00:00")
    contact_book.set_last_active_peer(book, BOB)
    contact_book.save_book(
        str(tmp_path / f"{PROFILE}.contacts.json"), book, identity_key=IDENTITY_KEY
    )

    result = _read("read-contacts", tmp_path)

    assert [c["addr"] for c in result["contacts"]] == [BOB, ALICE]
    assert result["last_active_peer"] == BOB
    bob = result["contacts"][0]
    assert bob["display_name"] == "Тест 🙂"
    assert bob["note"] == "заметка"
    assert bob["last_preview"] == "привет"
    assert bob["last_activity_ts"] == "2026-01-01T00:00:00+00:00"


def test_python_reads_a_cpp_contact_book(tmp_path: Path) -> None:
    _write(
        "write-contacts",
        tmp_path,
        {
            "contacts": [
                {
                    "addr": BOB,
                    "display_name": "Боб",
                    "note": "",
                    "last_preview": "как дела",
                    "last_activity_ts": "2026-02-02T12:00:00+00:00",
                },
                {"addr": ALICE, "display_name": "", "note": "", "last_preview": ""},
            ],
            "last_active_peer": ALICE,
        },
    )

    book = contact_book.load_book(
        str(tmp_path / f"{PROFILE}.contacts.json"), identity_key=IDENTITY_KEY
    )

    assert contact_book.ordered_peer_addrs(book) == [BOB, ALICE]
    assert book.last_active_peer == ALICE
    record = book.get(BOB)
    assert record is not None
    assert record.display_name == "Боб"
    assert record.last_preview == "как дела"
    assert record.last_activity_ts == "2026-02-02T12:00:00+00:00"


def test_the_contacts_file_is_sealed_not_plaintext(tmp_path: Path) -> None:
    # If either side ever wrote plaintext the tests above would still pass, so
    # check the magic explicitly.
    _write("write-contacts", tmp_path, {"contacts": [{"addr": BOB}]})
    raw = (tmp_path / f"{PROFILE}.contacts.json").read_bytes()
    assert raw[:4] == contact_book.CONTACTS_STORE_MAGIC
    assert BOB.encode("ascii") not in raw


def test_a_python_contact_book_written_by_cpp_stays_readable_by_cpp(
    tmp_path: Path,
) -> None:
    # A save from each side in turn, to catch a salt or header that only survives
    # one round.
    _write("write-contacts", tmp_path, {"contacts": [{"addr": BOB}]})
    path = str(tmp_path / f"{PROFILE}.contacts.json")

    book = contact_book.load_book(path, identity_key=IDENTITY_KEY)
    contact_book.remember_peer(book, ALICE)
    contact_book.save_book(path, book, identity_key=IDENTITY_KEY)

    result = _read("read-contacts", tmp_path)
    assert [c["addr"] for c in result["contacts"]] == [ALICE, BOB]


# --------------------------------------------------------------------------- #
# Compose drafts
# --------------------------------------------------------------------------- #


def test_cpp_reads_python_drafts(tmp_path: Path) -> None:
    drafts = {BOB: "недописанное", "group-7": "draft for the group"}
    compose_drafts_store.save_compose_drafts(
        str(tmp_path / f"{PROFILE}.compose_drafts.json"),
        drafts,
        identity_key=IDENTITY_KEY,
    )
    assert _read("read-drafts", tmp_path) == drafts


def test_python_reads_cpp_drafts(tmp_path: Path) -> None:
    drafts = {ALICE: "line one\nline two", "group-7": "🙂"}
    _write("write-drafts", tmp_path, drafts)

    assert (
        compose_drafts_store.load_compose_drafts(
            str(tmp_path / f"{PROFILE}.compose_drafts.json"), identity_key=IDENTITY_KEY
        )
        == drafts
    )


# --------------------------------------------------------------------------- #
# Chat history
# --------------------------------------------------------------------------- #


def test_cpp_reads_python_history(tmp_path: Path) -> None:
    entries = [
        chat_history.HistoryEntry(kind="out", text="привет", ts="2026-01-01T00:00:00+00:00"),
        chat_history.HistoryEntry(
            kind="in",
            text="hello",
            ts="2026-01-01T00:00:01+00:00",
            message_id="abc123",
            delivery_state="delivered",
            delivery_route="direct",
            delivery_hint="hint",
            delivery_reason="reason",
            retryable=True,
        ),
    ]
    chat_history.save_history(str(tmp_path), PROFILE, BOB, entries, IDENTITY_KEY)

    result = _read("read-history", tmp_path, BOB)

    assert [e["text"] for e in result] == ["привет", "hello"]
    assert result[1]["message_id"] == "abc123"
    assert result[1]["delivery_state"] == "delivered"
    assert result[1]["delivery_route"] == "direct"
    assert result[1]["delivery_hint"] == "hint"
    assert result[1]["delivery_reason"] == "reason"
    assert result[1]["retryable"] is True
    # An unset field must not come back as the string "None".
    assert result[0]["message_id"] is None


def test_python_reads_cpp_history(tmp_path: Path) -> None:
    _write(
        "write-history",
        tmp_path,
        [
            {"kind": "out", "text": "первое", "ts": "2026-01-01T00:00:00+00:00"},
            {
                "kind": "in",
                "text": "второе",
                "ts": "2026-01-01T00:00:01+00:00",
                "message_id": "id-1",
                "delivery_state": "sent",
                "retryable": True,
            },
        ],
        BOB,
    )

    entries = chat_history.load_history(str(tmp_path), PROFILE, BOB, IDENTITY_KEY)

    assert [e.text for e in entries] == ["первое", "второе"]
    assert entries[1].message_id == "id-1"
    assert entries[1].delivery_state == "sent"
    assert entries[1].retryable is True


def test_both_sides_agree_on_the_history_file_name(tmp_path: Path) -> None:
    # The file is named after the digest of the address, so a difference in how
    # the address is normalised means each side writes a file the other never
    # looks at — a silent, total loss of history.
    _write("write-history", tmp_path, [{"kind": "in", "text": "x", "ts": ""}], BOB)
    expected = tmp_path / f"{PROFILE}.history.{chat_history._safe_peer_id(BOB)}.enc"
    assert expected.exists()

    suffixed = f"{BOB}.b32.i2p"
    _write("write-history", tmp_path, [{"kind": "in", "text": "y", "ts": ""}], suffixed)
    expected_suffixed = (
        tmp_path / f"{PROFILE}.history.{chat_history._safe_peer_id(suffixed)}.enc"
    )
    assert expected_suffixed.exists()
    assert expected_suffixed != expected


def test_cpp_reads_a_legacy_short_named_history_file(tmp_path: Path) -> None:
    entries = [chat_history.HistoryEntry(kind="in", text="старое", ts="2026-01-01T00:00:00+00:00")]
    chat_history.save_history(str(tmp_path), PROFILE, BOB, entries, IDENTITY_KEY)

    current = tmp_path / f"{PROFILE}.history.{chat_history._safe_peer_id(BOB)}.enc"
    legacy = tmp_path / f"{PROFILE}.history.{chat_history._legacy_safe_peer_id(BOB)}.enc"
    current.rename(legacy)

    assert [e["text"] for e in _read("read-history", tmp_path, BOB)] == ["старое"]


def test_history_written_by_cpp_keeps_pythons_retention_marker(tmp_path: Path) -> None:
    # 1001 messages, one over the default limit, so the oldest is dropped and the
    # payload records where the record now begins.
    payload = [
        {"kind": "in", "text": str(i), "ts": f"2026-01-01T00:00:{i % 60:02d}+00:00"}
        for i in range(1001)
    ]
    _write("write-history", tmp_path, payload, BOB)

    entries = chat_history.load_history(str(tmp_path), PROFILE, BOB, IDENTITY_KEY)
    assert len(entries) == 1000
    assert entries[0].text == "1"


# --------------------------------------------------------------------------- #
# Group records
# --------------------------------------------------------------------------- #


def test_cpp_reads_a_python_group_record(tmp_path: Path) -> None:
    group_id = "group-alpha"
    payload = {
        "version": 1,
        "state": {
            "group_id": group_id,
            "title": "Группа",
            "epoch": 1,
            "members": [ALICE, BOB],
        },
        "next_group_seq": 3,
        "history": [],
        "seen_msg_ids": [],
        "pending_deliveries": [],
    }
    path = tmp_path / f"{PROFILE}.group.{group_store._safe_group_token(group_id)}.json"
    group_store._write_group_payload(
        str(path), payload, group_store._safe_group_token(group_id), IDENTITY_KEY
    )

    assert _read("read-group", tmp_path, group_id) == payload


def test_python_reads_a_cpp_group_record(tmp_path: Path) -> None:
    group_id = "группа-7"
    payload = {
        "version": 1,
        "state": {"group_id": group_id, "title": "Тест", "epoch": 2, "members": [BOB]},
        "next_group_seq": 1,
    }
    _write("write-group", tmp_path, payload, group_id)

    path = tmp_path / f"{PROFILE}.group.{group_store._safe_group_token(group_id)}.json"
    assert path.exists()
    assert (
        group_store._read_group_payload(
            str(path), group_store._safe_group_token(group_id), IDENTITY_KEY
        )
        == payload
    )


# --------------------------------------------------------------------------- #
# Identity .dat
# --------------------------------------------------------------------------- #


def test_cpp_opens_a_python_written_dat(tmp_path: Path) -> None:
    key = base64.b64encode(bytes(range(64))).decode("ascii")
    wrap_key = bytes(range(31, -1, -1))
    (tmp_path / f"{PROFILE}.dat.wrap").write_text(
        base64.b64encode(wrap_key).decode("ascii") + "\n", encoding="ascii"
    )
    (tmp_path / f"{PROFILE}.dat").write_bytes(
        profile_dat.encrypt_profile_dat(key, wrap_key)
    )

    result = _read("read-dat", tmp_path)
    assert result["private_key_base64"] == key
    assert result["was_plaintext"] is False


def test_python_opens_a_cpp_written_dat(tmp_path: Path) -> None:
    key = base64.b64encode(bytes(range(64))).decode("ascii")
    _run("write-dat", str(tmp_path), PROFILE, key)

    sidecar = tmp_path / f"{PROFILE}.dat.wrap"
    assert sidecar.exists(), "the C++ side must leave a sidecar when the keyring is off"
    wrap_key = base64.b64decode(sidecar.read_text(encoding="ascii").strip())

    raw = (tmp_path / f"{PROFILE}.dat").read_bytes()
    assert raw[:4] == profile_dat.PROFILE_DAT_MAGIC
    assert profile_dat.decrypt_profile_dat(raw, wrap_key) == key


def test_cpp_reads_a_legacy_plaintext_dat(tmp_path: Path) -> None:
    key = base64.b64encode(bytes(range(64))).decode("ascii")
    (tmp_path / f"{PROFILE}.dat").write_text(f"{key}\n{BOB}\n", encoding="ascii")

    result = _read("read-dat", tmp_path)
    assert result["private_key_base64"] == key
    assert result["was_plaintext"] is True


def test_the_dat_sidecar_permissions_are_tight(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("POSIX permissions do not apply on Windows")
    _run("write-dat", str(tmp_path), PROFILE, "AAECAwQFBgcICQoLDA0ODw==")
    for name in (f"{PROFILE}.dat", f"{PROFILE}.dat.wrap"):
        mode = (tmp_path / name).stat().st_mode & 0o777
        assert mode == 0o600, f"{name} is {oct(mode)}"
