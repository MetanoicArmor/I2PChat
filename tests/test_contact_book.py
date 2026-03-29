"""Unit tests for contact_book (v2 format, v1 migration)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import contact_book as cb


PEER_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.b32.i2p"
PEER_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.b32.i2p"


def test_normalize_peer_address() -> None:
    assert cb.normalize_peer_address("") is None
    assert cb.normalize_peer_address(PEER_A) == PEER_A
    assert cb.normalize_peer_address(PEER_A.upper()) == PEER_A
    assert cb.normalize_peer_address(PEER_A.replace(".b32.i2p", "")) == PEER_A


def test_parse_v1_list_of_strings() -> None:
    data = {"version": 1, "contacts": [PEER_B, PEER_A]}
    book = cb.parse_book_from_json(data)
    assert [r.addr for r in book.contacts] == [PEER_B, PEER_A]
    assert book.last_active_peer is None


def test_parse_v1_implicit_no_version() -> None:
    data = {"contacts": [PEER_A]}
    book = cb.parse_book_from_json(data)
    assert len(book.contacts) == 1
    assert book.contacts[0].addr == PEER_A


def test_parse_v2_full() -> None:
    data = {
        "version": 2,
        "last_active_peer": PEER_B,
        "contacts": [
            {
                "addr": PEER_A,
                "display_name": "Alice",
                "note": "work",
                "last_preview": "hi",
                "last_activity_ts": "2026-01-01T12:00:00+00:00",
            },
            {"addr": PEER_B},
        ],
    }
    book = cb.parse_book_from_json(data)
    assert book.last_active_peer == PEER_B
    assert book.contacts[0].display_name == "Alice"
    assert book.contacts[0].note == "work"
    assert book.contacts[0].last_preview == "hi"


def test_last_active_ignored_if_not_in_contacts() -> None:
    data = {"version": 2, "last_active_peer": PEER_B, "contacts": [{"addr": PEER_A}]}
    book = cb.parse_book_from_json(data)
    assert book.last_active_peer is None


def test_remember_peer_mru() -> None:
    book = cb.ContactBook(contacts=[cb.ContactRecord(addr=PEER_A), cb.ContactRecord(addr=PEER_B)])
    assert cb.remember_peer(book, PEER_B) is True
    assert book.contacts[0].addr == PEER_B
    assert cb.remember_peer(book, PEER_B) is False


def test_touch_message_meta_inserts_peer() -> None:
    book = cb.ContactBook()
    assert cb.touch_peer_message_meta(book, PEER_A, "hello", "2026-03-01T00:00:00Z") is True
    assert book.contacts[0].addr == PEER_A
    assert book.contacts[0].last_preview == "hello"


def test_roundtrip_file() -> None:
    with tempfile.TemporaryDirectory() as td:
        path = str(Path(td) / "x.contacts.json")
        book = cb.ContactBook(
            contacts=[
                cb.ContactRecord(
                    addr=PEER_A,
                    display_name="A",
                    note="n",
                    last_preview="m",
                    last_activity_ts="t",
                )
            ],
            last_active_peer=PEER_A,
        )
        cb.save_book(path, book)
        loaded = cb.load_book(path)
        assert loaded.last_active_peer == PEER_A
        assert len(loaded.contacts) == 1
        assert loaded.contacts[0].display_name == "A"


def test_save_migrates_from_v1_file() -> None:
    with tempfile.TemporaryDirectory() as td:
        path = str(Path(td) / "p.contacts.json")
        Path(path).write_text(
            json.dumps({"version": 1, "contacts": [PEER_A]}),
            encoding="utf-8",
        )
        book = cb.load_book(path)
        cb.save_book(path, book)
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        assert raw["version"] == 2
        assert isinstance(raw["contacts"][0], dict)
        assert raw["contacts"][0]["addr"] == PEER_A
