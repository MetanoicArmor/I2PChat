import asyncio
import socket
import unittest

from blindbox_client import BlindBoxClient


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    _, port = s.getsockname()
    s.close()
    return int(port)


class _ReplicaServer:
    def __init__(self, mode: str, storage: dict[str, bytes], *, flaky_first_put: bool = False) -> None:
        self.mode = mode
        self.storage = storage
        self.flaky_first_put = flaky_first_put
        self.put_calls = 0
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
            line = await reader.readline()
            parts = line.decode("utf-8", errors="ignore").strip().split()
            if not parts:
                return
            cmd = parts[0]
            if cmd == "PUT" and len(parts) >= 3:
                key = parts[1]
                size = int(parts[2])
                self.put_calls += 1
                if self.flaky_first_put and self.put_calls == 1:
                    writer.close()
                    await writer.wait_closed()
                    return
                body = await reader.readexactly(size)
                if self.mode == "error":
                    writer.write(b"ERR\n")
                elif key in self.storage:
                    writer.write(b"EXISTS\n")
                else:
                    self.storage[key] = body
                    writer.write(b"OK\n")
                await writer.drain()
                return
            if cmd == "GET" and len(parts) >= 2:
                key = parts[1]
                if key not in self.storage:
                    writer.write(b"MISS\n")
                    await writer.drain()
                    return
                data = self.storage[key]
                writer.write(f"OK {len(data)}\n".encode("utf-8"))
                writer.write(data)
                await writer.drain()
                return
            writer.write(b"ERR\n")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()


class BlindBoxClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_put_quorum_and_get_dedup(self) -> None:
        storage_a: dict[str, bytes] = {}
        storage_b: dict[str, bytes] = {}
        srv_a = _ReplicaServer("ok", storage_a)
        srv_b = _ReplicaServer("ok", storage_b)
        await srv_a.start()
        await srv_b.start()
        try:
            replicas = [f"127.0.0.1:{srv_a.port}", f"127.0.0.1:{srv_b.port}"]
            client = BlindBoxClient(
                session_id="test1",
                replicas=replicas,
                use_sam=False,
                put_quorum=2,
                get_quorum=1,
            )
            payload = b"hello-blindbox"
            results = await client.put("k1", payload)
            self.assertEqual(len(results), 2)
            blobs = await client.get("k1")
            self.assertEqual(blobs, [payload])
            await client.close()
        finally:
            await srv_a.stop()
            await srv_b.stop()

    async def test_get_quorum_failure(self) -> None:
        storage_a: dict[str, bytes] = {"k2": b"payload"}
        storage_b: dict[str, bytes] = {}
        srv_a = _ReplicaServer("ok", storage_a)
        srv_b = _ReplicaServer("ok", storage_b)
        await srv_a.start()
        await srv_b.start()
        try:
            client = BlindBoxClient(
                session_id="test2",
                replicas=[f"127.0.0.1:{srv_a.port}", f"127.0.0.1:{srv_b.port}"],
                use_sam=False,
                get_quorum=2,
            )
            with self.assertRaises(RuntimeError):
                await client.get("k2")
            await client.close()
        finally:
            await srv_a.stop()
            await srv_b.stop()

    async def test_retry_backoff_on_flaky_replica(self) -> None:
        storage_a: dict[str, bytes] = {}
        storage_b: dict[str, bytes] = {}
        srv_a = _ReplicaServer("ok", storage_a, flaky_first_put=True)
        srv_b = _ReplicaServer("ok", storage_b)
        await srv_a.start()
        await srv_b.start()
        try:
            client = BlindBoxClient(
                session_id="test3",
                replicas=[f"127.0.0.1:{srv_a.port}", f"127.0.0.1:{srv_b.port}"],
                use_sam=False,
                put_quorum=2,
                retry_attempts=3,
                retry_backoff_base=0.01,
            )
            await client.put("k3", b"payload-3")
            self.assertGreaterEqual(srv_a.put_calls, 2)
            await client.close()
        finally:
            await srv_a.stop()
            await srv_b.stop()


if __name__ == "__main__":
    unittest.main()
