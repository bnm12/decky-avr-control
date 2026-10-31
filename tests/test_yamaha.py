from __future__ import annotations

import asyncio
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from tests import PY_MODULES  # noqa: F401
from tests.fakes import FakeSsdpResponder
from avr_control import ssdp
from avr_control.drivers.base import (
    DriverError,
    MODE,
    MUTE,
    POWER,
    VOLUME,
    Refused,
    Unsupported,
)
from avr_control.drivers.yamaha import YamahaDriver


class FakeYamahaYXC:
    def __init__(self, *, actual_volume: bool = True, raw_max: int = 194):
        self.actual_volume = actual_volume
        self.raw_max = raw_max
        self.volume = 101 if raw_max == 194 else 70
        self.mute = False
        self.power = "on"
        self.sound_program = "straight"
        self.functions = [
            "power",
            "sleep",
            "volume",
            "mute",
            "sound_program",
            "actual_volume",
        ]
        self.zone_order = ["main", "zone2"]
        self.requests: list[tuple[str, dict[str, list[str]]]] = []
        self.server = None

    def _features(self) -> dict:
        ranges = [{"id": "volume", "min": 0, "max": self.raw_max, "step": 1}]
        if self.actual_volume:
            ranges.append(
                {"id": "actual_volume_db", "min": -80.5, "max": 16.5, "step": 0.5}
            )
            ranges.append(
                {
                    "id": "actual_volume_numeric",
                    "min": 0.0,
                    "max": 97.0,
                    "step": 0.5,
                }
            )
        zones = {
            "main": {
                "id": "main",
                "func_list": [
                    function
                    for function in self.functions
                    if function != "actual_volume" or self.actual_volume
                ],
                "sound_program_list": [
                    "munich",
                    "vienna",
                    "straight",
                    "7ch_stereo",
                    "surr_decoder",
                    "sci-fi",
                    "hall_in_munich",
                ],
                "range_step": ranges,
            },
            "zone2": {
                "id": "zone2",
                "zone_b": False,
                "func_list": ["power", "sleep", "volume", "mute"],
                "range_step": [
                    {"id": "volume", "min": 0, "max": 194, "step": 1}
                ],
            },
        }
        return {
            "response_code": 0,
            "system": {
                "func_list": ["wired_lan", "network_standby"],
                "zone_num": 2,
            },
            "zone": [zones[zone_id] for zone_id in self.zone_order],
        }

    def response(self, path: str, query: dict[str, list[str]]) -> dict:
        self.requests.append((path, query))
        if path == "/YamahaExtendedControl/v1/system/getDeviceInfo":
            return {
                "response_code": 0,
                "model_name": "RX-V6A",
                "device_id": "Yamaha-1234",
            }
        if path == "/YamahaExtendedControl/v1/system/getFeatures":
            return self._features()
        if path == "/YamahaExtendedControl/v1/main/getStatus":
            result = {
                "response_code": 0,
                "power": self.power,
                "volume": self.volume,
                "mute": self.mute,
                "sound_program": self.sound_program,
            }
            if self.actual_volume:
                result["actual_volume"] = {
                    "mode": "db",
                    "value": (self.volume - 161) / 2,
                    "unit": "dB",
                }
            return result
        if path == "/YamahaExtendedControl/v1/main/setVolume":
            self.volume = int(query["volume"][0])
        elif path == "/YamahaExtendedControl/v1/main/setMute":
            self.mute = query["enable"][0] == "true"
        elif path == "/YamahaExtendedControl/v1/main/setPower":
            self.power = query["power"][0]
        elif path == "/YamahaExtendedControl/v1/main/setSoundProgram":
            program = query["program"][0]
            if program == "guarded":
                return {"response_code": 5}
            if program == "invalid":
                return {"response_code": 4}
            self.sound_program = program
        else:
            return {"response_code": 3}
        return {"response_code": 0}

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> int:
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlparse(self.path)
                response = receiver.response(parsed.path, parse_qs(parsed.query))
                body = json.dumps(response).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format, *args):
                pass

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.server.daemon_threads = True
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.thread = thread
        return self.server.server_port

    async def close(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)


