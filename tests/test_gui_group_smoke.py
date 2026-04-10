from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)

from PyQt6.QtWidgets import QApplication

from i2pchat.core.i2p_chat_core import ChatMessage
from i2pchat.groups.models import (
    GroupContentType,
    GroupDeliveryStatus,
    GroupEnvelope,
    GroupMemberDeliveryResult,
    GroupSendResult,
    GroupState,
)
from i2pchat.gui.main_qt import ChatWindow, THEME_DEFAULT
from i2pchat.storage.contact_book import ContactBook, ContactRecord
from i2pchat.storage.group_store import GroupHistoryEntry, StoredGroupConversation


LOCAL_MEMBER = "llllllllllllllllllllllllllllllllllllllll.b32.i2p"
PEER_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.b32.i2p"
PEER_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.b32.i2p"


@pytest.fixture
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _conversation_items(window: ChatWindow) -> list[str]:
    texts: list[str] = []
    for row in range(window.chat_model.rowCount()):
        item = window.chat_model.item_at(row)
        if item is not None:
            texts.append(item.text)
    return texts


def _group_list_titles(window: ChatWindow) -> list[str]:
    titles: list[str] = []
    for row in range(window.groups_list.count()):
        item = window.groups_list.item(row)
        widget = window.groups_list.itemWidget(item)
        title = getattr(widget, "_full_title", "") if widget is not None else ""
        if title:
            titles.append(title)
    return titles


def _install_group_store(window: ChatWindow, store: dict[str, StoredGroupConversation]) -> None:
    window.core.list_group_states = lambda: [conversation.state for conversation in store.values()]  # type: ignore[method-assign]
    window.core.load_group = lambda group_id: store.get(group_id)  # type: ignore[method-assign]
    window.core.load_group_state = (  # type: ignore[method-assign]
        lambda group_id: store.get(group_id).state if group_id in store else None
    )


def _replace_group_conversation(
    store: dict[str, StoredGroupConversation],
    group_id: str,
    *,
    state: GroupState | None = None,
    history: tuple[GroupHistoryEntry, ...] | None = None,
    next_group_seq: int | None = None,
) -> None:
    current = store[group_id]
    store[group_id] = StoredGroupConversation(
        state=state or current.state,
        next_group_seq=current.next_group_seq if next_group_seq is None else next_group_seq,
        history=current.history if history is None else history,
        seen_msg_ids=current.seen_msg_ids,
    )


def test_create_group_from_values_creates_entry_and_opens_conversation(
    qapp: QApplication,
) -> None:
    window = ChatWindow(profile="default", theme_id=THEME_DEFAULT)
    created_at = datetime(2026, 4, 9, 12, 0, tzinfo=timezone.utc)
    store: dict[str, StoredGroupConversation] = {}

    def _create_group(*, title: str, members: list[str], group_id: str | None = None, epoch: int = 0) -> GroupState:
        state = GroupState(
            group_id=group_id or "group-1",
            epoch=epoch,
            members=(LOCAL_MEMBER, *members),
            title=title,
            created_at=created_at,
            updated_at=created_at,
        )
        store[state.group_id] = StoredGroupConversation(state=state, next_group_seq=1, history=())
        return state

    window.core.create_group = _create_group  # type: ignore[method-assign]
    _install_group_store(window, store)

    group_id = window._create_group_from_values(
        title="Study Group",
        members_text=f"{PEER_A}\n{PEER_B}",
    )

    assert group_id == "group-1"
    assert window._active_group_id == "group-1"
    assert window.groups_list.count() == 1
    assert "--- Group: Study Group ---" in _conversation_items(window)
    assert "No local group messages yet." in _conversation_items(window)


