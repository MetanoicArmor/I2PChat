import asyncio
import unittest

from i2pchat.sam.backend import (
    create_session,
    dest_lookup,
    naming_lookup,
    new_destination,
    stream_accept,
    stream_connect,
)
from i2pchat.sam.client import SAMClient, open_stream_accept, open_stream_connect
from i2pchat.sam.destination import Destination


def _sample_public_destination() -> bytes:
    return bytes((index % 256 for index in range(400)))


def _sample_private_destination(cert_len: int = 5) -> bytes:
    data = bytearray((index % 256 for index in range(420)))
    data[385:387] = cert_len.to_bytes(2, "big")
    return bytes(data)


class SamBackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_naming_lookup_uses_internal_client(self) -> None:
        seen: list[str] = []

        async def _handle(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            try:
                hello = await reader.readline()
                seen.append(hello.decode("utf-8").strip())
                writer.write(b"HELLO REPLY RESULT=OK VERSION=3.1\n")
                await writer.drain()

                lookup = await reader.readline()
                seen.append(lookup.decode("utf-8").strip())
                writer.write(b"NAMING REPLY RESULT=OK NAME=peer.i2p VALUE=destvalue\n")
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(_handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            value = await naming_lookup("peer.i2p", sam_address=("127.0.0.1", int(port)))
            self.assertEqual(value, "destvalue")
            self.assertEqual(seen[0], "HELLO VERSION MIN=3.0 MAX=3.2")
            self.assertEqual(seen[1], "NAMING LOOKUP NAME=peer.i2p")
        finally:
            server.close()
            await server.wait_closed()

    async def test_dest_lookup_wraps_destination(self) -> None:
        public_b64 = Destination(_sample_public_destination()).base64

        async def _handle(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            try:
                await reader.readline()
                writer.write(b"HELLO REPLY RESULT=OK VERSION=3.1\n")
                await writer.drain()

                await reader.readline()
                writer.write(
                    f"NAMING REPLY RESULT=OK NAME=peer.b32.i2p VALUE={public_b64}\n".encode(
                        "utf-8"
                    )
                )
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(_handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            looked_up = await dest_lookup(
                "peer.b32.i2p", sam_address=("127.0.0.1", int(port))
            )
            self.assertIsInstance(looked_up, Destination)
            self.assertEqual(looked_up.base64, public_b64)
        finally:
            server.close()
            await server.wait_closed()

    async def test_new_destination_returns_private_destination(self) -> None:
        public_b64 = Destination(_sample_public_destination()).base64
        private_b64 = Destination(
            _sample_private_destination(), has_private_key=True
        ).private_key.base64

        async def _handle(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            try:
                await reader.readline()
                writer.write(b"HELLO REPLY RESULT=OK VERSION=3.1\n")
                await writer.drain()

                request = await reader.readline()
                self.assertEqual(
                    request.decode("utf-8").strip(),
                    "DEST GENERATE SIGNATURE_TYPE=7",
                )
                writer.write(
                    (
                        "DEST REPLY RESULT=OK "
                        f"PUB={public_b64} "
                        f"PRIV={private_b64}\n"
                    ).encode("utf-8")
                )
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(_handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            generated = await new_destination(sam_address=("127.0.0.1", int(port)))
            self.assertIsInstance(generated, Destination)
            self.assertIsNotNone(generated.private_key)
            assert generated.private_key is not None
            self.assertEqual(generated.private_key.base64, private_b64)
        finally:
            server.close()
            await server.wait_closed()

    async def test_stream_connect_resolves_i2p_name_and_connects(self) -> None:
        public_b64 = Destination(_sample_public_destination()).base64
        seen: list[str] = []

        async def _handle(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            try:
                hello = await reader.readline()
                seen.append(hello.decode("utf-8").strip())
                writer.write(b"HELLO REPLY RESULT=OK VERSION=3.1\n")
                await writer.drain()

                command = await reader.readline()
                text = command.decode("utf-8").strip()
                seen.append(text)
                if text.startswith("NAMING LOOKUP"):
                    writer.write(
                        f"NAMING REPLY RESULT=OK NAME=peer.b32.i2p VALUE={public_b64}\n".encode(
                            "utf-8"
                        )
                    )
                    await writer.drain()
                    return
                writer.write(b"STREAM STATUS RESULT=OK\n")
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        lookup_server = await asyncio.start_server(_handle, "127.0.0.1", 0)
        stream_server = await asyncio.start_server(_handle, "127.0.0.1", 0)
        lookup_port = lookup_server.sockets[0].getsockname()[1]
        stream_port = stream_server.sockets[0].getsockname()[1]
        try:
            looked_up = await dest_lookup(
                "peer.b32.i2p", sam_address=("127.0.0.1", int(lookup_port))
            )
            self.assertEqual(looked_up.base64, public_b64)

            reader, writer = await stream_connect(
                "sess",
                "peer.b32.i2p",
                sam_address=("127.0.0.1", int(stream_port)),
            )
            writer.close()
            await writer.wait_closed()
            self.assertIsNotNone(reader)
        finally:
            lookup_server.close()
            stream_server.close()
            await lookup_server.wait_closed()
            await stream_server.wait_closed()

    async def test_stream_connect_uses_base64_for_raw_destination(self) -> None:
        public_b64 = Destination(_sample_public_destination()).base64
        seen: list[str] = []

        async def _handle(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            try:
                hello = await reader.readline()
                seen.append(hello.decode("utf-8").strip())
                writer.write(b"HELLO REPLY RESULT=OK VERSION=3.1\n")
                await writer.drain()

                command = await reader.readline()
                seen.append(command.decode("utf-8").strip())
                writer.write(b"STREAM STATUS RESULT=OK\n")
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(_handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            _reader, writer = await stream_connect(
                "sess",
                public_b64,
                sam_address=("127.0.0.1", int(port)),
            )
            writer.close()
            await writer.wait_closed()
            self.assertEqual(seen[0], "HELLO VERSION MIN=3.0 MAX=3.2")
            self.assertEqual(
                seen[1],
                f"STREAM CONNECT ID=sess DESTINATION={public_b64} SILENT=false",
            )
        finally:
            server.close()
            await server.wait_closed()

    async def test_stream_accept_uses_internal_client(self) -> None:
        seen: list[str] = []

        async def _handle(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            try:
                hello = await reader.readline()
                seen.append(hello.decode("utf-8").strip())
                writer.write(b"HELLO REPLY RESULT=OK VERSION=3.1\n")
                await writer.drain()

                command = await reader.readline()
                seen.append(command.decode("utf-8").strip())
                writer.write(b"STREAM STATUS RESULT=OK\n")
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(_handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            _reader, writer = await stream_accept(
                "sess",
                sam_address=("127.0.0.1", int(port)),
            )
            writer.close()
            await writer.wait_closed()
            self.assertEqual(seen[0], "HELLO VERSION MIN=3.0 MAX=3.2")
            self.assertEqual(seen[1], "STREAM ACCEPT ID=sess SILENT=false")
        finally:
            server.close()
            await server.wait_closed()

    async def test_stream_connect_closes_writer_on_status_error(self) -> None:
        holder: dict[str, asyncio.StreamWriter] = {}

        async def _handle(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            holder["writer"] = writer
            try:
                await reader.readline()
                writer.write(b"HELLO REPLY RESULT=OK VERSION=3.1\n")
                await writer.drain()

                await reader.readline()
                writer.write(
                    b"STREAM STATUS RESULT=CANT_REACH_PEER MESSAGE=offline\n"
                )
                await writer.drain()
                await asyncio.sleep(0)
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(_handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            with self.assertRaises(Exception):
                await stream_connect(
                    "sess",
                    Destination(_sample_public_destination()).base64,
                    sam_address=("127.0.0.1", int(port)),
                )
            await asyncio.sleep(0)
            self.assertTrue(holder["writer"].is_closing())
        finally:
            server.close()
            await server.wait_closed()

    async def test_stream_accept_closes_writer_on_status_error(self) -> None:
        holder: dict[str, asyncio.StreamWriter] = {}

        async def _handle(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            holder["writer"] = writer
            try:
                await reader.readline()
                writer.write(b"HELLO REPLY RESULT=OK VERSION=3.1\n")
                await writer.drain()

                await reader.readline()
                writer.write(b"STREAM STATUS RESULT=INVALID_ID MESSAGE=missing\n")
                await writer.drain()
                await asyncio.sleep(0)
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(_handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            with self.assertRaises(Exception):
                await stream_accept(
                    "sess",
                    sam_address=("127.0.0.1", int(port)),
                )
            await asyncio.sleep(0)
            self.assertTrue(holder["writer"].is_closing())
        finally:
            server.close()
            await server.wait_closed()

    async def test_create_session_keeps_control_socket_open(self) -> None:
        private_destination = Destination(
            _sample_private_destination(), has_private_key=True
        )
        seen: list[str] = []

        async def _handle(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            try:
                hello = await reader.readline()
                seen.append(hello.decode("utf-8").strip())
                writer.write(b"HELLO REPLY RESULT=OK VERSION=3.1\n")
                await writer.drain()

                command = await reader.readline()
                seen.append(command.decode("utf-8").strip())
                writer.write(b"SESSION STATUS RESULT=OK DESTINATION=TRANSIENT\n")
                await writer.drain()

                await reader.read(1)
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(_handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            reader, writer = await create_session(
                "sess",
                destination=private_destination,
                sam_address=("127.0.0.1", int(port)),
                options={
                    "inbound.length": "2",
                    "outbound.length": "2",
                },
            )
            self.assertIsNotNone(reader)
            self.assertFalse(writer.is_closing())
            self.assertEqual(seen[0], "HELLO VERSION MIN=3.0 MAX=3.2")
            self.assertTrue(
                seen[1].startswith(
                    "SESSION CREATE STYLE=STREAM ID=sess DESTINATION="
                )
            )
            self.assertIn("inbound.length=2", seen[1])
            self.assertIn("outbound.length=2", seen[1])
            writer.close()
            await writer.wait_closed()
        finally:
            server.close()
            await server.wait_closed()

    async def test_create_session_uses_transient_destination_when_omitted(self) -> None:
        seen: list[str] = []

        async def _handle(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            try:
                await reader.readline()
                writer.write(b"HELLO REPLY RESULT=OK VERSION=3.1\n")
                await writer.drain()

                command = await reader.readline()
                seen.append(command.decode("utf-8").strip())
                writer.write(b"SESSION STATUS RESULT=OK DESTINATION=TRANSIENT\n")
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(_handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            _reader, writer = await create_session(
                "sess",
                sam_address=("127.0.0.1", int(port)),
            )
            self.assertIn("DESTINATION=TRANSIENT", seen[0])
            self.assertIn("SIGNATURE_TYPE=7", seen[0])
            writer.close()
            await writer.wait_closed()
        finally:
            server.close()
            await server.wait_closed()

    async def test_create_session_closes_writer_on_status_error(self) -> None:
        holder: dict[str, asyncio.StreamWriter] = {}

        async def _handle(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            holder["writer"] = writer
            try:
                await reader.readline()
                writer.write(b"HELLO REPLY RESULT=OK VERSION=3.1\n")
                await writer.drain()

                await reader.readline()
                writer.write(b"SESSION STATUS RESULT=DUPLICATED_ID MESSAGE=dup\n")
                await writer.drain()
                await asyncio.sleep(0)
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(_handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            with self.assertRaises(Exception):
                await create_session(
                    "sess",
                    sam_address=("127.0.0.1", int(port)),
                )
            await asyncio.sleep(0)
            self.assertTrue(holder["writer"].is_closing())
        finally:
            server.close()
            await server.wait_closed()

    async def test_sam_client_open_closes_socket_on_hello_timeout(self) -> None:
        async def _slow_hello(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            try:
                await reader.readline()
                # Peer closes after hello timeout; unblock without a long sleep.
                await reader.read(65536)
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(_slow_hello, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            client = SAMClient(
                sam_address=("127.0.0.1", int(port)),
                hello_timeout=0.05,
                io_timeout=0.05,
            )
            with self.assertRaises(asyncio.TimeoutError):
                await client.open()
            self.assertIsNone(client._reader)
            self.assertIsNone(client._writer)
        finally:
            server.close()
            await server.wait_closed()

    async def test_open_stream_connect_closes_on_hello_timeout(self) -> None:
        async def _slow(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            try:
                await reader.readline()
                await reader.read(65536)
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(_slow, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            with self.assertRaises(asyncio.TimeoutError):
                await open_stream_connect(
                    "sess",
                    Destination(_sample_public_destination()).base64,
                    sam_address=("127.0.0.1", int(port)),
                    hello_timeout=0.05,
                    io_timeout=0.05,
                )
        finally:
            server.close()
            await server.wait_closed()

    async def test_open_stream_accept_closes_on_hello_timeout(self) -> None:
        async def _slow(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            try:
                await reader.readline()
                await reader.read(65536)
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(_slow, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            with self.assertRaises(asyncio.TimeoutError):
                await open_stream_accept(
                    "sess",
                    sam_address=("127.0.0.1", int(port)),
                    hello_timeout=0.05,
                    io_timeout=0.05,
                )
        finally:
            server.close()
            await server.wait_closed()

    async def test_open_stream_connect_closes_on_second_line_timeout(self) -> None:
        async def _hello_only(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            try:
                await reader.readline()
                writer.write(b"HELLO REPLY RESULT=OK VERSION=3.1\n")
                await writer.drain()
                # Do not answer STREAM CONNECT; wait until the client times out and drops the TCP session.
                loop = asyncio.get_running_loop()
                deadline = loop.time() + 0.5
                while loop.time() < deadline and not reader.at_eof():
                    await asyncio.sleep(0.02)
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(_hello_only, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            with self.assertRaises(asyncio.TimeoutError):
                await open_stream_connect(
                    "sess",
                    Destination(_sample_public_destination()).base64,
                    sam_address=("127.0.0.1", int(port)),
                    hello_timeout=1.0,
                    io_timeout=0.05,
                )
        finally:
            server.close()
            await server.wait_closed()


if __name__ == "__main__":
    unittest.main()
