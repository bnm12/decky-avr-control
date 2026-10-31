from __future__ import annotations

import asyncio
import re
import struct
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable

from avr_control.drivers.eiscp import packet


_MEMORY_EOF = object()


class InMemoryStreamReader:
    def __init__(self):
        self._chunks: asyncio.Queue = asyncio.Queue()
        self._buffer = bytearray()
        self._eof = False

    def feed_data(self, data: bytes) -> None:
        self._chunks.put_nowait(data)

    def feed_eof(self) -> None:
        if not self._eof:
            self._eof = True
            self._chunks.put_nowait(_MEMORY_EOF)

    async def readexactly(self, count: int) -> bytes:
        while len(self._buffer) < count:
            if self._eof and self._chunks.empty():
                partial = bytes(self._buffer)
                self._buffer.clear()
                raise asyncio.IncompleteReadError(partial, count)
            chunk = await self._chunks.get()
            if chunk is _MEMORY_EOF:
                continue
            self._buffer.extend(chunk)
        result = bytes(self._buffer[:count])
        del self._buffer[:count]
        return result

    async def readuntil(self, separator: bytes = b"\n") -> bytes:
        while True:
            position = self._buffer.find(separator)
            if position >= 0:
                end = position + len(separator)
                result = bytes(self._buffer[:end])
                del self._buffer[:end]
                return result
            if self._eof and self._chunks.empty():
                partial = bytes(self._buffer)
                self._buffer.clear()
                raise asyncio.IncompleteReadError(partial, None)
            chunk = await self._chunks.get()
            if chunk is _MEMORY_EOF:
                continue
            self._buffer.extend(chunk)

    async def readline(self) -> bytes:
        try:
            return await self.readuntil(b"\n")
        except asyncio.IncompleteReadError as exc:
            return exc.partial


class InMemoryStreamWriter:
    def __init__(
        self,
        peer: InMemoryStreamReader,
        on_write: Callable[[bytes], None] | None = None,
    ):
        self._peer = peer
        self._on_write = on_write
        self._closed = False

    def write(self, data: bytes) -> None:
        if self._closed:
            raise ConnectionError("memory stream is closed")
        if self._on_write:
            self._on_write(data)
        self._peer.feed_data(bytes(data))

    async def drain(self) -> None:
        await asyncio.sleep(0)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._peer.feed_eof()

    def is_closing(self) -> bool:
        return self._closed

    async def wait_closed(self) -> None:
        await asyncio.sleep(0)