def test_open_group_loads_local_history_and_control_entries(
    qapp: QApplication,
) -> None:
    window = ChatWindow(profile="default", theme_id=THEME_DEFAULT)
    window._contact_book = ContactBook(
        contacts=[
            ContactRecord(addr=PEER_A, display_name="Alice"),
            ContactRecord(addr=PEER_B, display_name="Bob"),
        ]
    )
    created_at = datetime(2026, 4, 9, 12, 0, tzinfo=timezone.utc)
    state = GroupState(
        group_id="group-1",
        epoch=2,
        members=(LOCAL_MEMBER, PEER_A, PEER_B),
        title="Study Group",
        created_at=created_at,
        updated_at=created_at,
    )
    store = {
        state.group_id: StoredGroupConversation(
            state=state,
            next_group_seq=4,
            history=(
                GroupHistoryEntry(
                    kind="me",
                    sender_id=LOCAL_MEMBER,
                    content_type=GroupContentType.GROUP_TEXT,
                    text="hello team",
                    msg_id="msg-1",
                    created_at=created_at,
                    delivery_results={
                        PEER_A: "delivered_live",
                        PEER_B: "queued_offline",
                    },
                ),
                GroupHistoryEntry(
                    kind="peer",
                    sender_id=PEER_A,
                    content_type=GroupContentType.GROUP_TEXT,
                    text="hi back",
                    msg_id="msg-2",
                    created_at=created_at,
                ),
                GroupHistoryEntry(
                    kind="peer",
                    sender_id=PEER_A,
                    content_type=GroupContentType.GROUP_CONTROL,
                    payload={"title": "Renamed Study Group"},
                    msg_id="msg-3",
                    created_at=created_at,
                ),
            ),
        )
    }
    _install_group_store(window, store)

    assert window._set_active_group("group-1") is True

    texts = _conversation_items(window)
    assert "--- Group: Study Group ---" in texts
    assert "hello team" in texts
    assert "Delivery: 1 live, 1 queued" in texts
    assert "Alice: hi back" in texts
    assert 'Alice updated group settings: title "Renamed Study Group"' in texts


def test_group_send_flow_routes_via_core_and_appends_delivery_summary(
    qapp: QApplication,
) -> None:
    window = ChatWindow(profile="default", theme_id=THEME_DEFAULT)
    created_at = datetime(2026, 4, 9, 12, 0, tzinfo=timezone.utc)
    state = GroupState(
        group_id="group-1",
        epoch=1,
        members=(LOCAL_MEMBER, PEER_A, PEER_B),
        title="Study Group",
        created_at=created_at,
        updated_at=created_at,
    )
    store = {
        state.group_id: StoredGroupConversation(state=state, next_group_seq=1, history=())
    }
    _install_group_store(window, store)
    window._set_active_group("group-1")
    window._peer_chat_is_foreground = lambda: True  # type: ignore[method-assign]

    async def _send_group_text(group_id: str, text: str) -> GroupSendResult:
        _replace_group_conversation(
            store,
            group_id,
            history=store[group_id].history
            + (
                GroupHistoryEntry(
                    kind="me",
                    sender_id=LOCAL_MEMBER,
                    content_type=GroupContentType.GROUP_TEXT,
                    text=text,
                    msg_id="msg-1",
                    created_at=created_at,
                    delivery_results={
                        PEER_A: "delivered_live",
                        PEER_B: "queued_offline",
                    },
                ),
            ),
            next_group_seq=2,
        )
        window.handle_message(
            ChatMessage(
                kind="me",
                text="[Group Study Group] hello group",
                timestamp=created_at,
                message_id="msg-1",
                conversation_kind="group",
                conversation_id=group_id,
                conversation_title="Study Group",
                group_sender_id=LOCAL_MEMBER,
                group_content_type="GROUP_TEXT",
                group_plain_text=text,
            )
        )
        return GroupSendResult(
            envelope=GroupEnvelope(
                group_id=group_id,
                epoch=1,
                msg_id="msg-1",
                sender_id=LOCAL_MEMBER,
                group_seq=1,
                content_type=GroupContentType.GROUP_TEXT,
                payload=text,
                created_at=created_at,
            ),
            delivery_results={
                PEER_A: GroupMemberDeliveryResult(
                    recipient_id=PEER_A,
                    status=GroupDeliveryStatus.DELIVERED_LIVE,
                ),
                PEER_B: GroupMemberDeliveryResult(
                    recipient_id=PEER_B,
                    status=GroupDeliveryStatus.QUEUED_OFFLINE,
                ),
            },
        )

    window.core.send_group_text = AsyncMock(side_effect=_send_group_text)  # type: ignore[method-assign]
    window.input_edit.setPlainTextForCompose("hello group")

    asyncio.run(window._send_text_ui_flow("hello group"))

    window.core.send_group_text.assert_awaited_once_with("group-1", "hello group")  # type: ignore[attr-defined]
    assert window.input_edit.plainTextForSend() == ""
    texts = _conversation_items(window)
    assert "hello group" in texts
    assert "Delivery: 1 live, 1 queued" in texts
    assert "No local group messages yet." not in texts
    assert texts[-1] == "--- end of group history ---"


