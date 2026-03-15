import asyncio
import unittest
from typing import Optional
from unittest.mock import AsyncMock

from i2p_chat_core import I2PChatCore


class _FakeReader:
    def __init__(self, payload: bytes) -> None:
        self._buffer = bytearray(payload)

    async def read(self, n: int = -1) -> bytes:
        if not self._buffer:
            return b""
        if n < 0 or n >= len(self._buffer):
            data = bytes(self._buffer)
            self._buffer.clear()
            return data
        data = bytes(self._buffer[:n])
        del self._buffer[:n]
        return data

    async def readexactly(self, n: int) -> bytes:
        if len(self._buffer) < n:
            partial = bytes(self._buffer)
            self._buffer.clear()
            raise asyncio.IncompleteReadError(partial=partial, expected=n)
        data = bytes(self._buffer[:n])
        del self._buffer[:n]
        return data


class _FakeWriter:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class AsyncioRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_protocol_downgrade_schedules_disconnect_without_reentrancy(self) -> None:
        errors: list[str] = []
        core = I2PChatCore(on_error=errors.append)
        core.handshake_complete = True
        core.use_encryption = True
        core.shared_key = b"x" * 32

        reader = _FakeReader(b"U0001x\n")
        writer = _FakeWriter()
        core.conn = (reader, writer)

        await core.receive_loop(core.conn)
        await asyncio.sleep(0)

        self.assertTrue(any("Protocol downgrade detected" in e for e in errors))
        self.assertIsNone(core.conn)
        self.assertTrue(writer.closed)
        self.assertFalse(core._recv_loop_active)

    async def test_schedule_disconnect_is_idempotent_while_task_running(self) -> None:
        core = I2PChatCore()
        core.conn = (_FakeReader(b""), _FakeWriter())
        disconnect_mock: AsyncMock = AsyncMock()
        core.disconnect = disconnect_mock  # type: ignore[method-assign]

        core._schedule_disconnect()
        core._schedule_disconnect()
        await asyncio.sleep(0)

        self.assertEqual(disconnect_mock.call_count, 1)

    async def test_pin_or_verify_waits_for_trust_decision_future(self) -> None:
        seen: Optional[tuple[str, str, str]] = None

        def trust_cb(peer: str, fp: str, key_hex: str) -> bool:
            nonlocal seen
            seen = (peer, fp, key_hex)
            return True

        core = I2PChatCore(on_trust_decision=trust_cb)
        ok = await core._pin_or_verify_peer_signing_key(
            "examplepeer.b32.i2p",
            b"\x11" * 32,
        )

        self.assertTrue(ok)
        self.assertIsNotNone(seen)
        self.assertIn("examplepeer.b32.i2p", core.peer_trusted_signing_keys)


if __name__ == "__main__":
    unittest.main()
