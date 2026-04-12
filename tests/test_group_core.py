from __future__ import annotations

import json
import os
import asyncio
import sys
import tempfile
import types
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

if "PIL" not in sys.modules:
    pil_module = types.ModuleType("PIL")
    pil_image_module = types.ModuleType("PIL.Image")
    pil_image_module.Image = object  # type: ignore[attr-defined]
    pil_module.Image = pil_image_module  # type: ignore[attr-defined]
    sys.modules["PIL"] = pil_module
    sys.modules["PIL.Image"] = pil_image_module

from i2pchat import crypto
from i2pchat.core.i2p_chat_core import I2PChatCore, _BlindBoxPeerSnapshot
from i2pchat.blindbox.blindbox_blob import encrypt_blindbox_blob
from i2pchat.blindbox.blindbox_key_schedule import (
    derive_blindbox_message_keys,
    derive_group_blindbox_message_keys,
)

from tests.live_session_helpers import attach_mock_live_session
from i2pchat.groups import (
    GroupContentType,
    GroupDeliveryStatus,
    GroupEnvelope,
    GroupImportStatus,
    GroupRecipientDeliveryMetadata,
    GroupState,
    GroupTransportOutcome,
)
from i2pchat.groups.wire import (
    encode_group_transport_text,
    encode_group_transport_text_v2,
)
from i2pchat.storage.blindbox_state import BlindBoxState
from i2pchat.storage.group_store import GroupBlindBoxChannel

ALICE_BARE = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
BOB_BARE = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
CAROL_BARE = "cccccccccccccccccccccccccccccccccccccccc"


class _DummyDest:
    def __init__(self, base32: str) -> None:
        self.base32 = base32


class _DummyWriter:
    def __init__(self) -> None:
        self.frames: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.frames.append(data)

    async def drain(self) -> None:
        return None


class _FakeBlindBoxPollClient:
    def __init__(self, blobs_by_token: dict[str, list[bytes]]) -> None:
        self._blobs_by_token = {
            token: list(blobs)
            for token, blobs in blobs_by_token.items()
        }

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def is_runtime_ready(self) -> bool:
        return True

    async def get_first_accepted(
        self,
        lookup_token: str,
        *,
        accept_blob,
        miss_grace_sec: float,
        diag: dict[str, object] | None = None,
    ) -> bytes | None:
        del miss_grace_sec
        if diag is not None:
            diag["first_result_addr"] = "fake-replica"
        for blob in self._blobs_by_token.get(lookup_token, []):
            if await accept_blob(blob):
                if diag is not None:
                    diag["accepted_addr"] = "fake-replica"
                return blob
        return None


class _RetrySlotBlindBoxClient:
    def __init__(self) -> None:
        self.calls = 0
        self.tokens: list[str] = []

    def is_runtime_ready(self) -> bool:
        return True

    async def put(self, key: str, blob: bytes) -> list[object]:
        del blob
        self.calls += 1
        self.tokens.append(key)
        if self.calls == 1:
            raise RuntimeError(
                "Blind Box PUT quorum not reached: 0/1 (first failure: PUT EXISTS verification mismatch)"
            )
        return []


class _FailingBlindBoxRuntimeClient:
    def __init__(self, detail: str) -> None:
        self.detail = detail
        self.start_calls = 0
        self.close_calls = 0
        self.put_calls = 0

    async def start(self) -> None:
        self.start_calls += 1
        raise RuntimeError(self.detail)

    async def close(self) -> None:
        self.close_calls += 1

    def is_runtime_ready(self) -> bool:
        return False

    async def put(self, key: str, blob: bytes) -> list[object]:
        del key, blob
        self.put_calls += 1
        raise AssertionError("put() should not be reached while runtime is unavailable")


