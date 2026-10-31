from __future__ import annotations

import asyncio
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from tests import PY_MODULES  # noqa: F401
from avr_control import ssdp
from avr_control.drivers.base import (
    MODE,
    MUTE,
    POWER,
    VOLUME,
    DriverError,
    ModeState,
    Refused,
)
from avr_control.drivers.denon import DenonDriver


def encode_volume(raw: int) -> str:
    whole, half = divmod(raw, 2)
    return f"{whole:02d}{'5' if half else ''}"


def decode_volume(value: str) -> int:
    if len(value) == 2:
        return int(value) * 2
    return int(value[:2]) * 2 + (value[-1] == "5")


# Excerpt from the MIT-licensed denonavr test recording:
# https://github.com/ol-iver/denonavr/blob/12b5cf70efe843a81276cfab56311de88c7fe489/tests/xml/AVR-X2100W-2-formMainZone_MainZoneXmlStatus.xml
RECORDED_X2100W_STATUS_XML = """\
<item>
<Power><value>ON</value></Power>
<VolumeDisplay><value>Absolute</value></VolumeDisplay>
<SurrMode><value>PLII Cinema                    </value></SurrMode>
<MasterVolume><value>-28.0</value></MasterVolume>
<Mute><value>off</value></Mute>
</item>
"""

