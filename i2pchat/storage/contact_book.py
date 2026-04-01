"""
Encrypted-profile contact list (v2): MRU peers, display names, notes, last message preview.

File: ``profiles/<profile>/<profile>.contacts.json`` (v2; migrates from v1 list-of-strings).
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from i2pchat.storage.blindbox_state import atomic_write_json

logger = logging.getLogger("i2pchat.contacts")

_CONTACT_HOST_RE = re.compile(r"^[a-z2-7]{40,80}$")
MAX_CONTACTS = 500
BOOK_VERSION = 2
PREVIEW_MAX_LEN = 80


def normalize_peer_address(raw: str) -> Optional[str]:
    value = (raw or "").strip().lower()
    if not value:
        return None
    if value.endswith(".b32.i2p"):
        host = value[: -len(".b32.i2p")]
    else:
        host = value
    if not _CONTACT_HOST_RE.fullmatch(host):
        return None
    return host + ".b32.i2p"


@dataclass
class ContactRecord:
    addr: str
    display_name: str = ""
    note: str = ""
    last_preview: str = ""
    last_activity_ts: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "addr": self.addr,
            "display_name": self.display_name,
            "note": self.note,
            "last_preview": self.last_preview,
            "last_activity_ts": self.last_activity_ts,
        }

    @staticmethod
    def from_dict(d: Any) -> Optional["ContactRecord"]:
        if not isinstance(d, dict):
            return None
        addr = normalize_peer_address(str(d.get("addr", "")))
        if not addr:
            return None
        def _s(key: str) -> str:
            v = d.get(key, "")
            return v if isinstance(v, str) else str(v) if v is not None else ""

        return ContactRecord(
            addr=addr,
            display_name=_s("display_name").strip(),
            note=_s("note").strip(),
            last_preview=_s("last_preview")[:PREVIEW_MAX_LEN],
            last_activity_ts=_s("last_activity_ts"),
        )


@dataclass
class ContactBook:
    contacts: list[ContactRecord] = field(default_factory=list)
    last_active_peer: Optional[str] = None

    def peer_index(self, addr: str) -> int:
        for i, r in enumerate(self.contacts):
            if r.addr == addr:
                return i
        return -1

    def get(self, addr: str) -> Optional[ContactRecord]:
        i = self.peer_index(addr)
        return self.contacts[i] if i >= 0 else None


def _coerce_last_active(raw: Any) -> Optional[str]:
    if raw is None or raw is False:
        return None
    if not isinstance(raw, str):
        return None
    return normalize_peer_address(raw)


def parse_book_from_json(data: Any) -> ContactBook:
    if not isinstance(data, dict):
        return ContactBook()
    ver = data.get("version", 1)
    raw_contacts = data.get("contacts")
    if not isinstance(raw_contacts, list):
        return ContactBook()

    records: list[ContactRecord] = []
    seen: set[str] = set()

    if ver == 1 or all(isinstance(x, str) for x in raw_contacts):
        for item in raw_contacts:
            if not isinstance(item, str):
                continue
            a = normalize_peer_address(item)
            if not a or a in seen:
                continue
            seen.add(a)
            records.append(ContactRecord(addr=a))
    else:
        for item in raw_contacts:
            rec = ContactRecord.from_dict(item)
            if rec is None or rec.addr in seen:
                continue
            seen.add(rec.addr)
            records.append(rec)

    lap = _coerce_last_active(data.get("last_active_peer"))
    if lap is not None and lap not in seen:
        lap = None

    return ContactBook(contacts=records, last_active_peer=lap)


def book_to_json_dict(book: ContactBook) -> dict[str, Any]:
    out_contacts = [r.to_dict() for r in book.contacts[:MAX_CONTACTS]]
    lap = book.last_active_peer
    if lap is not None:
        lap = normalize_peer_address(lap)
    return {
        "version": BOOK_VERSION,
        "last_active_peer": lap,
        "contacts": out_contacts,
    }


def load_book(path: str) -> ContactBook:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return ContactBook()
    return parse_book_from_json(data)


def save_book(path: str, book: ContactBook) -> None:
    book = trim_book(book)
    try:
        atomic_write_json(path, book_to_json_dict(book))
    except Exception:
        logger.debug("failed to save contact book", exc_info=True)


def trim_book(book: ContactBook) -> ContactBook:
    if len(book.contacts) <= MAX_CONTACTS:
        return book
    return ContactBook(
        contacts=book.contacts[:MAX_CONTACTS],
        last_active_peer=book.last_active_peer,
    )


def remember_peer(book: ContactBook, addr: str) -> bool:
    """Move peer to front (MRU). Returns True if book changed."""
    addr = normalize_peer_address(addr) or ""
    if not addr:
        return False
    i = book.peer_index(addr)
    if i == 0:
        return False
    if i > 0:
        rec = book.contacts.pop(i)
        book.contacts.insert(0, rec)
        return True
    book.contacts.insert(0, ContactRecord(addr=addr))
    if len(book.contacts) > MAX_CONTACTS:
        book.contacts[:] = book.contacts[:MAX_CONTACTS]
    return True


def set_last_active_peer(book: ContactBook, addr: Optional[str]) -> bool:
    """Set last active peer (normalized). None clears. Returns True if changed."""
    if addr is None or addr == "":
        if book.last_active_peer is None:
            return False
        book.last_active_peer = None
        return True
    n = normalize_peer_address(addr)
    if not n:
        return False
    if book.last_active_peer == n:
        return False
    book.last_active_peer = n
    return True


def set_peer_profile(
    book: ContactBook,
    addr: str,
    *,
    display_name: str,
    note: str,
) -> bool:
    addr = normalize_peer_address(addr) or ""
    if not addr:
        return False
    display_name = display_name.strip()
    note = note.strip()
    rec = book.get(addr)
    if rec is None:
        remember_peer(book, addr)
        rec = book.get(addr)
        assert rec is not None
    if rec.display_name == display_name and rec.note == note:
        return False
    rec.display_name = display_name
    rec.note = note
    return True


def touch_peer_message_meta(
    book: ContactBook,
    addr: str,
    preview: str,
    ts_iso: str,
) -> bool:
    """Update last_preview / last_activity_ts for peer. Returns True if changed."""
    addr = normalize_peer_address(addr) or ""
    if not addr:
        return False
    preview = (preview or "").replace("\n", " ").strip()
    if len(preview) > PREVIEW_MAX_LEN:
        preview = preview[: PREVIEW_MAX_LEN - 1] + "…"
    ts_iso = (ts_iso or "").strip()
    rec = book.get(addr)
    if rec is None:
        remember_peer(book, addr)
        rec = book.get(addr)
        assert rec is not None
    if rec.last_preview == preview and rec.last_activity_ts == ts_iso:
        return False
    rec.last_preview = preview
    rec.last_activity_ts = ts_iso
    return True


def remove_peer(book: ContactBook, addr: str) -> bool:
    """Удалить контакт из книги. Сбрасывает last_active_peer, если он совпал. Возвращает True, если запись была."""
    n = normalize_peer_address(addr) or ""
    if not n:
        return False
    i = book.peer_index(n)
    if i < 0:
        return False
    book.contacts.pop(i)
    if book.last_active_peer == n:
        book.last_active_peer = None
    return True


def has_peer(book: ContactBook, addr: str) -> bool:
    a = normalize_peer_address(addr) or ""
    return bool(a) and book.peer_index(a) >= 0


def ordered_peer_addrs(book: ContactBook) -> list[str]:
    return [r.addr for r in book.contacts]