class InMemoryDuplex:
    """A duplex-stream adapter with the subset used by asyncio stream drivers."""

    def __init__(
        self, handler: Callable[[InMemoryStreamReader, InMemoryStreamWriter], Awaitable[None]]
    ):
        self.handler = handler
        self.connections: list[tuple[InMemoryStreamReader, InMemoryStreamWriter]] = []
        self.client_writes: list[tuple[int, float, bytes]] = []
        self._tasks: set[asyncio.Task] = set()

    async def connect(self):
        client_reader = InMemoryStreamReader()
        server_reader = InMemoryStreamReader()
        connection_index = len(self.connections)

        def record(data: bytes) -> None:
            self.client_writes.append(
                (connection_index, asyncio.get_running_loop().time(), bytes(data))
            )

        client_writer = InMemoryStreamWriter(server_reader, record)
        server_writer = InMemoryStreamWriter(client_reader)
        self.connections.append((client_reader, client_writer))
        task = asyncio.create_task(self.handler(server_reader, server_writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return client_reader, client_writer

    async def close(self) -> None:
        for _reader, writer in self.connections:
            writer.close()
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)


class FakeEiscp:
    def __init__(self):
        self.volume = 48
        self.muted = False
        self.power = True
        self.mode = "00"
        self.standby = False
        self.refused_mode: str | None = None
        self.status_delay = 0.0
        self.report_power = True
        self.stale_volume_before_ack: int | None = None
        self.suppress_volume_ack = False
        self.server = None
        self.connections = 0

    async def start(self, host: str = "127.0.0.1", port: int = 0):
        self.server = await asyncio.start_server(self.handle, host, port)
        return self.server.sockets[0].getsockname()[1]

    async def close(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def send(self, writer, message: str):
        writer.write(packet(message))
        await writer.drain()

    async def send_later(self, writer, message: str):
        await asyncio.sleep(self.status_delay)
        try:
            await self.send(writer, message)
        except (ConnectionError, OSError):
            pass

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.connections += 1
        await self.send(writer, "!1NJA2-http://127.0.0.1/album_art.cgi")
        try:
            while True:
                header = await reader.readexactly(16)
                header_size, data_size = struct.unpack(">II", header[4:12])
                if header_size > 16:
                    await reader.readexactly(header_size - 16)
                command = (await reader.readexactly(data_size)).decode("ascii").strip()
                command = re.sub(r"^![0-9]", "", command)
                if command == "MVLQSTN":
                    if not self.standby:
                        response = f"MVL{self.volume:02X}"
                        if self.status_delay:
                            asyncio.create_task(self.send_later(writer, response))
                        else:
                            await self.send(writer, response)
                elif command == "AMTQSTN":
                    if not self.standby:
                        response = f"AMT{'01' if self.muted else '00'}"
                        if self.status_delay:
                            asyncio.create_task(self.send_later(writer, response))
                        else:
                            await self.send(writer, response)
                elif command == "LMDQSTN":
                    if not self.standby:
                        await self.send(writer, f"LMD{self.mode}")
                elif command == "PWRQSTN":
                    if self.report_power:
                        await self.send(writer, f"PWR{'01' if self.power else '00'}")
                elif command.startswith("MVL"):
                    stale_volume = self.stale_volume_before_ack
                    self.stale_volume_before_ack = None
                    self.volume = int(command[3:], 16)
                    if stale_volume is not None:
                        await self.send(writer, f"!1MVL{stale_volume:02X}")
                    if not self.suppress_volume_ack:
                        await self.send(writer, f"MVL{self.volume:02X}")
                    self.suppress_volume_ack = False
                elif command.startswith("AMT"):
                    self.muted = command[3:] == "01"
                    await self.send(writer, f"AMT{'01' if self.muted else '00'}")
                elif command == "PWR01" or command == "PWR00":
                    self.power = command == "PWR01"
                    await self.send(writer, command)
                elif command.startswith("LMD"):
                    code = command[3:].upper()
                    if code != self.refused_mode:
                        self.mode = code
                        await self.send(writer, f"LMD{self.mode}")
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


class FakePioneerTelnet:
    def __init__(self):
        self.volume = 48
        self.muted = False
        self.power = True
        self.off_power_reply = "2"
        self.report_volume = True
        self.report_mute = True
        self.stale_volume_before_ack: int | None = None
        self.server = None

    async def start(self, host: str = "127.0.0.1", port: int = 0):
        self.server = await asyncio.start_server(self.handle, host, port)
        return self.server.sockets[0].getsockname()[1]

    async def close(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                command = line.decode("ascii").strip()
                if command == "?V":
                    if not self.report_volume:
                        continue
                    response = f"VOL{self.volume:03d}"
                elif command == "?M":
                    if not self.report_mute:
                        continue
                    response = f"MUT{'0' if self.muted else '1'}"
                elif command == "?P":
                    response = f"PWR{'0' if self.power else self.off_power_reply}"
                elif command == "MO":
                    self.muted = True
                    response = "MUT0"
                elif command == "MF":
                    self.muted = False
                    response = "MUT1"
                elif command == "PO":
                    self.power = True
                    response = "PWR0"
                elif command == "PF":
                    self.power = False
                    response = f"PWR{self.off_power_reply}"
                elif command.endswith("VL"):
                    requested = int(command[:-2])
                    stale_volume = self.stale_volume_before_ack
                    self.stale_volume_before_ack = None
                    if stale_volume is not None:
                        writer.write(f"VOL{stale_volume:03d}\r\n".encode("ascii"))
                        await writer.drain()
                    self.volume = requested
                    response = f"VOL{self.volume:03d}"
                else:
                    continue
                writer.write((response + "\r\n").encode("ascii"))
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


class FakeEiscpDiscovery(asyncio.DatagramProtocol):
    def __init__(self):
        self.transport = None
        self.probes = []

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data: bytes, address):
        self.probes.append(data)
        payload = "!1ECNVSX-935/60128/XX/0009B0617D30\r"
        response = packet(payload)
        self.transport.sendto(response, address)
        self.transport.sendto(response, address)


class FakeSsdpResponder(asyncio.DatagramProtocol):
    def __init__(self, location: str):
        self.transport = None
        self.requests = []
        self.location = location

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data: bytes, address):
        self.requests.append(data.decode("ascii", errors="ignore"))
        response = (
            "HTTP/1.1 200 OK\r\n"
            f"LOCATION: {self.location}\r\n"
            "USN: uuid:device-1::upnp:rootdevice\r\n"
            "SERVER: Fake/1.0 UPnP/1.1\r\n"
            "ST: upnp:rootdevice\r\n\r\n"
        ).encode("ascii")
        self.transport.sendto(response, address)
        self.transport.sendto(response, address)