# Excerpt from the MIT-licensed denonavr fallback-page recording:
# https://github.com/ol-iver/denonavr/blob/12b5cf70efe843a81276cfab56311de88c7fe489/tests/xml/AVR-1912-formMainZone_MainZoneXml.xml
RECORDED_AVR1912_FALLBACK_XML = """\
<item>
<Power><value>ON</value></Power>
<selectSurround><value>STEREO                         </value></selectSurround>
<VolumeDisplay><value>Relative</value></VolumeDisplay>
<MasterVolume><value>-60.0</value></MasterVolume>
<Mute><value>off</value></Mute>
</item>
"""


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
        if self.path.startswith("/goform/formMainZone_MainZoneXmlStatus.xml"):
            body = (
                "<item><Power><value>ON</value></Power>"
                "<VolumeDisplay><value>Relative</value></VolumeDisplay>"
                "<MasterVolume><value>-34.5</value></MasterVolume>"
                "<Mute><value>off</value></Mute>"
                "<SurrMode><value>STEREO</value></SurrMode></item>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
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


class FakeHttpDenon:
    def __init__(self):
        self.volume = 91
        self.maximum = 139
        self.muted = False
        self.power = True
        self.mode = "STEREO"
        self.commands: list[str] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlsplit(self.path)
                if parsed.path == "/goform/formiPhoneAppDirect.xml":
                    command = unquote(parsed.query)
                    owner.commands.append(command)
                    if command.startswith("MV"):
                        owner.volume = min(decode_volume(command[2:]), owner.maximum)
                    elif command == "MUON":
                        owner.muted = True
                    elif command == "MUOFF":
                        owner.muted = False
                    elif command == "PWON":
                        owner.power = True
                    elif command == "PWSTANDBY":
                        owner.power = False
                    elif command.startswith("MS"):
                        owner.mode = command[2:]
                    body = b"OK"
                elif parsed.path in {
                    "/goform/formMainZone_MainZoneXmlStatus.xml",
                    "/goform/formMainZone_MainZoneXml.xml",
                }:
                    db = owner.volume / 2 - 80
                    body = (
                        f"<item><Power><value>{'ON' if owner.power else 'STANDBY'}"
                        "</value></Power><VolumeDisplay><value>Relative</value>"
                        f"</VolumeDisplay><MasterVolume><value>{db:.1f}</value>"
                        f"</MasterVolume><Mute><value>{'on' if owner.muted else 'off'}"
                        f"</value></Mute><SurrMode><value>{owner.mode}</value>"
                        "</SurrMode></item>"
                    ).encode("utf-8")
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/xml")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class RecordedHttpDenon:
    def __init__(
        self, *, status_xml: str | None = None, fallback_xml: str | None = None
    ):
        self.paths: list[str] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                owner.paths.append(self.path)
                if self.path == "/goform/formMainZone_MainZoneXmlStatus.xml":
                    if status_xml is None:
                        self.send_error(404)
                        return
                    body = status_xml.encode("utf-8")
                elif self.path == "/goform/formMainZone_MainZoneXml.xml":
                    if fallback_xml is None:
                        self.send_error(404)
                        return
                    body = fallback_xml.encode("utf-8")
                elif self.path.startswith("/goform/formiPhoneAppDirect.xml?"):
                    body = b"OK"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/xml")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


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

    async def test_http_status_and_setters_use_readback(self):
        http = FakeHttpDenon()
        try:
            driver = DenonDriver(
                {
                    "host": "127.0.0.1",
                    "transport": "http",
                    "http_port": http.port,
                }
            )
            status = await driver.status()
            self.assertEqual(
                (status.volume, status.muted, status.power, status.mode),
                (91, False, True, "STEREO"),
            )
            self.assertEqual(driver.scale.display(status.volume), -34.5)

            self.assertEqual(await driver.set_volume(91), 91)
            self.assertEqual(await driver.set_volume(196), 139)
            self.assertEqual(driver.scale.max, 139)
            self.assertTrue(await driver.set_mute(True))
            self.assertFalse(await driver.set_mute(False))
            self.assertFalse(await driver.set_power(False))
            self.assertTrue(await driver.set_power(True))
            self.assertEqual(await driver.set_mode("DTS SURROUND"), "DTS SURROUND")
            self.assertIn("MV455", http.commands)
            self.assertIn("MSDTS SURROUND", http.commands)
            self.assertEqual(driver.config["transport"], "http")
            self.assertEqual(driver.config["http_port"], http.port)
        finally:
            http.close()

    async def test_http_absolute_volume_uses_db_scale_and_decodes_minimum(self):
        recorded_xml = RecordedHttpDenon(status_xml=RECORDED_X2100W_STATUS_XML)
        minimum_xml = RECORDED_X2100W_STATUS_XML.replace(
            "<value>-28.0</value>", "<value>--</value>"
        )
        recorded_minimum = RecordedHttpDenon(status_xml=minimum_xml)
        try:
            driver = DenonDriver(
                {
                    "host": "127.0.0.1",
                    "transport": "http",
                    "http_port": recorded_xml.port,
                }
            )
            status = await driver.status()
            self.assertEqual(status.volume, 104)
            self.assertEqual(driver.scale.display(status.volume), -28.0)

            minimum_driver = DenonDriver(
                {
                    "host": "127.0.0.1",
                    "transport": "http",
                    "http_port": recorded_minimum.port,
                }
            )
            minimum_status = await minimum_driver.status()
            self.assertEqual(minimum_status.volume, 0)
            self.assertEqual(driver.config["transport"], "http")
            self.assertIn(
                "/goform/formMainZone_MainZoneXmlStatus.xml", recorded_xml.paths
            )
        finally:
            recorded_xml.close()
            recorded_minimum.close()

    async def test_fallback_xml_select_surround_mode_round_trips(self):
        http = RecordedHttpDenon(fallback_xml=RECORDED_AVR1912_FALLBACK_XML)
        try:
            driver = DenonDriver(
                {
                    "host": "127.0.0.1",
                    "transport": "http",
                    "http_port": http.port,
                }
            )
            self.assertEqual(await driver.set_mode("STEREO"), "STEREO")
            self.assertEqual((await driver.status()).mode, "STEREO")
            self.assertIn("/goform/formMainZone_MainZoneXml.xml", http.paths)
        finally:
            http.close()

    async def test_telnet_refused_falls_back_to_http(self):
        http = FakeHttpDenon()
        reserve = await asyncio.start_server(
            lambda _r, w: w.close(), "127.0.0.1", 0
        )
        refused_port = reserve.sockets[0].getsockname()[1]
        reserve.close()
        await reserve.wait_closed()
        try:
            driver = DenonDriver(
                {
                    "host": "127.0.0.1",
                    "port": refused_port,
                    "http_port": http.port,
                }
            )
            status = await driver.status()
            self.assertEqual(status.volume, 91)
            self.assertEqual(driver.config["transport"], "http")
            self.assertEqual(driver.config["http_port"], http.port)
        finally:
            http.close()

    async def test_probe_discovers_http_only_receiver(self):
        http = FakeHttpDenon()
        reserve = await asyncio.start_server(
            lambda _r, w: w.close(), "127.0.0.1", 0
        )
        refused_port = reserve.sockets[0].getsockname()[1]
        reserve.close()
        await reserve.wait_closed()
        try:
            found = await DenonDriver.probe(
                {
                    "host": "127.0.0.1",
                    "port": refused_port,
                    "http_port": http.port,
                }
            )
            self.assertIsNotNone(found)
            self.assertEqual(found.config["transport"], "http")
            self.assertEqual(found.config["http_port"], http.port)
            self.assertEqual(found.config["port"], refused_port)
        finally:
            http.close()

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
        self.assertEqual(self.fake.mode, "STEREO")
        self.assertEqual(
            DenonDriver.mode_label("DOLBY D+ +DSUR"), "Dolby D+ +Dsur"
        )
        self.assertEqual(DenonDriver.mode_label("stereo"), "Stereo")
        modes = await self.driver.list_modes()
        self.assertIn(("PURE DIRECT", "Pure Direct"), [(m.code, m.name) for m in modes])

    async def test_mode_state_keeps_unknown_current_out_of_static_modes(self):
        self.fake.mode = "CUSTOM MODE"
        status = await self.driver.status()
        state = await self.driver.mode_state(status)
        self.assertEqual(state.current, "CUSTOM MODE")
        self.assertNotIn("CUSTOM MODE", [mode.code for mode in state.selectable])
        self.assertFalse(state.complete)

        self.fake.power = False
        state = await self.driver.mode_state(await self.driver.status())
        self.assertEqual(state, ModeState(None, (), complete=False))

        async def unexpected_status():
            raise AssertionError("mode_state must reuse the supplied status")

        self.driver.status = unexpected_status
        state = await self.driver.mode_state(status)
        self.assertEqual(state.current, "CUSTOM MODE")

    async def test_probe_accepts_denon_and_rejects_refused_or_wrong_protocol(self):
        found = await DenonDriver.probe(self.config)
        self.assertIsNotNone(found)
        self.assertEqual(found.driver, "denon")
        self.assertEqual(found.id, "denon-127.0.0.1")
        self.assertEqual(found.config["port"], self.port)
        self.assertEqual(found.config["transport"], "telnet")

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
        self.assertEqual(found[0].config["host"], "127.0.0.1")
        self.assertEqual(found[0].config["transport"], "http")
        self.assertEqual(found[0].config["http_port"], http_server.server_port)
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
                self.assertEqual(DenonDriver.mode_label(status.mode), name)

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
