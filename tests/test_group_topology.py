from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock

if "PIL" not in sys.modules:
    pil_module = types.ModuleType("PIL")
    pil_image_module = types.ModuleType("PIL.Image")
    pil_image_module.Image = object  # type: ignore[attr-defined]
    pil_module.Image = pil_image_module  # type: ignore[attr-defined]
    sys.modules["PIL"] = pil_module
    sys.modules["PIL.Image"] = pil_image_module

from i2pchat import crypto
from i2pchat.core.i2p_chat_core import I2PChatCore, _BlindBoxPeerSnapshot
from i2pchat.groups import (
    GroupDeliveryStatus,
    GroupState,
    GroupTransportOutcome,
    build_observed_group_topology,
    render_group_topology_ascii,
    render_group_topology_mermaid,
)
from i2pchat.storage.blindbox_state import BlindBoxState
ALICE_BARE = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
BOB_BARE = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
CAROL_BARE = "cccccccccccccccccccccccccccccccccccccccc"


class _DummyDest:
    def __init__(self, base32: str) -> None:
        self.base32 = base32


class GroupTopologyRendererTests(unittest.TestCase):
    def test_observed_topology_ascii_and_mermaid_renderers(self) -> None:
        state = GroupState(
            group_id="group-topology",
            epoch=1,
            members=(ALICE_BARE, BOB_BARE, CAROL_BARE),
            title="Mesh",
        )
        snapshot = build_observed_group_topology(
            state,
            local_member_id=ALICE_BARE,
            live_by_member={BOB_BARE: True, CAROL_BARE: False},
            peer_state_by_member={
                BOB_BARE: "secure",
                CAROL_BARE: "disconnected",
            },
            group_blindbox_ready=True,
            blindbox_ready_by_member={CAROL_BARE: True},
            delivery_status_by_member={
                BOB_BARE: GroupDeliveryStatus.DELIVERED_LIVE.value,
                CAROL_BARE: GroupDeliveryStatus.QUEUED_OFFLINE.value,
            },
            delivery_reason_by_member={CAROL_BARE: "blindbox-await-root"},
        )

        ascii_map = render_group_topology_ascii(snapshot)
        mermaid_map = render_group_topology_mermaid(snapshot)

        self.assertIn("Observed group topology: Mesh [group-topology]", ascii_map)
        self.assertIn("Local: You", ascii_map)
        self.assertIn("Group blindbox: ready", ascii_map)
        self.assertIn("live", ascii_map)
        self.assertIn("blindbox-ready", ascii_map)
        self.assertIn("last=queued_offline", ascii_map)
        self.assertIn("reason=blindbox-await-root", ascii_map)

        self.assertIn("graph TD", mermaid_map)
        self.assertIn('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa["You"]', mermaid_map)
        self.assertIn("last=queued_offline", mermaid_map)

    def test_observed_topology_marks_await_root_when_group_channel_missing(self) -> None:
        state = GroupState(
            group_id="group-topology-await-root",
            epoch=2,
            members=(ALICE_BARE, BOB_BARE),
            title="Await root",
        )
        snapshot = build_observed_group_topology(
            state,
            local_member_id=ALICE_BARE,
            live_by_member={BOB_BARE: False},
            peer_state_by_member={BOB_BARE: "disconnected"},
            await_group_root=True,
            delivery_status_by_member={
                BOB_BARE: GroupDeliveryStatus.QUEUED_OFFLINE.value,
            },
            delivery_reason_by_member={
                BOB_BARE: "blindbox-await-group-root",
            },
        )

        self.assertEqual(snapshot.edges[0].state.value, "await-root")
        ascii_map = render_group_topology_ascii(snapshot)
        self.assertIn("Group blindbox: await-root", ascii_map)
        self.assertIn("await-root", ascii_map)


class GroupTopologyCoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_core_group_topology_snapshot_tracks_live_and_blindbox_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            core = I2PChatCore(profile="alice")
            core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
            core._profile_scoped_path = lambda filename: os.path.join(  # type: ignore[method-assign]
                tmpdir, filename
            )
            core.my_dest = _DummyDest(ALICE_BARE)
            core.my_signing_seed, core.my_signing_public = crypto.generate_signing_keypair()

            group_state = core.create_group(
                title="Observed mesh",
                members=[BOB_BARE, CAROL_BARE],
                group_id="group-topology-core",
                epoch=1,
            )

            core.session_manager.set_peer_handshake_complete(BOB_BARE)
            core._save_blindbox_peer_snapshot(
                _BlindBoxPeerSnapshot(
                    peer_addr=CAROL_BARE,
                    peer_id=CAROL_BARE,
                    state=BlindBoxState(send_index=0),
                    root_secret=b"x" * 32,
                    root_epoch=2,
                )
            )

            core._send_group_envelope_live = AsyncMock(  # type: ignore[method-assign]
                return_value=GroupTransportOutcome(
                    accepted=True,
                    reason="live-session",
                    transport_message_id="101",
                )
            )
            core._send_group_envelope_via_blindbox = AsyncMock(  # type: ignore[method-assign]
                return_value=GroupTransportOutcome(
                    accepted=True,
                    reason="blindbox-ready",
                    transport_message_id="202",
                )
            )
            core._send_group_envelope_via_group_blindbox = AsyncMock()  # type: ignore[method-assign]

            result = await core.send_group_text(group_state.group_id, "mesh hello")
            snapshot = core.get_group_topology_snapshot(group_state.group_id)

            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            self.assertEqual(snapshot.group_id, group_state.group_id)
            self.assertTrue(snapshot.group_blindbox_ready)
            self.assertFalse(snapshot.await_group_root)

            nodes = {node.member_id: node for node in snapshot.nodes}
            edges = {edge.target_id: edge for edge in snapshot.edges}

            self.assertIn(ALICE_BARE, nodes)
            self.assertTrue(nodes[ALICE_BARE].is_local)
            self.assertIn(BOB_BARE, edges)
            self.assertEqual(edges[BOB_BARE].state.value, "live")
            self.assertEqual(
                edges[BOB_BARE].last_delivery_status,
                GroupDeliveryStatus.DELIVERED_LIVE.value,
            )
            self.assertIn(CAROL_BARE, edges)
            self.assertEqual(edges[CAROL_BARE].state.value, "blindbox")
            self.assertTrue(edges[CAROL_BARE].blindbox_ready)
            self.assertEqual(
                edges[CAROL_BARE].last_delivery_status,
                GroupDeliveryStatus.QUEUED_OFFLINE.value,
            )
            self.assertEqual(
                result.delivery_results[CAROL_BARE].status,
                GroupDeliveryStatus.QUEUED_OFFLINE,
            )
            core._send_group_envelope_via_group_blindbox.assert_not_awaited()

            ascii_map = core.get_group_topology_ascii(group_state.group_id)
            mermaid_map = core.get_group_topology_mermaid(group_state.group_id)
            self.assertIn("Observed group topology: Observed mesh [group-topology-core]", ascii_map)
            self.assertIn("Group blindbox: ready", ascii_map)
            self.assertIn("blindbox-ready", ascii_map)
            self.assertIn("last=queued_offline", ascii_map)
            self.assertIn("graph TD", mermaid_map)

    def test_core_group_topology_snapshot_marks_only_missing_pairwise_roots_await(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            core = I2PChatCore(profile="alice")
            core.get_profile_data_dir = lambda create=True: tmpdir  # type: ignore[method-assign]
            core._profile_scoped_path = lambda filename: os.path.join(  # type: ignore[method-assign]
                tmpdir, filename
            )
            core.my_dest = _DummyDest(ALICE_BARE)
            core.my_signing_seed, core.my_signing_public = crypto.generate_signing_keypair()

            group_state = core.create_group(
                title="Observed partial",
                members=[BOB_BARE, CAROL_BARE],
                group_id="group-topology-partial",
                epoch=1,
            )
            core._save_blindbox_peer_snapshot(
                _BlindBoxPeerSnapshot(
                    peer_addr=CAROL_BARE,
                    peer_id=CAROL_BARE,
                    state=BlindBoxState(send_index=0),
                    root_secret=b"y" * 32,
                    root_epoch=1,
                )
            )

            snapshot = core.get_group_topology_snapshot(group_state.group_id)

            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            self.assertFalse(snapshot.group_blindbox_ready)
            self.assertTrue(snapshot.await_group_root)

            edges = {edge.target_id: edge for edge in snapshot.edges}
            self.assertEqual(edges[BOB_BARE].state.value, "await-root")
            self.assertFalse(edges[BOB_BARE].blindbox_ready)
            self.assertEqual(edges[CAROL_BARE].state.value, "blindbox")
            self.assertTrue(edges[CAROL_BARE].blindbox_ready)
