import asyncio
import socket
import unittest

from i2pchat.blindbox.blindbox_local_replica import (
    BLINDBOX_LOCAL_REPLICA_MAGIC,
    BlindBoxLocalReplicaServer,
    _probe_existing_local_replica,
)


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    _host, port = sock.getsockname()
    sock.close()
    return int(port)


class _ProbeRecorderServer:
    def __init__(self, responses: list[bytes]) -> None:
        self.responses = list(responses)
        self.lines: list[str] = []
        self.server: asyncio.AbstractServer | None = None
        self.port = _free_port()

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", self.port)

    async def stop(self) -> None:
        if self.server is None:
            return
        self.server.close()
        await self.server.wait_closed()
        self.server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                line = await reader.readline()
                if not line:
                    return
                self.lines.append(line.decode("utf-8", errors="ignore").strip())
                if self.responses:
                    writer.write(self.responses.pop(0))
                    await writer.drain()
                else:
                    return
        finally:
            writer.close()
            await writer.wait_closed()


class BlindBoxLocalReplicaTests(unittest.IsolatedAsyncioTestCase):
    async def test_probe_rejects_fake_service_that_returns_err(self) -> None:
        fake = _ProbeRecorderServer([b"ERR\n"])
        await fake.start()
        try:
            ok = await _probe_existing_local_replica(
                "127.0.0.1",
                fake.port,
                auth_token="secret-token",
            )
            self.assertFalse(ok)
            self.assertEqual(fake.lines, ["PING"])
        finally:
            await fake.stop()

    async def test_probe_authenticates_only_after_valid_magic(self) -> None:
        fake = _ProbeRecorderServer(
            [
                f"{BLINDBOX_LOCAL_REPLICA_MAGIC}\n".encode("utf-8"),
                b"OK\n",
            ]
        )
        await fake.start()
        try:
            ok = await _probe_existing_local_replica(
                "127.0.0.1",
                fake.port,
                auth_token="secret-token",
            )
            self.assertTrue(ok)
            self.assertEqual(fake.lines, ["PING", "AUTH secret-token"])
        finally:
            await fake.stop()

    async def test_real_server_with_token_passes_ping_then_auth(self) -> None:
        server = BlindBoxLocalReplicaServer(port=_free_port(), auth_token="t123")
        started = await server.start()
        self.assertTrue(started)
        try:
            self.assertTrue(
                await _probe_existing_local_replica(
                    "127.0.0.1",
                    server.port,
                    auth_token="t123",
                )
            )
        finally:
            await server.stop()

    async def test_real_server_without_token_passes_ping_only(self) -> None:
        server = BlindBoxLocalReplicaServer(port=_free_port())
        started = await server.start()
        self.assertTrue(started)
        try:
            self.assertTrue(
                await _probe_existing_local_replica(
                    "127.0.0.1",
                    server.port,
                    auth_token="",
                )
            )
        finally:
            await server.stop()

    async def test_real_server_queue_capabilities_roundtrip(self) -> None:
        server = BlindBoxLocalReplicaServer(port=_free_port(), auth_token="q123")
        started = await server.start()
        self.assertTrue(started)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
            writer.write(b"CAPA q123\n")
            await writer.drain()
            self.assertEqual(
                (await reader.readline()).decode("utf-8", errors="ignore").strip(),
                "OK BLINDBOX_QUEUE_CAPS_V1",
            )
            writer.close()
            await writer.wait_closed()

            queue = "queue-a"
            key = "item-1"
            payload = b"hello-queue"
            put_cap = "put-cap"
            get_cap = "get-cap"
            delete_cap = "delete-cap"

            reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
            writer.write(
                (
                    f"QPUT {queue} {key} {len(payload)} "
                    f"{put_cap} {get_cap} {delete_cap} q123\n"
                ).encode("utf-8")
            )
            writer.write(payload)
            await writer.drain()
            self.assertEqual(
                (await reader.readline()).decode("utf-8", errors="ignore").strip(),
                "OK",
            )
            writer.close()
            await writer.wait_closed()

            reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
            writer.write(f"QGET {queue} {key} {get_cap} q123\n".encode("utf-8"))
            await writer.drain()
            self.assertEqual(
                (await reader.readline()).decode("utf-8", errors="ignore").strip(),
                f"OK {len(payload)}",
            )
            self.assertEqual(await reader.readexactly(len(payload)), payload)
            writer.close()
            await writer.wait_closed()

            reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
            writer.write(f"QDEL {queue} {key} {delete_cap} q123\n".encode("utf-8"))
            await writer.drain()
            self.assertEqual(
                (await reader.readline()).decode("utf-8", errors="ignore").strip(),
                "OK",
            )
            writer.close()
            await writer.wait_closed()
        finally:
            await server.stop()


if __name__ == "__main__":
    unittest.main()