class GroupCoreTests(unittest.IsolatedAsyncioTestCase):
    def test_core_group_workflow_load_and_save_exposes_state_history_and_next_seq(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            core = I2PChatCore(profile="alice")
            core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
            core.my_dest = _DummyDest(ALICE_BARE)

            created_state = core.create_group(
                title="Workflow group",
                members=[BOB_BARE],
                group_id="core-group-workflow",
                epoch=2,
            )
            created = core.load_group(created_state.group_id)

            assert created is not None
            self.assertEqual(created.state.group_id, "core-group-workflow")
            self.assertEqual(created.state.title, "Workflow group")
            self.assertEqual(created.state.epoch, 2)
            self.assertEqual(created.next_group_seq, 1)
            self.assertEqual(created.history, ())

            updated_state = GroupState(
                group_id=created_state.group_id,
                epoch=4,
                members=(ALICE_BARE, BOB_BARE, CAROL_BARE),
                title="Workflow group renamed",
            )

            saved = core.save_group(updated_state, next_group_seq=5)
            reloaded = core.load_group(created_state.group_id)

            assert reloaded is not None
            self.assertEqual(saved.state.title, "Workflow group renamed")
            self.assertEqual(saved.state.epoch, 4)
            self.assertEqual(saved.state.members, (ALICE_BARE, BOB_BARE, CAROL_BARE))
            self.assertEqual(saved.next_group_seq, 5)
            self.assertEqual(reloaded.state.title, "Workflow group renamed")
            self.assertEqual(reloaded.state.epoch, 4)
            self.assertEqual(reloaded.state.members, (ALICE_BARE, BOB_BARE, CAROL_BARE))
            self.assertEqual(reloaded.next_group_seq, 5)
            self.assertEqual(reloaded.history, ())

    def test_delete_group_removes_file_and_forgets_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            core = I2PChatCore(profile="alice")
            core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
            core.my_dest = _DummyDest(ALICE_BARE)

            created_state = core.create_group(
                title="To delete",
                members=[BOB_BARE],
                group_id="core-group-delete-me",
                epoch=1,
            )
            core.group_manager.prime_group_sequence(
                created_state.group_id,
                next_group_seq=10,
            )
            self.assertTrue(core.delete_group(created_state.group_id))
            self.assertIsNone(core.load_group_state(created_state.group_id))
            self.assertNotIn(
                created_state.group_id,
                core.group_manager._group_seq_by_id,
            )
            self.assertFalse(core.delete_group(created_state.group_id))

    def test_get_group_send_ui_hints_reflects_pairwise_blindbox_readiness(self) -> None:
        """Group Send hints should track pairwise BlindBox roots for offline members."""
        with tempfile.TemporaryDirectory() as tmpdir:
            core = I2PChatCore(profile="alice")
            core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
            core._profile_scoped_path = lambda filename: os.path.join(  # type: ignore[method-assign]
                tmpdir, filename
            )
            core.my_dest = _DummyDest(ALICE_BARE)
            core.my_signing_seed, core.my_signing_public = crypto.generate_signing_keypair()
            core.create_group(title="g", members=[BOB_BARE], group_id="g-ui-hints", epoch=1)
            core.get_delivery_telemetry = lambda: {  # type: ignore[method-assign]
                "blindbox_enabled": True,
                "blindbox_ready": True,
                "blindbox_runtime_ready": True,
            }

            def _no_live(*_a: object, **_k: object) -> bool:
                return False

            core.session_manager.is_live_path_alive = _no_live  # type: ignore[method-assign]
            h = core.get_group_send_ui_hints("g-ui-hints")
            self.assertTrue(h["can_send"])
            self.assertTrue(h["show_offline_button"])
            self.assertFalse(h["any_live_to_member"])
            self.assertEqual(h.get("live_by_recipient"), {BOB_BARE: False})
            self.assertEqual(h.get("blindbox_ready_by_recipient"), {BOB_BARE: False})
            self.assertFalse(h["group_blindbox_ready"])
            self.assertTrue(h["await_group_root"])
            self.assertEqual(h["reason"], "await-group-root")

            core._save_blindbox_peer_snapshot(
                _BlindBoxPeerSnapshot(
                    peer_addr=BOB_BARE,
                    peer_id=BOB_BARE,
                    state=BlindBoxState(send_index=0),
                    root_secret=b"b" * 32,
                    root_epoch=1,
                )
            )
            h_ready = core.get_group_send_ui_hints("g-ui-hints")
            self.assertEqual(h_ready.get("blindbox_ready_by_recipient"), {BOB_BARE: True})
            self.assertTrue(h_ready["group_blindbox_ready"])
            self.assertFalse(h_ready["await_group_root"])
            self.assertEqual(h_ready["reason"], "ok")

            def _bob_live(*_a: object, peer_id: str | None = None, **_k: object) -> bool:
                return peer_id == BOB_BARE

            core.session_manager.is_live_path_alive = _bob_live  # type: ignore[method-assign]
            h2 = core.get_group_send_ui_hints("g-ui-hints")
            self.assertTrue(h2["can_send"])
            self.assertFalse(h2["show_offline_button"])
            self.assertTrue(h2["any_live_to_member"])
            self.assertEqual(h2.get("live_by_recipient"), {BOB_BARE: True})

    def test_update_group_bumps_epoch_preserves_next_seq_and_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            core = I2PChatCore(profile="alice")
            core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
            core.my_dest = _DummyDest(ALICE_BARE)
            created = core.create_group(
                title="Before",
                members=[BOB_BARE],
                group_id="core-group-update",
                epoch=5,
            )
            self.assertEqual(created.epoch, 5)
            conv = core.load_group(created.group_id)
            assert conv is not None
            self.assertEqual(conv.next_group_seq, 1)

            updated = core.update_group(
                created.group_id,
                title="After",
                members=[BOB_BARE, CAROL_BARE],
            )
            self.assertEqual(updated.title, "After")
            self.assertEqual(updated.epoch, 6)
            self.assertGreaterEqual(len(updated.members), 3)

            conv2 = core.load_group(created.group_id)
            assert conv2 is not None
            self.assertEqual(conv2.next_group_seq, 1)
            self.assertEqual(conv2.history, ())

    def test_save_group_state_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            core = I2PChatCore(profile="alice")
            core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
            core.my_dest = _DummyDest(ALICE_BARE)
            state = GroupState(
                group_id="core-group-save",
                epoch=11,
                members=(ALICE_BARE, BOB_BARE),
                title="Saved title",
            )

            saved = core.save_group_state(state, next_group_seq=9)
            loaded = core.load_group_state("core-group-save")
            history = core.load_group_history("core-group-save")

            assert loaded is not None
            self.assertEqual(saved.group_id, "core-group-save")
            self.assertEqual(loaded.title, "Saved title")
            self.assertEqual(loaded.epoch, 11)
            self.assertEqual(history, [])

    def test_new_group_starts_with_empty_history_until_messages_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            core = I2PChatCore(profile="alice")
            core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
            core.my_dest = _DummyDest(ALICE_BARE)

            group_state = core.create_group(
                title="Fresh group",
                members=[BOB_BARE],
                group_id="core-group-empty",
                epoch=1,
            )

            self.assertEqual(group_state.group_id, "core-group-empty")
            self.assertEqual(core.load_group_history(group_state.group_id), [])

    async def test_send_group_text_from_core_routes_live_and_pairwise_blindbox(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            core = I2PChatCore(profile="alice")
            core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
            core.my_dest = _DummyDest(ALICE_BARE)
            core.session_manager.set_peer_handshake_complete(BOB_BARE)
            group_state = core.create_group(
                title="Core group",
                members=[BOB_BARE, CAROL_BARE],
                group_id="core-group-1",
                epoch=7,
            )
            core._send_group_envelope_live = AsyncMock(  # type: ignore[method-assign]
                return_value=GroupTransportOutcome(
                    accepted=True,
                    reason="live-session",
                    transport_message_id="live-bob",
                )
            )
            core._send_group_envelope_via_blindbox = AsyncMock(  # type: ignore[method-assign]
                return_value=GroupTransportOutcome(
                    accepted=True,
                    reason="blindbox-ready",
                    transport_message_id="queue-offline",
                )
            )
            core._send_group_envelope_via_group_blindbox = AsyncMock()  # type: ignore[method-assign]

            result = await core.send_group_text(group_state.group_id, "hello group")
            history = core.load_group_history(group_state.group_id)
            reloaded_state = core.load_group_state(group_state.group_id)

            assert reloaded_state is not None
            self.assertEqual(result.envelope.epoch, 7)
            self.assertEqual(
                result.delivery_results[BOB_BARE].status,
                GroupDeliveryStatus.DELIVERED_LIVE,
            )
            self.assertEqual(
                result.delivery_results[CAROL_BARE].status,
                GroupDeliveryStatus.QUEUED_OFFLINE,
            )
            core._send_group_envelope_via_blindbox.assert_awaited_once()
            core._send_group_envelope_via_group_blindbox.assert_not_awaited()
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0].text, "hello group")
            self.assertEqual(history[0].epoch, 7)
            self.assertEqual(history[0].group_seq, result.envelope.group_seq)
            self.assertEqual(reloaded_state.epoch, 7)

    async def test_group_blindbox_poll_imports_same_message_for_two_members(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                seed_core = I2PChatCore(profile="seed")
                group_state = GroupState(
                    group_id="core-group-offline-blindbox",
                    epoch=3,
                    members=(ALICE_BARE, BOB_BARE, CAROL_BARE),
                    title="Offline group",
                )
                root_secret = b"r" * 32
                incoming_envelope = GroupEnvelope(
                    group_id=group_state.group_id,
                    epoch=3,
                    msg_id="offline-group-msg-v2",
                    sender_id=ALICE_BARE,
                    group_seq=1,
                    content_type=GroupContentType.GROUP_TEXT,
                    payload="offline to both",
                )
                incoming_wire = encode_group_transport_text_v2(
                    group_state,
                    incoming_envelope,
                )
                frame = seed_core._codec.encode(
                    "U",
                    incoming_wire.encode("utf-8"),
                    msg_id=77,
                    flags=0,
                )
                send_keys = derive_group_blindbox_message_keys(
                    root_secret,
                    group_state.group_id,
                    "send",
                    0,
                    group_epoch=3,
                    root_epoch=2,
                )
                blob = encrypt_blindbox_blob(
                    frame,
                    send_keys.blob_key,
                    "send",
                    0,
                    send_keys.state_tag,
                    padding_bucket=seed_core._blindbox_padding_bucket,
                )

                for profile, local_member in (("bob", BOB_BARE), ("carol", CAROL_BARE)):
                    core = I2PChatCore(profile=profile)
                    profile_dir = os.path.join(tmpdir, profile)
                    os.makedirs(profile_dir, exist_ok=True)
                    core.get_profile_data_dir = (  # type: ignore[method-assign]
                        lambda create=True, _profile_dir=profile_dir: _profile_dir
                    )
                    core.my_dest = _DummyDest(local_member)
                    core.my_signing_seed, core.my_signing_public = (
                        crypto.generate_signing_keypair()
                    )
                    core.save_group_state(group_state)
                    core._save_group_blindbox_channel(
                        group_state.group_id,
                        GroupBlindBoxChannel(
                            channel_id=f"group:{group_state.group_id}",
                            group_epoch=3,
                            state=BlindBoxState(),
                            root_secret_enc=core._group_blindbox_encrypt_root_secret(
                                root_secret,
                                group_state.group_id,
                            ),
                            root_epoch=2,
                        ),
                    )
                    core._blindbox_client = _FakeBlindBoxPollClient(
                        {send_keys.lookup_token: [blob]}
                    )

                    async def _stop_after_first_sleep() -> None:
                        raise asyncio.CancelledError()

                    core._blindbox_poll_sleep = _stop_after_first_sleep  # type: ignore[method-assign]

                    with self.assertRaises(asyncio.CancelledError):
                        await core._blindbox_poll_loop()

                    history = core.load_group_history(group_state.group_id)
                    self.assertEqual(len(history), 1)
                    self.assertEqual(history[0].text, "offline to both")
                    self.assertEqual(history[0].source_peer, ALICE_BARE)

    async def test_group_blindbox_send_retries_on_slot_conflict_and_advances_index(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
                "I2PCHAT_BLINDBOX_SEND_SLOT_RETRIES": "2",
            },
            clear=False,
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                core = I2PChatCore(profile="alice")
                core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
                core._profile_scoped_path = lambda filename: os.path.join(  # type: ignore[method-assign]
                    tmpdir, filename
                )
                core.my_dest = _DummyDest(ALICE_BARE)
                core.my_signing_seed, core.my_signing_public = crypto.generate_signing_keypair()
                core._ensure_blindbox_runtime_started = AsyncMock(return_value=None)  # type: ignore[method-assign]
                retry_client = _RetrySlotBlindBoxClient()
                core._blindbox_client = retry_client

                group_state = core.create_group(
                    title="Retry group",
                    members=[BOB_BARE],
                    group_id="core-group-blindbox-retry",
                    epoch=2,
                )
                root_secret = b"s" * 32
                core._save_group_blindbox_channel(
                    group_state.group_id,
                    GroupBlindBoxChannel(
                        channel_id=f"group:{group_state.group_id}",
                        group_epoch=2,
                        state=BlindBoxState(send_index=0),
                        root_secret_enc=core._group_blindbox_encrypt_root_secret(
                            root_secret,
                            group_state.group_id,
                        ),
                        root_epoch=4,
                    ),
                )

                envelope = GroupEnvelope(
                    group_id=group_state.group_id,
                    epoch=2,
                    msg_id="retry-group-msg-1",
                    sender_id=ALICE_BARE,
                    group_seq=1,
                    content_type=GroupContentType.GROUP_TEXT,
                    payload="retry me",
                )
                result = await core._send_group_envelope_via_group_blindbox(
                    group_state.group_id,
                    envelope,
                )

                self.assertTrue(result.accepted)
                self.assertEqual(result.reason, "blindbox-ready")
                self.assertEqual(retry_client.calls, 2)
                self.assertEqual(len(set(retry_client.tokens)), 2)
                snapshot_bundle = core._group_blindbox_runtime_snapshot(
                    group_state.group_id
                )
                assert snapshot_bundle is not None
                snapshot, _save_state = snapshot_bundle
                self.assertEqual(snapshot.state.send_index, 2)

    async def test_blindbox_runtime_start_failure_sets_retry_cooldown(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                core = I2PChatCore(profile="alice")
                core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
                core._profile_scoped_path = lambda filename: os.path.join(  # type: ignore[method-assign]
                    tmpdir, filename
                )
                core.my_dest = _DummyDest(ALICE_BARE)
                core.my_signing_seed, core.my_signing_public = crypto.generate_signing_keypair()
                core._blindbox_runtime_retry_sec = 60.0
                failing = _FailingBlindBoxRuntimeClient(
                    "SAM session create failed: (no response / disconnected — is I2P running and SAM enabled on 127.0.0.1:17656?)"
                )

                with patch(
                    "i2pchat.core.i2p_chat_core.BlindBoxClient",
                    return_value=failing,
                ):
                    await core._ensure_blindbox_runtime_started()
                    assert core._blindbox_task is not None
                    await core._blindbox_task

                    self.assertEqual(failing.start_calls, 1)
                    self.assertEqual(failing.close_calls, 1)
                    self.assertIsNone(core._blindbox_client)
                    self.assertIn(
                        "SAM session create failed",
                        core._blindbox_runtime_last_error,
                    )
                    self.assertGreater(core._blindbox_runtime_retry_not_before_mono, 0.0)

                    await core._ensure_blindbox_runtime_started()
                    self.assertEqual(failing.start_calls, 1)

    async def test_group_blindbox_send_returns_runtime_unavailable_without_put_retry(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                core = I2PChatCore(profile="alice")
                core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
                core._profile_scoped_path = lambda filename: os.path.join(  # type: ignore[method-assign]
                    tmpdir, filename
                )
                core.my_dest = _DummyDest(ALICE_BARE)
                core.my_signing_seed, core.my_signing_public = crypto.generate_signing_keypair()
                core._ensure_blindbox_runtime_started = AsyncMock(return_value=None)  # type: ignore[method-assign]
                failing = _FailingBlindBoxRuntimeClient(
                    "SAM session create failed: (no response / disconnected — is I2P running and SAM enabled on 127.0.0.1:17656?)"
                )
                core._blindbox_client = failing
                core._blindbox_runtime_last_error = failing.detail
                core._blindbox_runtime_retry_not_before_mono = 10**12

                group_state = core.create_group(
                    title="Unavailable runtime",
                    members=[BOB_BARE],
                    group_id="core-group-runtime-unavailable",
                    epoch=2,
                )
                root_secret = b"r" * 32
                core._save_group_blindbox_channel(
                    group_state.group_id,
                    GroupBlindBoxChannel(
                        channel_id=f"group:{group_state.group_id}",
                        group_epoch=2,
                        state=BlindBoxState(send_index=0),
                        root_secret_enc=core._group_blindbox_encrypt_root_secret(
                            root_secret,
                            group_state.group_id,
                        ),
                        root_epoch=3,
                    ),
                )

                envelope = GroupEnvelope(
                    group_id=group_state.group_id,
                    epoch=2,
                    msg_id="runtime-down-msg-1",
                    sender_id=ALICE_BARE,
                    group_seq=1,
                    content_type=GroupContentType.GROUP_TEXT,
                    payload="will not send",
                )
                result = await core._send_group_envelope_via_group_blindbox(
                    group_state.group_id,
                    envelope,
                )

                self.assertFalse(result.accepted)
                self.assertTrue(
                    result.reason.startswith("BlindBox runtime unavailable:"),
                    result.reason,
                )
                self.assertEqual(failing.put_calls, 0)

    async def test_group_send_uses_pairwise_puts_for_offline_members(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                core = I2PChatCore(profile="alice")
                core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
                core._profile_scoped_path = lambda filename: os.path.join(  # type: ignore[method-assign]
                    tmpdir, filename
                )
                core.my_dest = _DummyDest(ALICE_BARE)
                core.my_signing_seed, core.my_signing_public = crypto.generate_signing_keypair()
                core._ensure_blindbox_runtime_started = AsyncMock(return_value=None)  # type: ignore[method-assign]
                core._blindbox_client = _FakeBlindBoxPollClient({})

                put_calls: list[str] = []

                async def fake_put(
                    *,
                    peer_id: str,
                    **kwargs: object,
                ) -> None:
                    del kwargs
                    put_calls.append(peer_id)

                core._put_blindbox_frame_with_slot_retry = fake_put  # type: ignore[method-assign]

                root_secret = b"x" * 32
                group_state = core.create_group(
                    title="Fanout",
                    members=[BOB_BARE, CAROL_BARE],
                    group_id="core-group-offline-fanout",
                    epoch=1,
                )
                core._save_blindbox_peer_snapshot(
                    _BlindBoxPeerSnapshot(
                        peer_addr=BOB_BARE,
                        peer_id=BOB_BARE,
                        state=BlindBoxState(send_index=0),
                        root_secret=root_secret,
                        root_epoch=1,
                    )
                )
                core._save_blindbox_peer_snapshot(
                    _BlindBoxPeerSnapshot(
                        peer_addr=CAROL_BARE,
                        peer_id=CAROL_BARE,
                        state=BlindBoxState(send_index=0),
                        root_secret=root_secret,
                        root_epoch=1,
                    ),
                )
                core._send_group_envelope_live = AsyncMock(  # type: ignore[method-assign]
                    side_effect=lambda *_args, **_kwargs: GroupTransportOutcome(
                        accepted=False,
                        reason="needs-live-session",
                    )
                )

                await core.send_group_text(
                    group_state.group_id,
                    "hi all",
                    route="offline",
                )

                self.assertCountEqual(put_calls, [BOB_BARE, CAROL_BARE])

    async def test_group_send_queues_pairwise_pending_when_peer_root_missing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            core = I2PChatCore(profile="alice")
            core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
            core.my_dest = _DummyDest(ALICE_BARE)
            group_state = core.create_group(
                title="Pending group",
                members=[BOB_BARE, CAROL_BARE],
                group_id="core-group-pending-route",
                epoch=2,
            )
            core._send_group_envelope_live = AsyncMock(  # type: ignore[method-assign]
                return_value=GroupTransportOutcome(
                    accepted=False,
                    reason="needs-live-session",
                )
            )
            core._send_group_envelope_via_blindbox = AsyncMock(  # type: ignore[method-assign]
                return_value=GroupTransportOutcome(
                    accepted=False,
                    reason="blindbox-await-root",
                )
            )
            core._send_group_envelope_via_group_blindbox = AsyncMock()  # type: ignore[method-assign]

            result = await core.send_group_text(group_state.group_id, "queue me")
            conversation = core.load_group(group_state.group_id)

            assert conversation is not None
            self.assertEqual(conversation.pending_group_blindbox_messages, ())
            self.assertEqual(len(conversation.pending_deliveries), 2)
            self.assertEqual(
                {item.recipient_id for item in conversation.pending_deliveries},
                {BOB_BARE, CAROL_BARE},
            )
            self.assertEqual(
                conversation.history[-1].delivery_results,
                {
                    BOB_BARE: GroupDeliveryStatus.QUEUED_OFFLINE.value,
                    CAROL_BARE: GroupDeliveryStatus.QUEUED_OFFLINE.value,
                },
            )
            self.assertEqual(
                conversation.history[-1].delivery_reasons,
                {
                    BOB_BARE: "blindbox-await-root",
                    CAROL_BARE: "blindbox-await-root",
                },
            )
            self.assertEqual(
                result.delivery_results[BOB_BARE].reason,
                "blindbox-await-root",
            )
            self.assertEqual(
                result.delivery_results[CAROL_BARE].reason,
                "blindbox-await-root",
            )
            core._send_group_envelope_via_group_blindbox.assert_not_awaited()

    async def test_group_pending_delivery_flush_sends_after_peer_root_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            core = I2PChatCore(profile="alice")
            core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
            core.my_dest = _DummyDest(ALICE_BARE)
            group_state = core.create_group(
                title="Pending flush",
                members=[BOB_BARE, CAROL_BARE],
                group_id="core-group-pending-flush",
                epoch=2,
            )
            core._send_group_envelope_live = AsyncMock(  # type: ignore[method-assign]
                return_value=GroupTransportOutcome(
                    accepted=False,
                    reason="needs-live-session",
                )
            )
            core._send_group_envelope_via_blindbox = AsyncMock(  # type: ignore[method-assign]
                return_value=GroupTransportOutcome(
                    accepted=False,
                    reason="blindbox-await-root",
                )
            )
            core._send_group_envelope_via_group_blindbox = AsyncMock()  # type: ignore[method-assign]

            await core.send_group_text(group_state.group_id, "deliver later")

            async def _flush_member_offline(
                recipient_id: str,
                envelope: GroupEnvelope,
                metadata: GroupRecipientDeliveryMetadata,
                *,
                state_snapshot: GroupState | None = None,
            ) -> GroupTransportOutcome:
                self.assertIn(recipient_id, {BOB_BARE, CAROL_BARE})
                self.assertEqual(metadata.recipient_id, recipient_id)
                self.assertEqual(envelope.payload, "deliver later")
                assert state_snapshot is not None
                self.assertEqual(state_snapshot.title, "Pending flush")
                self.assertEqual(state_snapshot.members, (ALICE_BARE, BOB_BARE, CAROL_BARE))
                return GroupTransportOutcome(
                    accepted=True,
                    reason="blindbox-ready",
                    transport_message_id=f"queued-{recipient_id}",
                )

            core._send_group_envelope_via_blindbox = AsyncMock(  # type: ignore[method-assign]
                side_effect=_flush_member_offline
            )

            flushed_bob = await core._flush_pending_group_deliveries_for_peer(BOB_BARE)
            flushed_carol = await core._flush_pending_group_deliveries_for_peer(CAROL_BARE)
            conversation = core.load_group(group_state.group_id)

            self.assertEqual(flushed_bob, 1)
            self.assertEqual(flushed_carol, 1)
            assert conversation is not None
            self.assertEqual(conversation.pending_deliveries, ())
            self.assertEqual(
                conversation.history[-1].delivery_results,
                {
                    BOB_BARE: GroupDeliveryStatus.QUEUED_OFFLINE.value,
                    CAROL_BARE: GroupDeliveryStatus.QUEUED_OFFLINE.value,
                },
            )
            self.assertEqual(conversation.history[-1].delivery_reasons, {})
            core._send_group_envelope_via_group_blindbox.assert_not_awaited()

    def test_group_blindbox_membership_change_requires_root_rotate(self) -> None:
        with patch.dict(
            os.environ,
            {"I2PCHAT_ENABLE_LEGACY_GROUP_BLINDBOX": "1"},
            clear=False,
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                core = I2PChatCore(profile="alice")
                core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
                core.my_dest = _DummyDest(ALICE_BARE)
                core.my_signing_seed, core.my_signing_public = crypto.generate_signing_keypair()
                group_state = core.create_group(
                    title="Rotate me",
                    members=[BOB_BARE],
                    group_id="core-group-rotate-required",
                    epoch=4,
                )
                root_secret = b"z" * 32
                core._save_group_blindbox_channel(
                    group_state.group_id,
                    GroupBlindBoxChannel(
                        channel_id=f"group:{group_state.group_id}",
                        group_epoch=4,
                        state=BlindBoxState(send_index=3),
                        root_secret_enc=core._group_blindbox_encrypt_root_secret(
                            root_secret,
                            group_state.group_id,
                        ),
                        root_epoch=2,
                    ),
                )

                updated = core.update_group(
                    group_state.group_id,
                    title="Rotate me",
                    members=[BOB_BARE, CAROL_BARE],
                )
                snapshot_bundle = core._group_blindbox_runtime_snapshot(updated.group_id)

                assert snapshot_bundle is not None
                snapshot, _save_state = snapshot_bundle
                self.assertEqual(snapshot.group_epoch, updated.epoch)
                self.assertIsNone(snapshot.root_secret)
                self.assertEqual(snapshot.root_epoch, 0)
                self.assertEqual(snapshot.pending_root_secret, None)
                self.assertTrue(snapshot.prev_roots)
                self.assertEqual(snapshot.prev_roots[0]["group_epoch"], 4)
                self.assertEqual(snapshot.prev_roots[0]["root_epoch"], 2)
                self.assertEqual(snapshot.prev_roots[0]["secret"], root_secret)

    def test_group_update_leaves_legacy_group_blindbox_state_untouched_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            core = I2PChatCore(profile="alice")
            core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
            core.my_dest = _DummyDest(ALICE_BARE)
            core.my_signing_seed, core.my_signing_public = crypto.generate_signing_keypair()
            group_state = core.create_group(
                title="Legacy idle",
                members=[BOB_BARE],
                group_id="core-group-legacy-idle",
                epoch=4,
            )
            root_secret = b"y" * 32
            core._save_group_blindbox_channel(
                group_state.group_id,
                GroupBlindBoxChannel(
                    channel_id=f"group:{group_state.group_id}",
                    group_epoch=4,
                    state=BlindBoxState(send_index=3),
                    root_secret_enc=core._group_blindbox_encrypt_root_secret(
                        root_secret,
                        group_state.group_id,
                    ),
                    root_epoch=2,
                ),
            )

            updated = core.update_group(
                group_state.group_id,
                title="Legacy idle",
                members=[BOB_BARE, CAROL_BARE],
            )
            snapshot_bundle = core._group_blindbox_runtime_snapshot(updated.group_id)

            assert snapshot_bundle is not None
            snapshot, _save_state = snapshot_bundle
            self.assertEqual(snapshot.group_epoch, 4)
            self.assertEqual(snapshot.root_secret, root_secret)
            self.assertEqual(snapshot.root_epoch, 2)

    async def test_schedule_group_blindbox_root_push_requires_legacy_opt_in(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_ENABLED": "1",
                "I2PCHAT_BLINDBOX_REPLICAS": "r1.b32.i2p",
            },
            clear=False,
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                core = I2PChatCore(profile="alice")
                core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
                core.my_dest = _DummyDest(ALICE_BARE)
                core.create_group(
                    title="Legacy push",
                    members=[BOB_BARE],
                    group_id="core-group-legacy-push",
                    epoch=1,
                )
                state = core.load_group_state("core-group-legacy-push")
                assert state is not None
                core.session_manager.is_live_path_alive = lambda *args, **kwargs: True  # type: ignore[method-assign]
                core._writer_frame_peer_and_text_acks = lambda *_args, **_kwargs: (  # type: ignore[method-assign]
                    _DummyWriter(),
                    BOB_BARE,
                    {},
                )
                core._send_group_blindbox_root_if_needed = AsyncMock()  # type: ignore[method-assign]

                core._schedule_group_blindbox_root_push(state)
                await asyncio.sleep(0)
                core._send_group_blindbox_root_if_needed.assert_not_awaited()

                with patch.dict(
                    os.environ,
                    {"I2PCHAT_ENABLE_LEGACY_GROUP_BLINDBOX": "1"},
                    clear=False,
                ):
                    core._schedule_group_blindbox_root_push(state)
                    await asyncio.sleep(0)
                core._send_group_blindbox_root_if_needed.assert_awaited_once()

    async def test_group_next_sequence_survives_create_send_and_import_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            core = I2PChatCore(profile="alice")
            core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
            core.my_dest = _DummyDest(ALICE_BARE)
            group_state = core.create_group(
                title="Sequence group",
                members=[BOB_BARE],
                group_id="core-group-seq",
                epoch=8,
            )
            core.group_manager.prime_group_sequence(group_state.group_id, next_group_seq=1)
            core._send_group_envelope_live = AsyncMock(  # type: ignore[method-assign]
                return_value=GroupTransportOutcome(
                    accepted=True,
                    reason="live-session",
                    transport_message_id="live-bob",
                )
            )
            core._send_group_envelope_via_group_blindbox = AsyncMock(  # type: ignore[method-assign]
                return_value=GroupTransportOutcome(
                    accepted=True,
                    reason="blindbox-ready",
                    transport_message_id="queue-bob",
                )
            )

            sent = await core.send_group_text(group_state.group_id, "first")
            after_send = core.load_group(group_state.group_id)

            assert after_send is not None
            self.assertEqual(after_send.next_group_seq, sent.envelope.group_seq + 1)
            self.assertEqual(after_send.state.epoch, 8)
            self.assertEqual(len(after_send.history), 1)
            self.assertEqual(after_send.history[0].group_seq, sent.envelope.group_seq)

            imported_envelope = GroupEnvelope(
                group_id=group_state.group_id,
                epoch=8,
                msg_id="seq-import-1",
                sender_id=BOB_BARE,
                group_seq=sent.envelope.group_seq + 1,
                content_type=GroupContentType.GROUP_TEXT,
                payload="second",
            )
            imported_wire = encode_group_transport_text(
                GroupState(
                    group_id=group_state.group_id,
                    epoch=8,
                    members=(ALICE_BARE, BOB_BARE),
                    title="Sequence group",
                ),
                imported_envelope,
                GroupRecipientDeliveryMetadata(
                    recipient_id=ALICE_BARE,
                    delivery_id="seq-import-1:alice",
                ),
            )

            imported = core.import_group_transport(imported_wire, source_peer=BOB_BARE)
            after_import = core.load_group(group_state.group_id)

            assert imported is not None
            assert after_import is not None
            self.assertEqual(imported.status, GroupImportStatus.IMPORTED)
            self.assertEqual(after_import.next_group_seq, imported_envelope.group_seq + 1)
            self.assertEqual(after_import.state.epoch, 8)
            self.assertEqual(len(after_import.history), 2)
            self.assertEqual(after_import.history[-1].group_seq, imported_envelope.group_seq)
            self.assertEqual(after_import.history[-1].epoch, 8)

    async def test_reload_primes_group_sequence_from_persisted_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            core = I2PChatCore(profile="alice")
            core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
            core.my_dest = _DummyDest(ALICE_BARE)
            group_state = core.create_group(
                title="Reloaded sequence group",
                members=[BOB_BARE],
                group_id="core-group-reload-seq",
                epoch=5,
            )
            core.session_manager.set_peer_handshake_complete(BOB_BARE)
            core._send_group_envelope_live = AsyncMock(  # type: ignore[method-assign]
                return_value=GroupTransportOutcome(
                    accepted=True,
                    reason="live-session",
                    transport_message_id="live-bob",
                )
            )
            core._send_group_envelope_via_group_blindbox = AsyncMock(  # type: ignore[method-assign]
                return_value=GroupTransportOutcome(
                    accepted=True,
                    reason="blindbox-ready",
                    transport_message_id="queue-bob",
                )
            )

            first = await core.send_group_text(group_state.group_id, "first")
            record_path = os.path.join(
                tmpdir,
                next(name for name in os.listdir(tmpdir) if name.startswith("alice.group.")),
            )
            with open(record_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            payload["next_group_seq"] = 1
            with open(record_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)

            reloaded = core.load_group(group_state.group_id)
            second = await core.send_group_text(group_state.group_id, "second")
            history = core.load_group_history(group_state.group_id)

            assert reloaded is not None
            self.assertEqual(reloaded.next_group_seq, first.envelope.group_seq + 1)
            self.assertEqual(second.envelope.group_seq, first.envelope.group_seq + 1)
            self.assertEqual([entry.group_seq for entry in history], [1, 2])

    async def test_local_and_imported_group_text_entries_share_compatible_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            core = I2PChatCore(profile="alice")
            core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
            core.my_dest = _DummyDest(ALICE_BARE)
            group_state = core.create_group(
                title="Shape group",
                members=[BOB_BARE],
                group_id="core-group-shape",
                epoch=6,
            )
            core.session_manager.set_peer_handshake_complete(BOB_BARE)
            core._send_group_envelope_live = AsyncMock(  # type: ignore[method-assign]
                return_value=GroupTransportOutcome(
                    accepted=True,
                    reason="live-session",
                    transport_message_id="live-bob",
                )
            )
            core._send_group_envelope_via_group_blindbox = AsyncMock(  # type: ignore[method-assign]
                return_value=GroupTransportOutcome(
                    accepted=True,
                    reason="blindbox-ready",
                    transport_message_id="queue-bob",
                )
            )

            sent = await core.send_group_text(group_state.group_id, "from alice")
            imported_envelope = GroupEnvelope(
                group_id=group_state.group_id,
                epoch=6,
                msg_id="shape-import-1",
                sender_id=BOB_BARE,
                group_seq=sent.envelope.group_seq + 1,
                content_type=GroupContentType.GROUP_TEXT,
                payload="from bob",
            )
            imported_wire = encode_group_transport_text(
                GroupState(
                    group_id=group_state.group_id,
                    epoch=6,
                    members=(ALICE_BARE, BOB_BARE),
                    title="Shape group",
                ),
                imported_envelope,
                GroupRecipientDeliveryMetadata(
                    recipient_id=ALICE_BARE,
                    delivery_id="shape-import-1:alice",
                ),
            )

            imported = core.import_group_transport(imported_wire, source_peer=BOB_BARE)
            history = core.load_group_history(group_state.group_id)

            assert imported is not None
            self.assertEqual(imported.status, GroupImportStatus.IMPORTED)
            assert imported.state is not None
            self.assertEqual(imported.state.group_id, group_state.group_id)
            assert imported.envelope is not None
            self.assertEqual(imported.envelope.msg_id, "shape-import-1")
            self.assertEqual(len(history), 2)
            sent_entry, imported_entry = history
            self.assertIs(type(sent_entry), type(imported_entry))
            self.assertEqual(sent_entry.kind, "me")
            self.assertEqual(imported_entry.kind, "peer")
            self.assertEqual(sent_entry.sender_id, ALICE_BARE)
            self.assertEqual(imported_entry.sender_id, BOB_BARE)
            self.assertEqual(sent_entry.content_type, GroupContentType.GROUP_TEXT)
            self.assertEqual(imported_entry.content_type, GroupContentType.GROUP_TEXT)
            self.assertEqual(sent_entry.text, "from alice")
            self.assertEqual(imported_entry.text, "from bob")
            self.assertEqual(sent_entry.payload, "from alice")
            self.assertEqual(imported_entry.payload, "from bob")
            self.assertEqual(sent_entry.epoch, 6)
            self.assertEqual(imported_entry.epoch, 6)
            self.assertEqual(sent_entry.group_seq, sent.envelope.group_seq)
            self.assertEqual(imported_entry.group_seq, imported_envelope.group_seq)
            self.assertIsInstance(sent_entry.created_at, datetime)
            self.assertIsInstance(imported_entry.created_at, datetime)

    def test_incoming_group_text_imports_into_local_group_history_and_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            messages: list[object] = []
            core = I2PChatCore(profile="alice", on_message=messages.append)
            core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
            core.my_dest = _DummyDest(ALICE_BARE)
            state = GroupState(
                group_id="core-group-2",
                epoch=4,
                members=(ALICE_BARE, BOB_BARE),
                title="Imported group",
            )
            envelope = GroupEnvelope(
                group_id=state.group_id,
                epoch=state.epoch,
                msg_id="group-msg-1",
                sender_id=BOB_BARE,
                group_seq=2,
                content_type=GroupContentType.GROUP_TEXT,
                payload="hello from bob",
            )
            wire_text = encode_group_transport_text(
                state,
                envelope,
                GroupRecipientDeliveryMetadata(
                    recipient_id=ALICE_BARE,
                    delivery_id="group-msg-1:alice",
                ),
            )

            handled = core.import_group_transport(
                wire_text,
                source_peer=BOB_BARE,
            )

            assert handled is not None
            self.assertEqual(handled.status, GroupImportStatus.IMPORTED)
            loaded_state = core.load_group_state("core-group-2")
            history = core.load_group_history("core-group-2")
            assert loaded_state is not None
            self.assertEqual(loaded_state.title, "Imported group")
            self.assertEqual(loaded_state.epoch, 4)
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0].msg_id, "group-msg-1")
            self.assertEqual(history[0].text, "hello from bob")
            self.assertEqual(history[0].group_seq, 2)
            self.assertTrue(messages)

    def test_duplicate_import_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            core = I2PChatCore(profile="alice")
            core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
            core.my_dest = _DummyDest(ALICE_BARE)
            state = GroupState(
                group_id="core-group-3",
                epoch=2,
                members=(ALICE_BARE, BOB_BARE),
                title="Dup test",
            )
            envelope = GroupEnvelope(
                group_id=state.group_id,
                epoch=2,
                msg_id="dup-msg",
                sender_id=BOB_BARE,
                group_seq=1,
                content_type=GroupContentType.GROUP_TEXT,
                payload="once only",
            )
            wire_text = encode_group_transport_text(
                state,
                envelope,
                GroupRecipientDeliveryMetadata(
                    recipient_id=ALICE_BARE,
                    delivery_id="dup-msg:alice",
                ),
            )

            first = core.import_group_transport(wire_text, source_peer=BOB_BARE)
            second = core.import_group_transport(wire_text, source_peer=BOB_BARE)

            assert first is not None
            assert second is not None
            self.assertEqual(first.status, GroupImportStatus.IMPORTED)
            self.assertEqual(second.status, GroupImportStatus.DUPLICATE)
            self.assertEqual(len(core.load_group_history("core-group-3")), 1)

    def test_duplicate_import_is_a_safe_no_op_for_state_and_next_seq(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            core = I2PChatCore(profile="alice")
            core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
            core.my_dest = _DummyDest(ALICE_BARE)
            original_state = GroupState(
                group_id="core-group-dup-safe",
                epoch=2,
                members=(ALICE_BARE, BOB_BARE),
                title="Original title",
            )
            original_envelope = GroupEnvelope(
                group_id=original_state.group_id,
                epoch=2,
                msg_id="dup-safe-msg-1",
                sender_id=BOB_BARE,
                group_seq=1,
                content_type=GroupContentType.GROUP_TEXT,
                payload="first import",
            )
            original_wire = encode_group_transport_text(
                original_state,
                original_envelope,
                GroupRecipientDeliveryMetadata(
                    recipient_id=ALICE_BARE,
                    delivery_id="dup-safe-msg-1:alice",
                ),
            )

            first = core.import_group_transport(original_wire, source_peer=BOB_BARE)
            renamed_state = GroupState(
                group_id=original_state.group_id,
                epoch=3,
                members=(ALICE_BARE, BOB_BARE),
                title="Renamed title",
            )
            rename_envelope = GroupEnvelope(
                group_id=renamed_state.group_id,
                epoch=3,
                msg_id="dup-safe-rename-1",
                sender_id=BOB_BARE,
                group_seq=2,
                content_type=GroupContentType.GROUP_CONTROL,
                payload={"op": "rename", "title": "Renamed title", "epoch": 3},
            )
            rename_wire = encode_group_transport_text(
                renamed_state,
                rename_envelope,
                GroupRecipientDeliveryMetadata(
                    recipient_id=ALICE_BARE,
                    delivery_id="dup-safe-rename-1:alice",
                ),
            )

            renamed = core.import_group_transport(rename_wire, source_peer=BOB_BARE)
            before_duplicate = core.load_group("core-group-dup-safe")
            duplicate = core.import_group_transport(original_wire, source_peer=BOB_BARE)
            after_duplicate = core.load_group("core-group-dup-safe")

            assert first is not None
            assert renamed is not None
            assert before_duplicate is not None
            assert duplicate is not None
            assert after_duplicate is not None
            self.assertEqual(first.status, GroupImportStatus.IMPORTED)
            self.assertEqual(renamed.status, GroupImportStatus.IMPORTED)
            self.assertEqual(duplicate.status, GroupImportStatus.DUPLICATE)
            self.assertEqual(duplicate.state, before_duplicate.state)
            self.assertEqual(before_duplicate.state.title, "Renamed title")
            self.assertEqual(after_duplicate.state.title, "Renamed title")
            self.assertEqual(before_duplicate.state.epoch, 3)
            self.assertEqual(after_duplicate.state.epoch, 3)
            self.assertEqual(len(after_duplicate.history), 2)
            self.assertEqual(after_duplicate.next_group_seq, 3)
            self.assertEqual(before_duplicate.next_group_seq, after_duplicate.next_group_seq)
            self.assertEqual(
                [entry.msg_id for entry in after_duplicate.history],
                ["dup-safe-msg-1", "dup-safe-rename-1"],
            )

    def test_invalid_group_text_payload_is_rejected_without_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            messages: list[object] = []
            core = I2PChatCore(profile="alice", on_message=messages.append)
            core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
            core.my_dest = _DummyDest(ALICE_BARE)
            bad_wire = (
                '__I2PCHAT_GROUP__:{"content_type":"GROUP_TEXT","created_at":"2026-04-09T10:00:00+00:00",'
                '"delivery_id":"bad-1:alice","epoch":1,"group_id":"core-group-bad-text","group_seq":1,'
                '"members":["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.b32.i2p","bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.b32.i2p"],'
                '"msg_id":"bad-1","payload":{"text":"not-a-string"},"recipient_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.b32.i2p",'
                '"sender_id":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.b32.i2p","transport":"group","version":1}'
            )

            result = core.import_group_transport(bad_wire, source_peer=BOB_BARE)

            assert result is not None
            self.assertEqual(result.status, GroupImportStatus.INVALID)
            self.assertIsNone(core.load_group_state("core-group-bad-text"))
            self.assertEqual(core.load_group_history("core-group-bad-text"), [])
            self.assertTrue(messages)
            self.assertEqual(getattr(messages[-1], "kind", None), "error")

    def test_invalid_group_control_recipient_is_rejected_without_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            core = I2PChatCore(profile="alice")
            core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
            core.my_dest = _DummyDest(ALICE_BARE)
            bad_wire = (
                '__I2PCHAT_GROUP__:{"content_type":"GROUP_CONTROL","created_at":"2026-04-09T10:00:00+00:00",'
                '"delivery_id":"bad-2:carol","epoch":2,"group_id":"core-group-bad-control","group_seq":2,'
                '"members":["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.b32.i2p","bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.b32.i2p","cccccccccccccccccccccccccccccccccccccccc.b32.i2p"],'
                '"msg_id":"bad-2","payload":{"op":"rename","title":"Nope"},"recipient_id":"cccccccccccccccccccccccccccccccccccccccc.b32.i2p",'
                '"sender_id":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.b32.i2p","transport":"group","version":1}'
            )

            result = core.import_group_transport(bad_wire, source_peer=BOB_BARE)

            assert result is not None
            self.assertEqual(result.status, GroupImportStatus.INVALID)
            self.assertIsNone(core.load_group_state("core-group-bad-control"))
            self.assertEqual(core.load_group_history("core-group-bad-control"), [])

    async def test_group_control_can_be_persisted_and_imported_minimally(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            core = I2PChatCore(profile="alice")
            core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
            core.my_dest = _DummyDest(ALICE_BARE)
            group_state = core.create_group(
                title="Original title",
                members=[BOB_BARE],
                group_id="core-group-4",
                epoch=3,
            )
            core.session_manager.set_peer_handshake_complete(BOB_BARE)
            core._send_group_envelope_live = AsyncMock(  # type: ignore[method-assign]
                return_value=GroupTransportOutcome(
                    accepted=True,
                    reason="live-session",
                    transport_message_id="live-bob",
                )
            )
            core._send_group_envelope_via_group_blindbox = AsyncMock(  # type: ignore[method-assign]
                return_value=GroupTransportOutcome(
                    accepted=True,
                    reason="blindbox-ready",
                    transport_message_id="queue-bob",
                )
            )

            sent = await core.send_group_control(
                group_state.group_id,
                {"op": "rename", "title": "Renamed title", "epoch": 4},
            )
            after_send_state = core.load_group_state(group_state.group_id)
            after_send_history = core.load_group_history(group_state.group_id)

            assert after_send_state is not None
            self.assertEqual(sent.envelope.content_type, GroupContentType.GROUP_CONTROL)
            self.assertEqual(after_send_state.title, "Renamed title")
            self.assertEqual(after_send_state.epoch, 4)
            self.assertEqual(after_send_history[-1].kind, "me")
            self.assertEqual(after_send_history[-1].sender_id, ALICE_BARE)
            self.assertEqual(after_send_history[-1].text, "")
            self.assertEqual(
                after_send_history[-1].payload,
                {"op": "rename", "title": "Renamed title", "epoch": 4},
            )
            self.assertEqual(after_send_history[-1].content_type, GroupContentType.GROUP_CONTROL)
            self.assertEqual(after_send_history[-1].group_seq, sent.envelope.group_seq)
            self.assertIsInstance(after_send_history[-1].created_at, datetime)

            imported_state = GroupState(
                group_id=group_state.group_id,
                epoch=5,
                members=(ALICE_BARE, BOB_BARE, CAROL_BARE),
                title="Renamed title",
            )
            imported_envelope = GroupEnvelope(
                group_id=group_state.group_id,
                epoch=5,
                msg_id="control-import-1",
                sender_id=BOB_BARE,
                group_seq=sent.envelope.group_seq + 1,
                content_type=GroupContentType.GROUP_CONTROL,
                payload={
                    "op": "rename",
                    "title": "Imported title",
                    "members": [ALICE_BARE, BOB_BARE, CAROL_BARE],
                    "epoch": 5,
                },
            )
            imported_wire = encode_group_transport_text(
                imported_state,
                imported_envelope,
                GroupRecipientDeliveryMetadata(
                    recipient_id=ALICE_BARE,
                    delivery_id="control-import-1:alice",
                ),
            )

            imported = core.import_group_transport(imported_wire, source_peer=BOB_BARE)
            final_state = core.load_group_state(group_state.group_id)
            final_history = core.load_group_history(group_state.group_id)

            assert imported is not None
            self.assertEqual(imported.status, GroupImportStatus.IMPORTED)
            assert final_state is not None
            self.assertEqual(final_state.title, "Imported title")
            self.assertEqual(final_state.epoch, 5)
            self.assertIn(CAROL_BARE, final_state.members)
            self.assertEqual(final_history[-1].kind, "peer")
            self.assertEqual(final_history[-1].sender_id, BOB_BARE)
            self.assertEqual(final_history[-1].text, "")
            self.assertEqual(final_history[-1].payload, imported_envelope.payload)
            self.assertEqual(final_history[-1].content_type, GroupContentType.GROUP_CONTROL)
            self.assertEqual(final_history[-1].msg_id, "control-import-1")
            self.assertEqual(final_history[-1].group_seq, imported_envelope.group_seq)
            self.assertIsInstance(final_history[-1].created_at, datetime)

    async def test_direct_chat_behavior_still_works(self) -> None:
        core = I2PChatCore(profile="alice")
        attach_mock_live_session(core, BOB_BARE, (object(), _DummyWriter()))
        core.session_manager.set_peer_handshake_complete(
            core._normalize_peer_addr(BOB_BARE)
        )

        result = await core.send_text("hello direct")

        self.assertTrue(result.accepted)
        self.assertEqual(result.route, "online-live")
        self.assertEqual(result.reason, "live-session")

    def test_plain_text_is_not_interpreted_as_group_transport(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            core = I2PChatCore(profile="alice")
            core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
            self.assertIsNone(core.import_group_transport("plain direct text"))
            self.assertFalse(core.import_group_transport_text("plain direct text"))

    def test_bool_group_import_wrapper_only_reports_true_for_real_imports(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            core = I2PChatCore(profile="alice")
            core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
            core.my_dest = _DummyDest(ALICE_BARE)
            state = GroupState(
                group_id="core-group-bool-wrapper",
                epoch=1,
                members=(ALICE_BARE, BOB_BARE),
                title="Bool wrapper",
            )
            envelope = GroupEnvelope(
                group_id=state.group_id,
                epoch=state.epoch,
                msg_id="bool-wrapper-1",
                sender_id=BOB_BARE,
                group_seq=1,
                content_type=GroupContentType.GROUP_TEXT,
                payload="hello",
            )
            wire_text = encode_group_transport_text(
                state,
                envelope,
                GroupRecipientDeliveryMetadata(
                    recipient_id=ALICE_BARE,
                    delivery_id="bool-wrapper-1:alice",
                ),
            )
            bad_wire = (
                '__I2PCHAT_GROUP__:{"content_type":"GROUP_TEXT","created_at":"2026-04-09T10:00:00+00:00",'
                '"delivery_id":"bool-wrapper-bad:alice","epoch":1,"group_id":"core-group-bool-wrapper-bad","group_seq":1,'
                '"members":["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.b32.i2p","bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.b32.i2p"],'
                '"msg_id":"bool-wrapper-bad","payload":{"text":"not-a-string"},"recipient_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.b32.i2p",'
                '"sender_id":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.b32.i2p","transport":"group","version":1}'
            )

            self.assertTrue(core.import_group_transport_text(wire_text, source_peer=BOB_BARE))
            self.assertFalse(core.import_group_transport_text(wire_text, source_peer=BOB_BARE))
            self.assertFalse(core.import_group_transport_text(bad_wire, source_peer=BOB_BARE))


if __name__ == "__main__":
    unittest.main()
