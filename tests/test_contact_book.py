"""Unit tests for contact_book (v2 format, v1 migration)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import i2pchat.storage.contact_book as cb

# Canonical bare base32 (no .b32.i2p); legacy full form for migration/input tests.
PEER_A_BARE = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
PEER_B_BARE = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
PEER_A_LEGACY = f"{PEER_A_BARE}.b32.i2p"
PEER_B_LEGACY = f"{PEER_B_BARE}.b32.i2p"


def test_normalize_peer_address() -> None:
    assert cb.normalize_peer_address("") is None
    assert cb.normalize_peer_address(PEER_A_LEGACY) == PEER_A_BARE
    assert cb.normalize_peer_address(PEER_A_LEGACY.upper()) == PEER_A_BARE
    assert cb.normalize_peer_address(PEER_A_BARE) == PEER_A_BARE


def test_parse_v1_list_of_strings() -> None:
    data = {"version": 1, "contacts": [PEER_B_LEGACY, PEER_A_LEGACY]}
    book = cb.parse_book_from_json(data)
    assert [r.addr for r in book.contacts] == [PEER_B_BARE, PEER_A_BARE]
    assert book.last_active_peer is None


def test_parse_v1_implicit_no_version() -> None:
    data = {"contacts": [PEER_A_LEGACY]}
    book = cb.parse_book_from_json(data)
    assert len(book.contacts) == 1
    assert book.contacts[0].addr == PEER_A_BARE


def test_parse_v2_full() -> None:
    data = {
        "version": 2,
        "last_active_peer": PEER_B_LEGACY,
        "contacts": [
            {
                "addr": PEER_A_LEGACY,
                "display_name": "Alice",
                "note": "work",
                "last_preview": "hi",
                "last_activity_ts": "2026-01-01T12:00:00+00:00",
            },
            {"addr": PEER_B_BARE},
        ],
    }
    book = cb.parse_book_from_json(data)
    assert book.last_active_peer == PEER_B_BARE
    assert book.contacts[0].display_name == "Alice"
    assert book.contacts[0].note == "work"
    assert book.contacts[0].last_preview == "hi"


def test_last_active_ignored_if_not_in_contacts() -> None:
    data = {
        "version": 2,
        "last_active_peer": PEER_B_LEGACY,
        "contacts": [{"addr": PEER_A_LEGACY}],
    }
    book = cb.parse_book_from_json(data)
    assert book.last_active_peer is None


def test_remember_peer_mru() -> None:
    book = cb.ContactBook(
        contacts=[
            cb.ContactRecord(addr=PEER_A_BARE),
            cb.ContactRecord(addr=PEER_B_BARE),
        ]
    )
    assert cb.remember_peer(book, PEER_B_BARE) is True
    assert book.contacts[0].addr == PEER_B_BARE
    assert cb.remember_peer(book, PEER_B_BARE) is False


def test_touch_message_meta_inserts_peer() -> None:
    book = cb.ContactBook()
    assert (
        cb.touch_peer_message_meta(book, PEER_A_LEGACY, "hello", "2026-03-01T00:00:00Z")
        is True
    )
    assert book.contacts[0].addr == PEER_A_BARE
    assert book.contacts[0].last_preview == "hello"


def test_roundtrip_file() -> None:
    with tempfile.TemporaryDirectory() as td:
        path = str(Path(td) / "x.contacts.json")
        book = cb.ContactBook(
            contacts=[
                cb.ContactRecord(
                    addr=PEER_A_BARE,
                    display_name="A",
                    note="n",
                    last_preview="m",
                    last_activity_ts="t",
                )
            ],
            last_active_peer=PEER_A_BARE,
        )
        cb.save_book(path, book)
        loaded = cb.load_book(path)
        assert loaded.last_active_peer == PEER_A_BARE
        assert len(loaded.contacts) == 1
        assert loaded.contacts[0].display_name == "A"


def test_remove_peer() -> None:
    book = cb.ContactBook(
        contacts=[
            cb.ContactRecord(addr=PEER_A_BARE, display_name="A"),
            cb.ContactRecord(addr=PEER_B_BARE),
        ],
        last_active_peer=PEER_B_BARE,
    )
    assert cb.remove_peer(book, PEER_A_LEGACY) is True
    assert len(book.contacts) == 1
    assert book.contacts[0].addr == PEER_B_BARE
    assert book.last_active_peer == PEER_B_BARE
    assert cb.remove_peer(book, PEER_A_LEGACY) is False


def test_remove_peer_clears_last_active() -> None:
    book = cb.ContactBook(
        contacts=[cb.ContactRecord(addr=PEER_A_BARE)], last_active_peer=PEER_A_BARE
    )
    assert cb.remove_peer(book, PEER_A_LEGACY) is True
    assert book.contacts == []
    assert book.last_active_peer is None


def test_remove_peer_idempotent_unknown() -> None:
    book = cb.ContactBook(contacts=[cb.ContactRecord(addr=PEER_A_BARE)])
    assert cb.remove_peer(book, PEER_B_LEGACY) is False
    assert len(book.contacts) == 1


def test_save_migrates_from_v1_file() -> None:
    with tempfile.TemporaryDirectory() as td:
        path = str(Path(td) / "p.contacts.json")
        Path(path).write_text(
            json.dumps({"version": 1, "contacts": [PEER_A_LEGACY]}),
            encoding="utf-8",
        )
        book = cb.load_book(path)
        cb.save_book(path, book)
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        assert raw["version"] == 2
        assert isinstance(raw["contacts"][0], dict)
        assert raw["contacts"][0]["addr"] == PEER_A_BARE
