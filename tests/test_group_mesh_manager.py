from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from i2pchat.core.i2p_chat_core import I2PChatCore
from i2pchat.groups import GroupMeshManager, GroupMeshPeerSnapshot, GroupState

from tests.test_asyncio_regression import _FakeReader, _FakeWriter


LOCAL_MEMBER = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
PEER_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
PEER_C = "cccccccccccccccccccccccccccccccccccccccc"
PEER_D = "dddddddddddddddddddddddddddddddddddddddd"


class GroupMeshManagerPlannerTests(unittest.TestCase):
    def test_collect_due_peer_intros_prefers_bootstrap_over_offline_ready(self) -> None:
        state = GroupState(
            group_id="mesh-group",
            epoch=1,
            members=(LOCAL_MEMBER, PEER_B, PEER_C, PEER_D),
            title="Mesh",
        )
        snapshots = {
            PEER_B: GroupMeshPeerSnapshot(
                peer_id=PEER_B,
                peer_state="disconnected",
                blindbox_ready=False,
            ),
            PEER_C: GroupMeshPeerSnapshot(
                peer_id=PEER_C,
                peer_state="stale",
                blindbox_ready=False,
                next_retry_mono=50.0,
            ),
            PEER_D: GroupMeshPeerSnapshot(
                peer_id=PEER_D,
                peer_state="disconnected",
                blindbox_ready=True,
            ),
        }
        scheduled: list[list[str]] = []
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_GROUP_AUTO_MESH": "1",
                "I2PCHAT_GROUP_AUTO_MESH_MAX_PER_TICK": "8",
            },
            clear=False,
        ):
            manager = GroupMeshManager(
                list_group_states=lambda: [state],
                get_local_member_id=lambda: LOCAL_MEMBER,
                build_peer_snapshot=lambda peer_id: snapshots[peer_id],
                schedule_peer_intros=lambda peers: scheduled.append(list(peers)),
                clock=lambda: 100.0,
            )
            due = manager.collect_due_peer_intros(now_mono=100.0)
            self.assertEqual(due, [PEER_C, PEER_B])
            scheduled_now = manager.tick(now_mono=100.0)
            self.assertEqual(scheduled_now, [PEER_C, PEER_B])
            self.assertEqual(scheduled, [[PEER_C, PEER_B]])

    def test_collect_due_peer_intros_skips_peers_in_backoff_or_active(self) -> None:
        state = GroupState(
            group_id="mesh-group-2",
            epoch=1,
            members=(LOCAL_MEMBER, PEER_B, PEER_C),
            title="Mesh",
        )
        snapshots = {
            PEER_B: GroupMeshPeerSnapshot(
                peer_id=PEER_B,
                peer_state="failed",
                blindbox_ready=False,
                next_retry_mono=150.0,
            ),
            PEER_C: GroupMeshPeerSnapshot(
                peer_id=PEER_C,
                peer_state="handshaking",
                active_session=True,
                blindbox_ready=False,
            ),
        }
        with patch.dict(os.environ, {"I2PCHAT_GROUP_AUTO_MESH": "1"}, clear=False):
            manager = GroupMeshManager(
                list_group_states=lambda: [state],
                get_local_member_id=lambda: LOCAL_MEMBER,
                build_peer_snapshot=lambda peer_id: snapshots[peer_id],
                schedule_peer_intros=lambda peers: None,
                clock=lambda: 100.0,
            )
            self.assertEqual(manager.collect_due_peer_intros(now_mono=100.0), [])


class GroupMeshBackgroundConnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_background_connect_can_bootstrap_without_foreground_session(self) -> None:
        core = I2PChatCore(profile="alice")
        core.my_dest = SimpleNamespace(base64="DEST_B64")
        core._start_handshake_watchdog = lambda _conn, peer_id=None: None  # type: ignore[assignment]
        core.receive_loop = AsyncMock(return_value=None)  # type: ignore[method-assign]
        core.initiate_secure_handshake = AsyncMock(return_value=True)  # type: ignore[method-assign]
        core._keepalive_loop = AsyncMock(return_value=None)  # type: ignore[method-assign]

        reader = _FakeReader(b"")
        writer = _FakeWriter()

        import i2pchat.core.i2p_chat_core as core_module

        original_stream_connect = core_module.i2plib.stream_connect
        original_nacl_available = core_module.crypto.NACL_AVAILABLE

        async def _fake_stream_connect(session_id: str, target: str, sam_address=None):
            return reader, writer

        core_module.i2plib.stream_connect = _fake_stream_connect  # type: ignore[assignment]
        core_module.crypto.NACL_AVAILABLE = True
        try:
            await core.connect_to_peer(PEER_B, activate_as_current=False)
        finally:
            core_module.i2plib.stream_connect = original_stream_connect  # type: ignore[assignment]
            core_module.crypto.NACL_AVAILABLE = original_nacl_available

        normalized_peer = core._normalize_peer_addr(PEER_B)
        self.assertIn(normalized_peer, core._live_sessions)
        self.assertIsNone(core.current_peer_addr)
        self.assertEqual(core.active_live_peer_id, None)
        self.assertTrue(bytes(writer.buffer).startswith(b"DEST_B64\n"))


if __name__ == "__main__":
    unittest.main()
