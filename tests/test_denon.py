from __future__ import annotations

import asyncio
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests import PY_MODULES  # noqa: F401
from avr_control import ssdp
from avr_control.drivers.base import MODE, MUTE, POWER, VOLUME, DriverError, Refused
from avr_control.drivers.denon import DenonDriver


def encode_volume(raw: int) -> str:
    whole, half = divmod(raw, 2)
    return f"{whole:02d}{'5' if half else ''}"


def decode_volume(value: str) -> int:
    if len(value) == 2:
        return int(value) * 2
    return int(value[:2]) * 2 + (value[-1] == "5")


class FakeDenon:
    def __init__(self, *, strict_single_client: bool = False):
        self.volume = 91
        self.maximum = 139
        self.muted = False
        self.power = True
        self.mode = "STEREO"
        self.refused_mode = False
        self.strict_single_client = strict_single_client
        self.commands: list[str] = []
        self.connections = 0
        self.rejected_connections = 0
        self.active_connections = 0
        self.max_active_connections = 0
        self.server = None

    async def start(self, host: str = "127.0.0.1", port: int = 0):
        self.server = await asyncio.start_server(self.handle, host, port)
        return self.server.sockets[0].getsockname()[1]

    async def close(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def send(self, writer: asyncio.StreamWriter, line: str):
        writer.write(line.encode("ascii") + b"\r")
        await writer.drain()

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        self.connections += 1
        self.active_connections += 1
        self.max_active_connections = max(
            self.max_active_connections, self.active_connections
        )
        if self.strict_single_client and self.active_connections > 1:
            self.rejected_connections += 1
            writer.close()
            await writer.wait_closed()
            self.active_connections -= 1
            return
        await self.send(writer, "SISAT/CBL")
        try:
            while True:
                command = (await reader.readuntil(b"\r"))[:-1].decode("ascii").strip()
                self.commands.append(command)
                if command.endswith("?"):
                    await self.send(writer, "SISAT/CBL")
                if command == "PW?":
                    await self.send(writer, "PWON" if self.power else "PWSTANDBY")
                elif command == "MV?":
                    if self.power:
                        await self.send(writer, f"MV{encode_volume(self.volume)}")
                        await self.send(writer, f"MVMAX {encode_volume(self.maximum)}")
                elif command == "MU?":
                    if self.power:
                        await self.send(writer, "MUON" if self.muted else "MUOFF")
                elif command == "MS?":
                    if self.power:
                        await self.send(writer, f"MS{self.mode}")
                elif command.startswith("MV") and command not in {
                    "MV?",
                    "MVUP",
                    "MVDOWN",
                }:
                    requested = decode_volume(command[2:])
                    self.volume = min(requested, self.maximum)
                    await self.send(writer, f"MV{encode_volume(self.volume)}")
                    await self.send(writer, f"MVMAX {encode_volume(self.maximum)}")
                elif command == "MUON":
                    self.muted = True
                    await self.send(writer, "MUON")
                elif command == "MUOFF":
                    self.muted = False
                    await self.send(writer, "MUOFF")
                elif command == "PWON":
                    self.power = True
                    await self.send(writer, "PWON")
                elif command == "PWSTANDBY":
                    self.power = False
                    await self.send(writer, "PWSTANDBY")
                elif command.startswith("MS"):
                    requested_mode = command[2:]
                    if self.refused_mode:
                        await self.send(writer, "MSERROR")
                    else:
                        self.mode = requested_mode
                        await self.send(writer, f"MS{self.mode}")
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self.active_connections -= 1
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


class FakeWrongProtocol:
    async def start(self):
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def handle(self, reader, writer):
        try:
            await reader.readuntil(b"\r")
            writer.write(b"NOT A DENON\r")
            await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def close(self):
        self.server.close()
        await self.server.wait_closed()


class FakeDenonSsdpResponder(asyncio.DatagramProtocol):
    def __init__(self, location: str):
        self.location = location
        self.requests: list[str] = []
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data: bytes, address):
        self.requests.append(data.decode("ascii", errors="ignore"))
        response = (
            "HTTP/1.1 200 OK\r\n"
            f"LOCATION: {self.location}\r\n"
            "USN: uuid:denon-device::upnp:rootdevice\r\n"
            "SERVER: Denon/1.0 UPnP/1.1\r\n"
            "ST: upnp:rootdevice\r\n\r\n"
        ).encode("ascii")
        self.transport.sendto(response, address)
        self.transport.sendto(response, address)


class DenonDescriptionHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = (
            '<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0">'
            "<device><friendlyName>Living Room Denon AVR</friendlyName>"
            "<manufacturer>Denon</manufacturer><modelName>AVR-X3800H</modelName>"
            "<UDN>uuid:denon-device</UDN></device></root>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/xml")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *args):
        pass


class DenonDriverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fake = FakeDenon()
        self.port = await self.fake.start()
        self.config = {"host": "127.0.0.1", "port": self.port}
        self.driver = DenonDriver(self.config)

    async def asyncTearDown(self):
        await self.fake.close()

    async def test_capabilities_status_and_setters(self):
        self.assertEqual(self.driver.capabilities, {VOLUME, MUTE, POWER, MODE})
        status = await self.driver.status()
        self.assertEqual(
            (status.volume, status.muted, status.power, status.mode),
            (91, False, True, "STEREO"),
        )
        self.assertEqual(self.driver.scale.max, 139)
        self.assertEqual(self.driver.scale.display(91), -34.5)

        self.assertEqual(await self.driver.set_volume(91), 91)
        self.assertIn("MV455", self.fake.commands)
        self.assertEqual(await self.driver.set_volume(196), 139)
        self.assertEqual(self.fake.volume, 139)
        self.assertTrue(await self.driver.set_mute(True))
        self.assertFalse(await self.driver.set_mute(False))
        self.assertFalse(await self.driver.set_power(False))
        self.assertTrue(await self.driver.set_power(True))
        self.assertEqual(await self.driver.set_mode("DTS SURROUND"), "DTS SURROUND")
        self.assertEqual(await self.driver.get_mode(), "DTS SURROUND")
        self.assertEqual(self.fake.mode, "DTS SURROUND")
        self.assertLessEqual(self.fake.max_active_connections, 1)

    async def test_standby_reports_only_power(self):
        self.fake.power = False
        status = await self.driver.status()
        self.assertEqual(status.power, False)
        self.assertIsNone(status.volume)
        self.assertIsNone(status.muted)
        self.assertIsNone(status.mode)

    async def test_mode_refusal_and_names(self):
        self.fake.refused_mode = True
        with self.assertRaisesRegex(Refused, "didn't accept"):
            await self.driver.set_mode("DIRECT")
        self.assertEqual(self.driver.mode_name("DOLBY D+ +DSUR"), "Dolby D+ +Dsur")
        modes = await self.driver.list_modes()
        self.assertIn(("PURE DIRECT", "Pure Direct"), [(m.code, m.name) for m in modes])

    async def test_probe_accepts_denon_and_rejects_refused_or_wrong_protocol(self):
        found = await DenonDriver.probe(self.config)
        self.assertIsNotNone(found)
        self.assertEqual(found.driver, "denon")
        self.assertEqual(found.id, "denon-127.0.0.1")
        self.assertEqual(found.config["port"], self.port)

        wrong = FakeWrongProtocol()
        wrong_port = await wrong.start()
        try:
            self.assertIsNone(
                await DenonDriver.probe({"host": "127.0.0.1", "port": wrong_port})
            )
        finally:
            await wrong.close()

        reserve = await asyncio.start_server(
            lambda _r, w: w.close(), "127.0.0.1", 0
        )
        refused_port = reserve.sockets[0].getsockname()[1]
        reserve.close()
        await reserve.wait_closed()
        self.assertIsNone(
            await DenonDriver.probe(
                {"host": "127.0.0.1", "port": refused_port}
            )
        )

    async def test_discovery_uses_ssdp_description_and_deduplicates(self):
        http_server = ThreadingHTTPServer(("127.0.0.1", 0), DenonDescriptionHandler)
        thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        thread.start()
        location = f"http://127.0.0.1:{http_server.server_port}/device.xml"
        loop = asyncio.get_running_loop()
        transport, responder = await loop.create_datagram_endpoint(
            lambda: FakeDenonSsdpResponder(location), local_addr=("127.0.0.1", 0)
        )
        old_address = ssdp.SSDP_ADDRESS
        try:
            ssdp.SSDP_ADDRESS = transport.get_extra_info("sockname")
            found = await DenonDriver.discover(0.1)
        finally:
            ssdp.SSDP_ADDRESS = old_address
            transport.close()
            http_server.shutdown()
            http_server.server_close()
            thread.join(timeout=2)

        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].model, "AVR-X3800H")
        self.assertEqual(found[0].id, "uuid:denon-device")
        self.assertEqual(found[0].config, {"host": "127.0.0.1"})
        self.assertTrue(any("M-SEARCH" in request for request in responder.requests))

    async def test_unrelated_event_lines_do_not_replace_query_responses(self):
        status = await self.driver.status()
        self.assertEqual(status.power, True)
        self.assertEqual(status.volume, 91)
        self.assertEqual(status.mode, "STEREO")
        self.assertNotIn("SISAT/CBL", self.fake.commands)

    async def test_status_and_command_share_one_receiver_connection_at_a_time(self):
        fake = FakeDenon(strict_single_client=True)
        port = await fake.start()
        try:
            config = {"host": "127.0.0.1", "port": port}
            status_driver = DenonDriver(config)
            command_driver = DenonDriver(config)
            status, muted = await asyncio.gather(
                status_driver.status(), command_driver.set_mute(True)
            )

            self.assertTrue(status.power)
            self.assertTrue(muted)
            self.assertEqual(fake.rejected_connections, 0)
            self.assertEqual(fake.max_active_connections, 1)
            self.assertIn("MUON", fake.commands)
        finally:
            await fake.close()

    async def test_decoder_specific_modes_report_a_settable_canonical_mode(self):
        mapped_modes = (
            ("DOLBY D+ +DSUR", "DOLBY DIGITAL", "Dolby Digital"),
            ("DTS HD+DSUR", "DTS SURROUND", "DTS Surround"),
            ("DOLBY PLII MUSIC", "MUSIC", "Music"),
            ("DTS NEO:X C", "MOVIE", "Movie"),
            ("MULTI CH STEREO", "MCH STEREO", "Multi-Channel Stereo"),
            ("AUTO", "AUTO", "Auto"),
        )
        for reported, canonical, name in mapped_modes:
            with self.subTest(reported=reported):
                self.fake.mode = reported
                status = await self.driver.status()
                self.assertEqual(status.mode, canonical)
                self.assertEqual(self.driver.mode_name(status.mode), name)

        self.assertEqual(await self.driver.set_mode("DOLBY DIGITAL"), "DOLBY DIGITAL")
        self.assertIn("MSDOLBY DIGITAL", self.fake.commands)

    async def test_unknown_mode_is_sent_and_only_refused_by_receiver(self):
        self.fake.mode = "CUSTOM MODE"
        status = await self.driver.status()
        self.assertEqual(status.mode, "CUSTOM MODE")
        self.assertEqual(await self.driver.set_mode(status.mode), "CUSTOM MODE")
        self.assertIn("MSCUSTOM MODE", self.fake.commands)

        self.fake.refused_mode = True
        with self.assertRaisesRegex(Refused, "didn't accept"):
            await self.driver.set_mode("CUSTOM MODE")
        self.assertEqual(self.fake.commands.count("MSCUSTOM MODE"), 2)


if __name__ == "__main__":
    unittest.main()
