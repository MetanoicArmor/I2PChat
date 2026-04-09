import asyncio
import types
import unittest
from unittest.mock import AsyncMock

from i2pchat.core.i2p_chat_core import I2PChatCore
from i2pchat.core.session_manager import TransportState


class ShutdownCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_disconnects_and_cleans_runtime(self) -> None:
        core = I2PChatCore(profile="alice")
        core.conn = (object(), object())
        core.disconnect = AsyncMock()  # type: ignore[method-assign]
        core.session_manager.cancel_tasks_and_close_session = AsyncMock()  # type: ignore[method-assign]

        running_task = asyncio.create_task(asyncio.sleep(60))
        core._blindbox_task = running_task  # noqa: SLF001 - runtime cleanup coverage
        close_mock = AsyncMock()
        core._blindbox_client = types.SimpleNamespace(close=close_mock)  # noqa: SLF001

        await core.shutdown()

        core.disconnect.assert_awaited_once()  # type: ignore[attr-defined]
        core.session_manager.cancel_tasks_and_close_session.assert_awaited_once()  # type: ignore[attr-defined]
        close_mock.assert_awaited_once()
        self.assertIsNone(core._blindbox_task)  # noqa: SLF001
        self.assertIsNone(core._blindbox_client)  # noqa: SLF001
        self.assertTrue(running_task.done())
        self.assertEqual(core.session_manager.transport_state, TransportState.STOPPED)

    async def test_shutdown_skips_disconnect_without_connection(self) -> None:
        core = I2PChatCore(profile="alice")
        core.disconnect = AsyncMock()  # type: ignore[method-assign]
        core.session_manager.cancel_tasks_and_close_session = AsyncMock()  # type: ignore[method-assign]

        await core.shutdown()

        core.disconnect.assert_not_awaited()  # type: ignore[attr-defined]
        core.session_manager.cancel_tasks_and_close_session.assert_awaited_once()  # type: ignore[attr-defined]
        self.assertEqual(core.session_manager.transport_state, TransportState.STOPPED)


if __name__ == "__main__":
    unittest.main()