class YamahaDescriptionHandler(BaseHTTPRequestHandler):
    manufacturer = "Yamaha Corporation"
    include_yxc = True

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
            f"<manufacturer>{self.manufacturer}</manufacturer>"
            "<modelName>RX-V6A</modelName><UDN>uuid:yamaha-1234</UDN>"
            "<yamaha:X_device><yamaha:X_controlURL>"
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


class YamahaDriverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fake = FakeYamahaYXC()
        self.port = await self.fake.start()
        self.driver = YamahaDriver({"host": "127.0.0.1", "port": self.port})

    async def asyncTearDown(self):
        await self.fake.close()

    async def test_capabilities_status_scale_modes_and_setters(self):
        self.assertEqual(self.driver.capabilities, {VOLUME, MUTE, POWER, MODE})
        status = await self.driver.status()
        self.assertEqual(
            (status.volume, status.muted, status.power, status.mode),
            (101, False, True, "straight"),
        )
        self.assertEqual(self.driver.scale.unit, "dB")
        self.assertEqual((self.driver.scale.min, self.driver.scale.max), (0, 194))
        self.assertEqual(self.driver.scale.zero, 161)
        self.assertEqual(self.driver.scale.steps_per_unit, 2)
        self.assertEqual(self.driver.scale.display(status.volume), -30)
        self.assertEqual(self.driver.scale.default_cap, 101)

        modes = await self.driver.list_modes()
        self.assertEqual(
            [(mode.code, mode.name) for mode in modes],
            [
                ("munich", "Munich"),
                ("vienna", "Vienna"),
                ("straight", "Straight"),
                ("7ch_stereo", "7ch Stereo"),
                ("surr_decoder", "Surround Decoder"),
                ("sci-fi", "Sci-Fi"),
                ("hall_in_munich", "Hall in Munich"),
            ],
        )
        self.assertEqual(self.driver.mode_name("unknown_mode"), "Unknown Mode")

        self.assertEqual(await self.driver.set_volume(120), 120)
        self.assertTrue(await self.driver.set_mute(True))
        self.assertFalse(await self.driver.set_power(False))
        self.assertEqual(await self.driver.set_mode("7ch_stereo"), "7ch_stereo")
        self.assertEqual(
            (self.fake.volume, self.fake.mute, self.fake.power, self.fake.sound_program),
            (120, True, "standby", "7ch_stereo"),
        )

    async def test_spec_shaped_features_select_main_zone_by_id(self):
        self.fake.zone_order = ["zone2", "main"]
        driver = YamahaDriver({"host": "127.0.0.1", "port": self.port})

        status = await driver.status()
        self.assertEqual(status.mode, "straight")
        self.assertEqual(driver.capabilities, {VOLUME, MUTE, POWER, MODE})
        self.assertEqual((driver.scale.min, driver.scale.max), (0, 194))
        self.assertEqual(driver.scale.zero, 161)
        self.assertEqual(
            [mode.code for mode in await driver.list_modes()],
            [
                "munich",
                "vienna",
                "straight",
                "7ch_stereo",
                "surr_decoder",
                "sci-fi",
                "hall_in_munich",
            ],
        )
        self.assertEqual(await driver.set_volume(120), 120)

    async def test_percent_scale_without_actual_volume_and_response_errors(self):
        self.fake.actual_volume = False
        self.fake.raw_max = 100
        self.fake.volume = 70
        driver = YamahaDriver({"host": "127.0.0.1", "port": self.port})
        status = await driver.status()
        self.assertEqual(status.volume, 70)
        self.assertEqual(driver.scale.unit, "%")
        self.assertEqual(driver.scale.display(70), 70)
        self.assertEqual(driver.scale.default_cap, 70)

        with self.assertRaisesRegex(Refused, "response_code 5: Guarded"):
            await driver.set_mode("guarded")
        with self.assertRaisesRegex(Refused, "response_code 4: Invalid Parameter"):
            await driver.set_mode("invalid")

    async def test_common_scale_fallback_and_standby(self):
        self.fake.actual_volume = False
        self.fake.power = "standby"
        status = await self.driver.status()
        self.assertFalse(status.power)
        self.assertEqual(self.driver.scale.unit, "dB")
        self.assertEqual(self.driver.scale.zero, 161)
        self.assertEqual(self.driver.scale.steps_per_unit, 2)
        self.assertEqual(self.driver.scale.default_cap, 101)

    async def test_capabilities_follow_main_zone_features(self):
        self.fake.functions = ["volume", "sound_program"]
        driver = YamahaDriver({"host": "127.0.0.1", "port": self.port})
        await driver.status()
        self.assertEqual(driver.capabilities, {VOLUME, MODE})
        with self.assertRaisesRegex(Unsupported, "doesn't support mute"):
            await driver.set_mute(True)
        with self.assertRaisesRegex(Unsupported, "doesn't support power control"):
            await driver.set_power(False)

    async def test_probe_success_and_failure(self):
        found = await YamahaDriver.probe({"host": "127.0.0.1", "port": self.port})
        self.assertEqual(found.driver, "yamaha")
        self.assertEqual(found.model, "RX-V6A")
        self.assertEqual(found.id, "Yamaha-1234")
        self.assertEqual(found.config["port"], self.port)
        self.assertIsNone(
            await YamahaDriver.probe(
                {"host": "127.0.0.1", "port": self.port + 1}
            )
        )

    async def test_nonzero_response_code_is_a_driver_error(self):
        with self.assertRaisesRegex(DriverError, "response_code 3: Invalid Request"):
            await self.driver._api("main/not-a-method")

    async def test_discovery_uses_ssdp_and_yamaha_yxc_description(self):
        http_server = ThreadingHTTPServer(
            ("127.0.0.1", 0), YamahaDescriptionHandler
        )
        thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        thread.start()
        location = f"http://127.0.0.1:{http_server.server_port}/device.xml"
        loop = asyncio.get_running_loop()
        transport, responder = await loop.create_datagram_endpoint(
            lambda: FakeSsdpResponder(location), local_addr=("127.0.0.1", 0)
        )
        old_address = ssdp.SSDP_ADDRESS
        try:
            ssdp.SSDP_ADDRESS = transport.get_extra_info("sockname")
            found = await YamahaDriver.discover(0.1)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].driver, "yamaha")
            self.assertEqual(found[0].model, "RX-V6A")
            self.assertEqual(found[0].id, "uuid:yamaha-1234")
            self.assertEqual(found[0].host, "127.0.0.1")
            self.assertEqual(found[0].config, {"host": "127.0.0.1"})
            self.assertEqual(len(responder.requests), 2)
            self.assertTrue(
                all("M-SEARCH * HTTP/1.1" in request for request in responder.requests)
            )
            self.assertTrue(
                any(
                    "ST: urn:schemas-upnp-org:device:MediaRenderer:1" in request
                    for request in responder.requests
                )
            )
            self.assertTrue(
                any("ST: upnp:rootdevice" in request for request in responder.requests)
            )
        finally:
            ssdp.SSDP_ADDRESS = old_address
            transport.close()
            http_server.shutdown()
            http_server.server_close()
            thread.join(timeout=2)

    async def test_discovery_rejects_non_yamaha_and_legacy_xml_device(self):
        http_server = ThreadingHTTPServer(
            ("127.0.0.1", 0), YamahaDescriptionHandler
        )
        thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        thread.start()
        location = f"http://127.0.0.1:{http_server.server_port}/device.xml"
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: FakeSsdpResponder(location), local_addr=("127.0.0.1", 0)
        )
        old_address = ssdp.SSDP_ADDRESS
        old_manufacturer = YamahaDescriptionHandler.manufacturer
        old_include_yxc = YamahaDescriptionHandler.include_yxc
        try:
            ssdp.SSDP_ADDRESS = transport.get_extra_info("sockname")
            YamahaDescriptionHandler.manufacturer = "Example Audio"
            self.assertEqual(await YamahaDriver.discover(0.1), [])
            YamahaDescriptionHandler.manufacturer = "Yamaha Corporation"
            YamahaDescriptionHandler.include_yxc = False
            self.assertEqual(await YamahaDriver.discover(0.1), [])
        finally:
            ssdp.SSDP_ADDRESS = old_address
            YamahaDescriptionHandler.manufacturer = old_manufacturer
            YamahaDescriptionHandler.include_yxc = old_include_yxc
            transport.close()
            http_server.shutdown()
            http_server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
