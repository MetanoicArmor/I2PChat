import asyncio
import socket
import unittest
from unittest.mock import patch

from i2pchat.blindbox.blindbox_client import BlindBoxClient
from i2pchat.blindbox.blindbox_key_schedule import derive_blindbox_queue_capabilities
from i2plib.sam import session_create


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    _, port = s.getsockname()
    s.close()
    return int(port)


class _ReplicaServer:
    def __init__(
        self,
        mode: str,
        storage: dict[str, bytes],
        *,
        flaky_first_put: bool = False,
        required_token: str = "",
        get_header_override: str = "",
        supports_queue_caps: bool = False,
    ) -> None:
        self.mode = mode
        self.storage = storage
        self.flaky_first_put = flaky_first_put
        self.required_token = required_token
        self.get_header_override = get_header_override
        self.supports_queue_caps = supports_queue_caps
        self.put_calls = 0
        self.server: asyncio.AbstractServer | None = None
        self.port = _free_port()
        self.queues: dict[str, dict[str, object]] = {}

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
            if cmd == "CAPA" and len(parts) <= 2:
                if self.required_token:
                    if len(parts) < 2 or parts[1] != self.required_token:
                        writer.write(b"ERR\n")
                        await writer.drain()
                        return
                if self.supports_queue_caps:
                    writer.write(b"OK BLINDBOX_QUEUE_CAPS_V1\n")
                else:
                    writer.write(b"ERR\n")
                await writer.drain()
                return
            if cmd == "PUT" and len(parts) >= 3:
                if self.required_token:
                    if len(parts) < 4 or parts[3] != self.required_token:
                        writer.write(b"ERR\n")
                        await writer.drain()
                        return
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
                elif self.mode == "exists_no_store":
                    writer.write(b"EXISTS\n")
                elif key in self.storage:
                    writer.write(b"EXISTS\n")
                else:
                    self.storage[key] = body
                    writer.write(b"OK\n")
                await writer.drain()
                return
            if cmd == "GET" and len(parts) >= 2:
                if self.required_token:
                    if len(parts) < 3 or parts[2] != self.required_token:
                        writer.write(b"ERR\n")
                        await writer.drain()
                        return
                key = parts[1]
                if key not in self.storage:
                    writer.write(b"MISS\n")
                    await writer.drain()
                    return
                if self.get_header_override:
                    writer.write(f"{self.get_header_override}\n".encode("utf-8"))
                    await writer.drain()
                    return
                data = self.storage[key]
                writer.write(f"OK {len(data)}\n".encode("utf-8"))
                writer.write(data)
                await writer.drain()
                return
            if cmd == "QPUT" and len(parts) >= 7 and self.supports_queue_caps:
                if self.required_token:
                    if len(parts) < 8 or parts[7] != self.required_token:
                        writer.write(b"ERR\n")
                        await writer.drain()
                        return
                queue_id = parts[1]
                key = parts[2]
                size = int(parts[3])
                put_cap = parts[4]
                get_cap = parts[5]
                delete_cap = parts[6]
                self.put_calls += 1
                if self.flaky_first_put and self.put_calls == 1:
                    writer.close()
                    await writer.wait_closed()
                    return
                body = await reader.readexactly(size)
                queue = self.queues.get(queue_id)
                if queue is None:
                    queue = {
                        "put_cap": put_cap,
                        "get_cap": get_cap,
                        "delete_cap": delete_cap,
                        "items": {},
                    }
                    self.queues[queue_id] = queue
                elif (
                    queue["put_cap"] != put_cap
                    or queue["get_cap"] != get_cap
                    or queue["delete_cap"] != delete_cap
                ):
                    writer.write(b"ERR\n")
                    await writer.drain()
                    return
                items = queue["items"]
                assert isinstance(items, dict)
                if self.mode == "error":
                    writer.write(b"ERR\n")
                elif self.mode == "exists_no_store":
                    writer.write(b"EXISTS\n")
                elif key in items:
                    writer.write(b"EXISTS\n")
                else:
                    items[key] = body
                    writer.write(b"OK\n")
                await writer.drain()
                return
            if cmd == "QGET" and len(parts) >= 4 and self.supports_queue_caps:
                if self.required_token:
                    if len(parts) < 5 or parts[4] != self.required_token:
                        writer.write(b"ERR\n")
                        await writer.drain()
                        return
                queue = self.queues.get(parts[1])
                if queue is None or queue["get_cap"] != parts[3]:
                    writer.write(b"MISS\n")
                    await writer.drain()
                    return
                items = queue["items"]
                assert isinstance(items, dict)
                data = items.get(parts[2])
                if data is None:
                    writer.write(b"MISS\n")
                    await writer.drain()
                    return
                if self.get_header_override:
                    writer.write(f"{self.get_header_override}\n".encode("utf-8"))
                    await writer.drain()
                    return
                writer.write(f"OK {len(data)}\n".encode("utf-8"))
                writer.write(data)
                await writer.drain()
                return
            if cmd == "QDEL" and len(parts) >= 4 and self.supports_queue_caps:
                if self.required_token:
                    if len(parts) < 5 or parts[4] != self.required_token:
                        writer.write(b"ERR\n")
                        await writer.drain()
                        return
                queue = self.queues.get(parts[1])
                if queue is None or queue["delete_cap"] != parts[3]:
                    writer.write(b"MISS\n")
                    await writer.drain()
                    return
                items = queue["items"]
                assert isinstance(items, dict)
                if parts[2] in items:
                    del items[parts[2]]
                    writer.write(b"OK\n")
                else:
                    writer.write(b"MISS\n")
                await writer.drain()
                return
            writer.write(b"ERR\n")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()


class BlindBoxClientTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _queue_caps(epoch: int = 1):
        return derive_blindbox_queue_capabilities(
            b"\xaa" * 32,
            "alice.b32.i2p",
            "bob.b32.i2p",
            "send",
            epoch=epoch,
        )

    @staticmethod
    def _prime_queue(
        srv: _ReplicaServer,
        key: str,
        payload: bytes,
        queue_caps,
    ) -> None:
        srv.queues[queue_caps.queue_id] = {
            "put_cap": queue_caps.put_cap,
            "get_cap": queue_caps.get_cap,
            "delete_cap": queue_caps.delete_cap,
            "items": {key: payload},
        }

    async def test_start_uses_validated_session_create_command(self) -> None:
        lines: list[str] = []
        done = asyncio.Event()

        async def _handle_ctrl(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            try:
                hello = await reader.readline()
                if hello:
                    lines.append(hello.decode("utf-8", errors="ignore").strip())
                    writer.write(b"HELLO REPLY RESULT=OK VERSION=3.1\n")
                    await writer.drain()
                create_cmd = await reader.readline()
                if create_cmd:
                    lines.append(create_cmd.decode("utf-8", errors="ignore").strip())
                    writer.write(b"SESSION STATUS RESULT=OK DESTINATION=TRANSIENT\n")
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
                done.set()

        server = await asyncio.start_server(_handle_ctrl, "127.0.0.1", _free_port())
        sam_port = server.sockets[0].getsockname()[1]
        try:
            client = BlindBoxClient(
                session_id="sess",
                blind_boxes=["x.b32.i2p"],
                use_sam=True,
                sam_host="127.0.0.1",
                sam_port=int(sam_port),
                sam_options={
                    "inbound.length": "2",
                    "outbound.length": "2",
                },
            )
            with patch("i2pchat.blindbox.blindbox_client.secrets.token_hex", return_value="cafebabe"):
                await client.start()
            await client.close()
            await asyncio.wait_for(done.wait(), timeout=1.0)

            self.assertGreaterEqual(len(lines), 2, lines)
            self.assertEqual(lines[0], "HELLO VERSION MIN=3.0 MAX=3.2")
            expected = session_create(
                "STREAM",
                "sess_cafebabe",
                "TRANSIENT",
                "SIGNATURE_TYPE=7 OPTION inbound.length=2 outbound.length=2",
            ).decode("utf-8").strip()
            self.assertEqual(lines[1], expected)
        finally:
            server.close()
            await server.wait_closed()

    async def test_start_rejects_invalid_sam_options_via_validation(self) -> None:
        lines: list[str] = []
        done = asyncio.Event()

        async def _handle_ctrl(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            try:
                hello = await reader.readline()
                if hello:
                    lines.append(hello.decode("utf-8", errors="ignore").strip())
                    writer.write(b"HELLO REPLY RESULT=OK VERSION=3.1\n")
                    await writer.drain()
                maybe_create = await reader.readline()
                if maybe_create:
                    lines.append(maybe_create.decode("utf-8", errors="ignore").strip())
            finally:
                writer.close()
                await writer.wait_closed()
                done.set()

        server = await asyncio.start_server(_handle_ctrl, "127.0.0.1", _free_port())
        sam_port = server.sockets[0].getsockname()[1]
        try:
            client = BlindBoxClient(
                session_id="sess",
                blind_boxes=["x.b32.i2p"],
                use_sam=True,
                sam_host="127.0.0.1",
                sam_port=int(sam_port),
                sam_options={"inbound.length": "2\nBAD=1"},
            )
            with patch("i2pchat.blindbox.blindbox_client.secrets.token_hex", return_value="cafebabe"):
                with self.assertRaises(ValueError):
                    await client.start()
            await client.close()
            await asyncio.wait_for(done.wait(), timeout=1.0)

            self.assertEqual(lines, ["HELLO VERSION MIN=3.0 MAX=3.2"])
        finally:
            server.close()
            await server.wait_closed()

    async def test_put_quorum_and_get_dedup(self) -> None:
        storage_a: dict[str, bytes] = {}
        storage_b: dict[str, bytes] = {}
        srv_a = _ReplicaServer("ok", storage_a, supports_queue_caps=True)
        srv_b = _ReplicaServer("ok", storage_b, supports_queue_caps=True)
        await srv_a.start()
        await srv_b.start()
        try:
            boxes = [f"127.0.0.1:{srv_a.port}", f"127.0.0.1:{srv_b.port}"]
            client = BlindBoxClient(
                session_id="test1",
                blind_boxes=boxes,
                use_sam=False,
                put_quorum=2,
                get_quorum=1,
            )
            payload = b"hello-blindbox"
            queue_caps = self._queue_caps()
            results = await client.put("k1", payload, queue_caps=queue_caps)
            self.assertEqual(len(results), 2)
            blobs = await client.get("k1", queue_caps=queue_caps)
            self.assertEqual(blobs, [payload])
            await client.close()
        finally:
            await srv_a.stop()
            await srv_b.stop()

    async def test_get_quorum_failure(self) -> None:
        storage_a: dict[str, bytes] = {}
        storage_b: dict[str, bytes] = {}
        srv_a = _ReplicaServer("ok", storage_a, supports_queue_caps=True)
        srv_b = _ReplicaServer("ok", storage_b, supports_queue_caps=True)
        await srv_a.start()
        await srv_b.start()
        try:
            queue_caps = self._queue_caps()
            self._prime_queue(srv_a, "k2", b"payload", queue_caps)
            client = BlindBoxClient(
                session_id="test2",
                blind_boxes=[f"127.0.0.1:{srv_a.port}", f"127.0.0.1:{srv_b.port}"],
                use_sam=False,
                get_quorum=2,
            )
            with self.assertRaises(RuntimeError):
                await client.get("k2", queue_caps=queue_caps)
            await client.close()
        finally:
            await srv_a.stop()
            await srv_b.stop()

    async def test_put_rejects_unverified_exists_response(self) -> None:
        storage: dict[str, bytes] = {}
        srv = _ReplicaServer("exists_no_store", storage, supports_queue_caps=True)
        await srv.start()
        try:
            client = BlindBoxClient(
                session_id="test-exists",
                blind_boxes=[f"127.0.0.1:{srv.port}"],
                use_sam=False,
                put_quorum=1,
            )
            queue_caps = self._queue_caps()
            with self.assertRaises(RuntimeError):
                await client.put("k-exists", b"payload", queue_caps=queue_caps)
            await client.close()
        finally:
            await srv.stop()

    async def test_retry_backoff_on_flaky_blind_box(self) -> None:
        storage_a: dict[str, bytes] = {}
        storage_b: dict[str, bytes] = {}
        srv_a = _ReplicaServer("ok", storage_a, flaky_first_put=True, supports_queue_caps=True)
        srv_b = _ReplicaServer("ok", storage_b, supports_queue_caps=True)
        await srv_a.start()
        await srv_b.start()
        try:
            client = BlindBoxClient(
                session_id="test3",
                blind_boxes=[f"127.0.0.1:{srv_a.port}", f"127.0.0.1:{srv_b.port}"],
                use_sam=False,
                put_quorum=2,
                retry_attempts=3,
                retry_backoff_base=0.01,
            )
            await client.put("k3", b"payload-3", queue_caps=self._queue_caps())
            self.assertGreaterEqual(srv_a.put_calls, 2)
            await client.close()
        finally:
            await srv_a.stop()
            await srv_b.stop()

    def test_sam_destination_strips_i2p_port_suffix(self) -> None:
        b32 = (
            "dzyhukukogujr6r2vwfy667cwm7vg3oomhx2sryxhb6mn4i4wbjq.b32.i2p"
        )
        self.assertEqual(
            BlindBoxClient._sam_destination_from_endpoint(f"{b32}:19444"),
            b32,
        )
        self.assertEqual(BlindBoxClient._sam_destination_from_endpoint(b32), b32)

    def test_sam_destination_keeps_host_port(self) -> None:
        self.assertEqual(
            BlindBoxClient._sam_destination_from_endpoint("127.0.0.1:19444"),
            "127.0.0.1:19444",
        )

    def test_token_for_endpoint_uses_replica_auth_for_b32(self) -> None:
        c = BlindBoxClient(
            session_id="s",
            blind_boxes=["dzyhukukogujr6r2vwfy667cwm7vg3oomhx2sryxhb6mn4i4wbjq.b32.i2p:19444"],
            use_sam=False,
            local_auth_token="localonly",
            replica_auth={
                "dzyhukukogujr6r2vwfy667cwm7vg3oomhx2sryxhb6mn4i4wbjq.b32.i2p:19444": "perrep",
            },
        )
        addr = "dzyhukukogujr6r2vwfy667cwm7vg3oomhx2sryxhb6mn4i4wbjq.b32.i2p:19444"
        self.assertEqual(c._token_for_endpoint(addr), "perrep")
        self.assertEqual(c._command_auth_suffix(addr), " perrep")

    def test_token_for_endpoint_remote_without_map_empty(self) -> None:
        c = BlindBoxClient(
            session_id="s",
            blind_boxes=["x.b32.i2p:1"],
            use_sam=False,
            local_auth_token="loc",
        )
        self.assertEqual(c._token_for_endpoint("x.b32.i2p:1"), "")
        self.assertEqual(c._command_auth_suffix("x.b32.i2p:1"), "")

    def test_token_for_endpoint_loopback_without_map_uses_local(self) -> None:
        c = BlindBoxClient(
            session_id="s",
            blind_boxes=["127.0.0.1:1"],
            use_sam=False,
            local_auth_token="loc",
        )
        self.assertEqual(c._token_for_endpoint("127.0.0.1:1"), "loc")
        self.assertEqual(c._command_auth_suffix("127.0.0.1:1"), " loc")

    def test_sam_destination_rejects_injection_chars(self) -> None:
        with self.assertRaises(RuntimeError):
            BlindBoxClient._validate_sam_destination("abc\nINJECT=1")
        with self.assertRaises(RuntimeError):
            BlindBoxClient._validate_sam_destination("abc def")

    async def test_replica_auth_overrides_local_token_on_loopback(self) -> None:
        storage: dict[str, bytes] = {}
        srv = _ReplicaServer("ok", storage, required_token="mapped", supports_queue_caps=True)
        await srv.start()
        try:
            ep = f"127.0.0.1:{srv.port}"
            client = BlindBoxClient(
                session_id="test-auth-map",
                blind_boxes=[ep],
                use_sam=False,
                local_auth_token="wrong-token",
                replica_auth={ep: "mapped"},
            )
            payload = b"auth-map-payload"
            queue_caps = self._queue_caps()
            result = await client.put("k-am", payload, queue_caps=queue_caps)
            self.assertEqual(len(result), 1)
            blobs = await client.get("k-am", queue_caps=queue_caps)
            self.assertEqual(blobs, [payload])
            await client.close()
        finally:
            await srv.stop()

    async def test_local_auth_token_is_sent_to_loopback_replicas(self) -> None:
        storage: dict[str, bytes] = {}
        srv = _ReplicaServer("ok", storage, required_token="t123", supports_queue_caps=True)
        await srv.start()
        try:
            client = BlindBoxClient(
                session_id="test-auth",
                blind_boxes=[f"127.0.0.1:{srv.port}"],
                use_sam=False,
                local_auth_token="t123",
            )
            payload = b"auth-payload"
            queue_caps = self._queue_caps()
            result = await client.put("k-auth", payload, queue_caps=queue_caps)
            self.assertEqual(len(result), 1)
            blobs = await client.get("k-auth", queue_caps=queue_caps)
            self.assertEqual(blobs, [payload])
            await client.close()
        finally:
            await srv.stop()

    async def test_get_rejects_oversized_header_before_body_read(self) -> None:
        storage: dict[str, bytes] = {}
        srv = _ReplicaServer(
            "ok",
            storage,
            get_header_override="OK 999999999",
            supports_queue_caps=True,
        )
        await srv.start()
        try:
            queue_caps = self._queue_caps()
            self._prime_queue(srv, "k-big", b"x", queue_caps)
            client = BlindBoxClient(
                session_id="test-big",
                blind_boxes=[f"127.0.0.1:{srv.port}"],
                use_sam=False,
                retry_attempts=1,
                io_timeout=0.2,
            )
            with self.assertRaises(RuntimeError):
                await client.get("k-big", queue_caps=queue_caps)
            await client.close()
        finally:
            await srv.stop()

    async def test_get_rejects_malformed_size_header(self) -> None:
        storage: dict[str, bytes] = {}
        srv = _ReplicaServer(
            "ok",
            storage,
            get_header_override="OK not-a-number",
            supports_queue_caps=True,
        )
        await srv.start()
        try:
            queue_caps = self._queue_caps()
            self._prime_queue(srv, "k-bad", b"x", queue_caps)
            client = BlindBoxClient(
                session_id="test-bad-header",
                blind_boxes=[f"127.0.0.1:{srv.port}"],
                use_sam=False,
                retry_attempts=1,
            )
            with self.assertRaises(RuntimeError):
                await client.get("k-bad", queue_caps=queue_caps)
            await client.close()
        finally:
            await srv.stop()

    async def test_queue_capabilities_roundtrip_and_delete(self) -> None:
        storage: dict[str, bytes] = {}
        srv = _ReplicaServer("ok", storage, supports_queue_caps=True)
        await srv.start()
        try:
            client = BlindBoxClient(
                session_id="test-queue-caps",
                blind_boxes=[f"127.0.0.1:{srv.port}"],
                use_sam=False,
            )
            queue_caps = derive_blindbox_queue_capabilities(
                b"\x11" * 32,
                "alice.b32.i2p",
                "bob.b32.i2p",
                "send",
                epoch=3,
            )
            payload = b"queued-payload"
            result = await client.put("k-queue", payload, queue_caps=queue_caps)
            self.assertEqual(len(result), 1)
            blobs = await client.get("k-queue", queue_caps=queue_caps)
            self.assertEqual(blobs, [payload])
            deleted = await client.delete("k-queue", queue_caps=queue_caps)
            self.assertEqual(deleted, 1)
            blobs = await client.get(
                "k-queue",
                require_quorum=False,
                queue_caps=queue_caps,
            )
            self.assertEqual(blobs, [])
            await client.close()
        finally:
            await srv.stop()

    async def test_queue_capability_probe_uses_replica_auth_token(self) -> None:
        storage: dict[str, bytes] = {}
        srv = _ReplicaServer(
            "ok",
            storage,
            supports_queue_caps=True,
            required_token="mapped",
        )
        await srv.start()
        try:
            ep = f"127.0.0.1:{srv.port}"
            client = BlindBoxClient(
                session_id="test-queue-auth",
                blind_boxes=[ep],
                use_sam=False,
                replica_auth={ep: "mapped"},
            )
            queue_caps = derive_blindbox_queue_capabilities(
                b"\x23" * 32,
                "alice.b32.i2p",
                "bob.b32.i2p",
                "send",
                epoch=7,
            )
            payload = b"queue-auth-payload"
            result = await client.put("k-queue-auth", payload, queue_caps=queue_caps)
            self.assertEqual(len(result), 1)
            blobs = await client.get("k-queue-auth", queue_caps=queue_caps)
            self.assertEqual(blobs, [payload])
            await client.close()
        finally:
            await srv.stop()

    async def test_queue_capabilities_required_for_all_replicas(self) -> None:
        storage: dict[str, bytes] = {}
        srv = _ReplicaServer("ok", storage, supports_queue_caps=False)
        await srv.start()
        try:
            client = BlindBoxClient(
                session_id="test-queue-required",
                blind_boxes=[f"127.0.0.1:{srv.port}"],
                use_sam=False,
            )
            queue_caps = derive_blindbox_queue_capabilities(
                b"\x22" * 32,
                "alice.b32.i2p",
                "bob.b32.i2p",
                "send",
                epoch=7,
            )
            payload = b"must-fail-without-queue-caps"
            with self.assertRaises(RuntimeError):
                await client.put("k-no-queue-caps", payload, queue_caps=queue_caps)
            await client.close()
        finally:
            await srv.stop()


if __name__ == "__main__":
    unittest.main()
