from __future__ import annotations

import asyncio
import re
import struct
import xml.etree.ElementTree as ET

from avr_control.drivers.eiscp import packet


class FakeEiscp:
    def __init__(self):
        self.volume = 48
        self.muted = False
        self.power = True
        self.mode = "00"
        self.standby = False
        self.refused_mode: str | None = None
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
                        await self.send(writer, f"MVL{self.volume:02X}")
                elif command == "AMTQSTN":
                    if not self.standby:
                        await self.send(writer, f"AMT{'01' if self.muted else '00'}")
                elif command == "LMDQSTN":
                    if not self.standby:
                        await self.send(writer, f"LMD{self.mode}")
                elif command == "PWRQSTN":
                    await self.send(writer, f"PWR{'01' if self.power else '00'}")
                elif command.startswith("MVL"):
                    self.volume = int(command[3:], 16)
                    await self.send(writer, f"MVL{self.volume:02X}")
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
                    response = f"VOL{self.volume:03d}"
                elif command == "?M":
                    response = f"MUT{'0' if self.muted else '1'}"
                elif command == "?P":
                    response = f"PWR{'0' if self.power else '1'}"
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
                    response = "PWR1"
                elif command.endswith("VL"):
                    self.volume = int(command[:-2])
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