def test_imported_group_message_is_visible_in_the_active_group_conversation(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = ChatWindow(profile="default", theme_id=THEME_DEFAULT)
    window._contact_book = ContactBook(
        contacts=[ContactRecord(addr=PEER_A, display_name="Alice")]
    )
    created_at = datetime(2026, 4, 9, 12, 0, tzinfo=timezone.utc)
    state = GroupState(
        group_id="group-1",
        epoch=1,
        members=(LOCAL_MEMBER, PEER_A),
        title="Study Group",
        created_at=created_at,
        updated_at=created_at,
    )
    store = {
        state.group_id: StoredGroupConversation(state=state, next_group_seq=1, history=())
    }
    _install_group_store(window, store)
    window._set_active_group("group-1")
    monkeypatch.setattr(window, "_peer_chat_is_foreground", lambda: True)
    _replace_group_conversation(
        store,
        "group-1",
        history=(
            GroupHistoryEntry(
                kind="peer",
                sender_id=PEER_A,
                content_type=GroupContentType.GROUP_TEXT,
                text="hello from the group",
                msg_id="msg-2",
                created_at=created_at,
            ),
        ),
        next_group_seq=2,
    )

    window.handle_message(
        ChatMessage(
            kind="peer",
            text="[Group Study Group] alice: hello from the group",
            timestamp=created_at,
            source_peer=PEER_A,
            message_id="msg-2",
            conversation_kind="group",
            conversation_id="group-1",
            conversation_title="Study Group",
            group_sender_id=PEER_A,
            group_content_type="GROUP_TEXT",
            group_plain_text="hello from the group",
        )
    )

    assert _conversation_items(window) == [
        "--- Group: Study Group ---",
        "Alice: hello from the group",
        "--- end of group history ---",
    ]


def test_active_group_control_refresh_updates_title_and_readable_text(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = ChatWindow(profile="default", theme_id=THEME_DEFAULT)
    window._contact_book = ContactBook(
        contacts=[ContactRecord(addr=PEER_A, display_name="Alice")]
    )
    created_at = datetime(2026, 4, 9, 12, 0, tzinfo=timezone.utc)
    state = GroupState(
        group_id="group-1",
        epoch=1,
        members=(LOCAL_MEMBER, PEER_A),
        title="Study Group",
        created_at=created_at,
        updated_at=created_at,
    )
    store = {
        state.group_id: StoredGroupConversation(state=state, next_group_seq=1, history=())
    }
    _install_group_store(window, store)
    window._set_active_group("group-1")
    monkeypatch.setattr(window, "_peer_chat_is_foreground", lambda: True)

    renamed_state = replace(
        state,
        title="Renamed Study Group",
        epoch=2,
        updated_at=created_at,
    )
    _replace_group_conversation(
        store,
        "group-1",
        state=renamed_state,
        history=(
            GroupHistoryEntry(
                kind="peer",
                sender_id=PEER_A,
                content_type=GroupContentType.GROUP_CONTROL,
                payload={"title": "Renamed Study Group", "epoch": 2},
                msg_id="msg-3",
                created_at=created_at,
            ),
        ),
    )

    window.handle_message(
        ChatMessage(
            kind="system",
            text='Alice updated group settings: title "Renamed Study Group", epoch 2',
            timestamp=created_at,
            source_peer=PEER_A,
            message_id="msg-3",
            conversation_kind="group",
            conversation_id="group-1",
            conversation_title="Renamed Study Group",
            group_sender_id=PEER_A,
            group_content_type="GROUP_CONTROL",
            group_plain_text='Alice updated group settings: title "Renamed Study Group", epoch 2',
        )
    )

    assert _conversation_items(window) == [
        "--- Group: Renamed Study Group ---",
        'Alice updated group settings: title "Renamed Study Group", epoch 2',
        "--- end of group history ---",
    ]
    assert _group_list_titles(window) == ["Renamed Study Group"]


def test_switch_profile_resets_active_group_state_and_reloads_group_list(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = ChatWindow(profile="default", theme_id=THEME_DEFAULT)
    created_at = datetime(2026, 4, 9, 12, 0, tzinfo=timezone.utc)
    current_state = GroupState(
        group_id="group-old",
        epoch=1,
        members=(LOCAL_MEMBER, PEER_A),
        title="Current Group",
        created_at=created_at,
        updated_at=created_at,
    )
    current_store = {
        current_state.group_id: StoredGroupConversation(
            state=current_state,
            next_group_seq=1,
            history=(),
        )
    }
    _install_group_store(window, current_store)
    window._refresh_groups_list()
    window._set_active_group("group-old")

    next_state = GroupState(
        group_id="group-new",
        epoch=1,
        members=(LOCAL_MEMBER, PEER_B),
        title="Fresh Group",
        created_at=created_at,
        updated_at=created_at,
    )
    next_store = {
        next_state.group_id: StoredGroupConversation(
            state=next_state,
            next_group_seq=1,
            history=(),
        )
    }

    class _SwitchCore:
        def __init__(self, store: dict[str, StoredGroupConversation]) -> None:
            self.store = store
            self.stored_peer = ""
            self.current_peer_addr = ""

        async def init_session(self) -> None:
            return None

        def list_group_states(self) -> list[GroupState]:
            return [conversation.state for conversation in self.store.values()]

        def load_group(self, group_id: str) -> StoredGroupConversation | None:
            return self.store.get(group_id)

        def load_group_state(self, group_id: str) -> GroupState | None:
            conversation = self.store.get(group_id)
            return conversation.state if conversation is not None else None

    window.core.shutdown = AsyncMock()  # type: ignore[method-assign]
    monkeypatch.setattr(
        window,
        "_ensure_router_backend_ready",
        AsyncMock(return_value=("127.0.0.1", 7656)),
    )
    monkeypatch.setattr(
        window,
        "_create_core",
        lambda profile, sam_address: _SwitchCore(next_store),
    )
    monkeypatch.setattr(
        window,
        "_load_contacts_book",
        lambda: setattr(window, "_contact_book", ContactBook()),
    )
    monkeypatch.setattr(window, "_ensure_stored_peer_in_contact_book", lambda: None)
    monkeypatch.setattr(window, "_refresh_contacts_list", lambda: None)
    monkeypatch.setattr(window, "_apply_contacts_sidebar_startup_state", lambda: None)
    monkeypatch.setattr(window, "_sync_contacts_list_selection", lambda: None)
    monkeypatch.setattr(window, "_update_peer_lock_indicator", lambda: None)
    monkeypatch.setattr(window, "_deferred_saved_peers_refresh_after_switch", lambda: None)
    monkeypatch.setattr(window, "_balance_contacts_splitter_initial", lambda: None)
    monkeypatch.setattr(window, "_refresh_offline_history_display", lambda *args, **kwargs: None)
    monkeypatch.setattr(window, "refresh_status_label", lambda: None)
    monkeypatch.setattr(window, "_refresh_connection_buttons", lambda: None)

    asyncio.run(window.switch_profile("other-profile"))

    assert window.profile == "other-profile"
    assert window._active_group_id is None
    assert window._loaded_group_history_id is None
    assert _conversation_items(window) == []
    assert _group_list_titles(window) == ["Fresh Group"]
    assert window.groups_list.selectedItems() == []


def test_direct_peer_message_rendering_stays_on_the_direct_chat_path(
    qapp: QApplication,
) -> None:
    window = ChatWindow(profile="default", theme_id=THEME_DEFAULT)
    created_at = datetime(2026, 4, 9, 12, 0, tzinfo=timezone.utc)

    window.handle_message(
        ChatMessage(
            kind="peer",
            text="direct hello",
            timestamp=created_at,
            source_peer=PEER_A,
        )
    )

    texts = _conversation_items(window)
    assert texts[-1] == "direct hello"
    assert window._active_group_id is None
    assert not any(text.startswith("--- Group:") for text in texts)


def test_direct_peer_message_unread_behavior_still_uses_peer_keys_when_group_is_active(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = ChatWindow(profile="default", theme_id=THEME_DEFAULT)
    base = window._window_title_base
    created_at = datetime.now(timezone.utc)
    state = GroupState(
        group_id="group-1",
        epoch=1,
        members=(LOCAL_MEMBER, PEER_A),
        title="Study Group",
        created_at=created_at,
        updated_at=created_at,
    )
    _install_group_store(
        window,
        {
            state.group_id: StoredGroupConversation(
                state=state,
                next_group_seq=1,
                history=(),
            )
        },
    )
    window._set_active_group("group-1")
    monkeypatch.setattr(window, "_peer_chat_is_foreground", lambda: False)

    window.handle_message(
        ChatMessage(kind="peer", text="direct hello", timestamp=created_at, source_peer=PEER_B)
    )

    assert window.windowTitle() == f"{base} (1)"
