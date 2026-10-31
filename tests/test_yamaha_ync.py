from __future__ import annotations

import asyncio
import json
import threading
import unittest
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests import PY_MODULES  # noqa: F401
from tests.fakes import FakeSsdpResponder
from avr_control import ssdp
from avr_control.drivers.base import DriverError, MODE, MUTE, POWER, VOLUME, Refused
from avr_control.drivers.yamaha import YamahaDriver
from avr_control.drivers.yamaha_ync import YamahaYNCDriver


class FakeYamahaYNC:
    def __init__(
        self,
        *,
        power: str = "On",
        volume_val: int = -455,
        mute: str = "Off",
        straight: str = "Off",
        sound_program: str = "Hall in Munich",
        config_modes: bool = True,
        max_db: str | None = "9.5",
        yxc: bool = False,
        bad_volume: bool = False,
    ):
        self.power = power
        self.volume_val = volume_val
        self.mute = mute
        self.straight = straight
        self.sound_program = sound_program
        self.config_modes = config_modes
        self.max_db = max_db
        self.yxc = yxc
        self.bad_volume = bad_volume
        self.rc: str | None = None
        self.requests: list[tuple[str, str, bytes]] = []
        self.server = None

    def response(self, method: str, path: str, payload: bytes) -> tuple[int, bytes]:
        self.requests.append((method, path, payload))
        if path == "/YamahaExtendedControl/v1/system/getDeviceInfo":
            if self.yxc:
                body = json.dumps({"response_code": 0}).encode("utf-8")
                return 200, body
            return 404, b"not found"
        if path != "/YamahaRemoteControl/ctrl" or method != "POST":
            return 404, b"not found"
        request = ET.fromstring(payload)
        command = request.attrib.get("cmd")
        requested = next(iter(request), None)
        if requested is None:
            return 400, b""
        if self.rc is not None:
            return 200, f'<YAMAHA_AV RC="{self.rc}"/>'.encode("utf-8")
        if command == "GET" and requested.tag == "System":
            config = ET.Element("YAMAHA_AV", {"RC": "0"})
            system = ET.SubElement(config, "System")
            details = ET.SubElement(system, "Config")
            ET.SubElement(details, "Model_Name").text = "RX-V671"
            ET.SubElement(details, "System_ID").text = "Yamaha-system-123"
            if self.max_db is not None:
                volume = ET.SubElement(details, "Volume")
                ET.SubElement(volume, "Max").text = self.max_db
                ET.SubElement(volume, "Unit").text = "dB"
            if self.config_modes:
                modes = ET.SubElement(details, "Sound_Program_List")
                ET.SubElement(modes, "Item").text = "Hall in Munich"
                ET.SubElement(modes, "Item").text = "Jazz Club"
            return 200, ET.tostring(config, encoding="utf-8")
        if command == "GET":
            return 200, self._status_xml()
        if command == "PUT":
            if requested.tag == "Main_Zone":
                self._apply_put(requested)
            return 200, b'<YAMAHA_AV RC="0"/>'
        return 400, b""

    def _status_xml(self) -> bytes:
        root = ET.Element("YAMAHA_AV", {"RC": "0"})
        main = ET.SubElement(root, "Main_Zone")
        basic = ET.SubElement(main, "Basic_Status")
        power_control = ET.SubElement(basic, "Power_Control")
        ET.SubElement(power_control, "Power").text = self.power
        volume = ET.SubElement(basic, "Volume")
        level = ET.SubElement(volume, "Lvl")
        if not self.bad_volume:
            ET.SubElement(level, "Val").text = str(self.volume_val)
        ET.SubElement(level, "Exp").text = "1"
        ET.SubElement(level, "Unit").text = "dB"
        ET.SubElement(volume, "Mute").text = self.mute
        surround = ET.SubElement(basic, "Surround")
        program = ET.SubElement(surround, "Program_Sel")
        current = ET.SubElement(program, "Current")
        ET.SubElement(current, "Straight").text = self.straight
        ET.SubElement(current, "Sound_Program").text = self.sound_program
        ET.SubElement(current, "Enhancer").text = "Off"
        return ET.tostring(root, encoding="utf-8")

    def _apply_put(self, main: ET.Element) -> None:
        power = main.find("./Power_Control/Power")
        if power is not None:
            self.power = power.text or self.power
        mute = main.find("./Volume/Mute")
        if mute is not None:
            self.mute = mute.text or self.mute
        level = main.find("./Volume/Lvl")
        if level is not None:
            self.volume_val = int(level.findtext("Val", "0"))
        sound_program = main.find("./Surround/Program_Sel/Current/Sound_Program")
        if sound_program is not None:
            self.sound_program = sound_program.text or self.sound_program
            self.straight = "Off"
        straight = main.find("./Surround/Program_Sel/Current/Straight")
        if straight is not None:
            self.straight = straight.text or self.straight

    async def start(self) -> int:
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                status, body = receiver.response("GET", self.path, b"")
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                if self.path.endswith("getDeviceInfo"):
                    self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                payload = self.rfile.read(length)
                status, body = receiver.response("POST", self.path, payload)
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Type", "text/xml")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self.server.server_port

    async def close(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)


class YamahaYNCDescriptionHandler(BaseHTTPRequestHandler):
    include_yxc = False

    def do_GET(self):
        yxc = (
            "<yamaha:X_yxcControlURL>"
            "http://127.0.0.1/YamahaExtendedControl/v1/"
            "</yamaha:X_yxcControlURL>"
            if self.include_yxc
            else ""
        )
        body = (
            '<?xml version="1.0"?>'
            '<root xmlns="urn:schemas-upnp-org:device-1-0" '
            'xmlns:yamaha="urn:schemas-yamaha-com:device-1-0">'
            "<device><friendlyName>Living Room</friendlyName>"
            "<manufacturer>Yamaha Corporation</manufacturer>"
            "<modelName>RX-V671</modelName><UDN>uuid:yamaha-ync-123</UDN>"
            "<yamaha:X_device><yamaha:X_URLBase>"
            "http://127.0.0.1/"
            "</yamaha:X_URLBase><yamaha:X_controlURL>"
            "/YamahaRemoteControl/ctrl</yamaha:X_controlURL>"
            f"{yxc}</yamaha:X_device></device></root>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/xml")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *args):
        pass


class YamahaYNCDriverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fake = FakeYamahaYNC()
        self.port = await self.fake.start()
        self.driver = YamahaYNCDriver({"host": "127.0.0.1", "port": self.port})

    async def asyncTearDown(self):
        await self.fake.close()

    async def test_status_scale_programs_and_setters_read_back(self):
        self.assertEqual(self.driver.capabilities, {VOLUME, MUTE, POWER, MODE})
        status = await self.driver.status()
        self.assertEqual(
            (status.volume, status.muted, status.power, status.mode),
            (70, False, True, "Hall in Munich"),
        )
        self.assertEqual(self.driver.scale.unit, "dB")
        self.assertEqual(self.driver.scale.zero, 161)
        self.assertEqual(self.driver.scale.steps_per_unit, 2)
        self.assertEqual(self.driver.scale.max, 180)
        self.assertEqual(self.driver.scale.default_cap, 101)
        self.assertEqual(self.driver.scale.display(status.volume), -45.5)
        self.assertEqual(
            [(mode.code, mode.name) for mode in await self.driver.list_modes()],
            [
                ("Straight", "Straight"),
                ("Hall in Munich", "Hall in Munich"),
                ("Jazz Club", "Jazz Club"),
            ],
        )

        self.assertEqual(await self.driver.set_volume(120), 120)
        self.assertTrue(await self.driver.set_mute(True))
        self.assertFalse(await self.driver.set_power(False))
        self.assertEqual(await self.driver.set_mode("Jazz Club"), "Jazz Club")
        self.assertEqual(
            (
                self.fake.volume_val,
                self.fake.mute,
                self.fake.power,
                self.fake.sound_program,
                self.fake.straight,
            ),
            (-205, "On", "Standby", "Jazz Club", "Off"),
        )
        self.assertTrue(
            any(
                b"<Val>-205</Val>" in payload and b"<Exp>1</Exp>" in payload
                for method, _path, payload in self.fake.requests
                if method == "POST"
            )
        )

    async def test_standby_and_straight_mode(self):
        self.fake.power = "Standby"
        self.fake.straight = "On"
        status = await self.driver.status()
        self.assertFalse(status.power)
        self.assertEqual(status.mode, "Straight")
        self.assertEqual(await self.driver.set_mode("Straight"), "Straight")
        self.assertEqual(self.fake.straight, "On")

    async def test_fallback_modes_and_default_scale(self):
        self.fake.config_modes = False
        self.fake.max_db = None
        driver = YamahaYNCDriver({"host": "127.0.0.1", "port": self.port})
        await driver.status()
        self.assertEqual(driver.scale.max, 194)
        self.assertEqual(driver.scale.default_cap, 101)
        modes = await driver.list_modes()
        self.assertIn(("Straight", "Straight"), [
            (mode.code, mode.name) for mode in modes
        ])
        self.assertIn(("Hall in Munich", "Hall in Munich"), [
            (mode.code, mode.name) for mode in modes
        ])

    async def test_nonzero_rc_is_refused_and_malformed_volume_is_driver_error(self):
        self.fake.rc = "3"
        with self.assertRaisesRegex(Refused, "RC=3"):
            await self.driver.status()

        self.fake.rc = None
        self.fake.bad_volume = True
        with self.assertRaisesRegex(DriverError, "invalid volume level"):
            await self.driver.status()

    async def test_probe_success_failure_and_yxc_exclusion(self):
        found = await YamahaYNCDriver.probe({"host": "127.0.0.1", "port": self.port})
        self.assertEqual(found.driver, "yamaha_ync")
        self.assertEqual(found.model, "RX-V671")
        self.assertEqual(found.id, "Yamaha-system-123")
        self.assertEqual(found.config["port"], self.port)
        self.assertIsNone(
            await YamahaYNCDriver.probe(
                {"host": "127.0.0.1", "port": self.port + 1}
            )
        )

        self.fake.yxc = True
        self.assertIsNone(
            await YamahaYNCDriver.probe({"host": "127.0.0.1", "port": self.port})
        )

    async def test_probe_does_not_claim_missing_model(self):
        self.fake.rc = None
        self.fake.config_modes = False
        self.fake.response = self._config_without_model
        self.assertIsNone(
            await YamahaYNCDriver.probe({"host": "127.0.0.1", "port": self.port})
        )

    def _config_without_model(self, method: str, path: str, payload: bytes):
        if path == "/YamahaExtendedControl/v1/system/getDeviceInfo":
            return 404, b"not found"
        if method == "POST":
            request = ET.fromstring(payload)
            if request.attrib.get("cmd") == "GET":
                return 200, b'<YAMAHA_AV RC="0"><System><Config/></System></YAMAHA_AV>'
        return 404, b"not found"

    async def test_discovery_only_claims_legacy_yamaha_xml_devices(self):
        http_server = ThreadingHTTPServer(
            ("127.0.0.1", 0), YamahaYNCDescriptionHandler
        )
        thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        thread.start()
        location = f"http://127.0.0.1:{http_server.server_port}/device.xml"
        loop = asyncio.get_running_loop()
        transport, responder = await loop.create_datagram_endpoint(
            lambda: FakeSsdpResponder(location), local_addr=("127.0.0.1", 0)
        )
        old_address = ssdp.SSDP_ADDRESS
        old_yxc = YamahaYNCDescriptionHandler.include_yxc
        try:
            ssdp.SSDP_ADDRESS = transport.get_extra_info("sockname")
            YamahaYNCDescriptionHandler.include_yxc = False
            found = await YamahaYNCDriver.discover(0.1)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].driver, "yamaha_ync")
            self.assertEqual(found[0].model, "RX-V671")
            self.assertEqual(found[0].id, "uuid:yamaha-ync-123")
            self.assertEqual(found[0].host, "127.0.0.1")
            self.assertEqual(found[0].config, {"host": "127.0.0.1"})
            self.assertEqual(await YamahaDriver.discover(0.1), [])

            YamahaYNCDescriptionHandler.include_yxc = True
            self.assertEqual(await YamahaYNCDriver.discover(0.1), [])
            self.assertEqual(len(await YamahaDriver.discover(0.1)), 1)
            self.assertEqual(len(responder.requests), 8)
        finally:
            ssdp.SSDP_ADDRESS = old_address
            YamahaYNCDescriptionHandler.include_yxc = old_yxc
            transport.close()
            http_server.shutdown()
            http_server.server_close()
            thread.join(timeout=2)
if __name__ == "__main__":
    unittest.main()
