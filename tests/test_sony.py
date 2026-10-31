from __future__ import annotations

import asyncio
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from tests import PY_MODULES  # noqa: F401
from tests.fakes import FakeSsdpResponder
from avr_control import ssdp
from avr_control.drivers.base import (
    MODE,
    MUTE,
    POWER,
    VOLUME,
    DriverError,
    Refused,
    Unsupported,
)
from avr_control.drivers.sony import SonyDriver


class FakeSonyScalarWebAPI:
    def __init__(self):
        self.power = "active"
        self.volume = 30
        self.minimum = 0
        self.maximum = 74
        self.mute = "off"
        self.sound_field = "stereo"
        self.category = "audio"
        self.errors: dict[str, tuple[int, str]] = {}
        self.requests: list[tuple[str, dict]] = []
        self.server = None

    def response(self, path: str, payload: dict) -> dict:
        method = payload.get("method", "")
        params = payload.get("params", [])
        args = params[0] if params and isinstance(params[0], dict) else {}
        self.requests.append((path, payload))
        if method in self.errors:
            return {"id": payload.get("id"), "error": list(self.errors[method])}
        if path == "/sony/system":
            if method == "getPowerStatus":
                result = [{"status": self.power}]
            elif method == "setPowerStatus":
                self.power = args["status"]
                result = [{"status": self.power}]
            elif method == "getSystemInformation":
                result = [
                    {
                        "model": "STR-DN1080",
                        "serialNumber": "SNY123",
                        "macAddr": "00:11:22:33:44:55",
                    }
                ]
            elif method == "getInterfaceInformation":
                result = [
                    {
                        "modelName": "STR-DN1080",
                        "productCategory": self.category,
                    }
                ]
            else:
                return {"id": payload.get("id"), "error": [3, "not supported"]}
        elif path == "/sony/audio":
            if method == "getVolumeInformation":
                result = [
                    {
                        "volume": str(self.volume),
                        "minVolume": str(self.minimum),
                        "maxVolume": str(self.maximum),
                        "step": "1",
                        "mute": self.mute,
                        "output": "",
                    }
                ]
            elif method == "setAudioVolume":
                assert isinstance(args.get("volume"), str)
                self.volume = int(args["volume"])
                result = []
            elif method == "setAudioMute":
                self.mute = args["mute"]
                result = []
            elif method == "getSoundSettings":
                result = [
                    {
                        "target": "soundField",
                        "currentValue": self.sound_field,
                        "candidate": [
                            {
                                "value": "stereo",
                                "title": "Stereo",
                                "isAvailable": True,
                            },
                            {
                                "value": "movie",
                                "title": "Movie",
                                "isAvailable": True,
                            },
                            {
                                "value": "unavailable",
                                "title": "Unavailable",
                                "isAvailable": False,
                            },
                        ],
                    }
                ]
            elif method == "setSoundSettings":
                self.sound_field = args["settings"][0]["value"]
                result = []
            else:
                return {"id": payload.get("id"), "error": [3, "not supported"]}
        else:
            return {"id": payload.get("id"), "error": [3, "unknown service"]}
        return {"id": payload.get("id"), "result": result}

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> int:
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                result = receiver.response(urlparse(self.path).path, payload)
                body = json.dumps(result).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format, *args):
                pass

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self.server.server_port

    async def close(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)


class SonyDescriptionHandler(BaseHTTPRequestHandler):
    base_url = ""
    include_video = False

    def do_GET(self):
        services = "<av:X_ScalarWebAPI_ServiceType>audio</av:X_ScalarWebAPI_ServiceType>"
        if self.include_video:
            services += (
                "<av:X_ScalarWebAPI_ServiceType>videoScreen</av:X_ScalarWebAPI_ServiceType>"
            )
        body = (
            '<?xml version="1.0"?>'
            '<root xmlns="urn:schemas-upnp-org:device-1-0" '
            'xmlns:av="urn:schemas-sony-com:av">'
            "<device><friendlyName>Living Room</friendlyName>"
            "<manufacturer>Sony Corporation</manufacturer>"
            "<modelName>STR-DN1080</modelName><UDN>uuid:sony-1234</UDN>"
            "<av:X_ScalarWebAPI_DeviceInfo>"
            f"<av:X_ScalarWebAPI_BaseURL>{self.base_url}</av:X_ScalarWebAPI_BaseURL>"
            "<av:X_ScalarWebAPI_ServiceList>"
            f"{services}</av:X_ScalarWebAPI_ServiceList>"
            "</av:X_ScalarWebAPI_DeviceInfo></device></root>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/xml")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *args):
        pass


class SonyDriverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fake = FakeSonyScalarWebAPI()
        self.port = await self.fake.start()
        self.driver = SonyDriver({"host": "127.0.0.1", "port": self.port})

    async def asyncTearDown(self):
        await self.fake.close()

    async def test_status_uses_runtime_volume_range_and_sound_field_candidates(self):
        self.assertEqual(self.driver.capabilities, {VOLUME, MUTE, POWER, MODE})
        status = await self.driver.status()
        self.assertEqual(
            (status.volume, status.muted, status.power, status.mode),
            (30, False, True, "stereo"),
        )
        self.assertEqual((self.driver.scale.min, self.driver.scale.max), (0, 74))
        self.assertEqual(self.driver.scale.unit, "steps")
        self.assertEqual(self.driver.scale.display(30), 30)
        self.assertEqual(self.driver.scale.default_cap, 30)
        self.assertEqual(
            [(mode.code, mode.name) for mode in await self.driver.list_modes()],
            [("stereo", "Stereo"), ("movie", "Movie")],
        )
        volume_request = next(
            payload
            for path, payload in self.fake.requests
            if path == "/sony/audio" and payload["method"] == "getVolumeInformation"
        )
        self.assertEqual(volume_request["params"], [{"output": ""}])
        self.assertEqual(volume_request["version"], "1.1")

    async def test_percent_scale_requires_exact_zero_to_100_range(self):
        self.fake.minimum = 0
        self.fake.maximum = 100
        await self.driver.status()
        self.assertEqual(self.driver.scale.unit, "%")

        self.fake.minimum = 10
        await self.driver.status()
        self.assertEqual(self.driver.scale.unit, "steps")

    async def test_setters_read_back_volume_mute_power_and_mode(self):
        self.assertEqual(await self.driver.set_volume(44), 44)
        self.assertTrue(await self.driver.set_mute(True))
        self.assertEqual(await self.driver.set_mode("movie"), "movie")
        self.assertFalse(await self.driver.set_power(False))
        self.assertEqual(
            (self.fake.volume, self.fake.mute, self.fake.power, self.fake.sound_field),
            (44, "on", "off", "movie"),
        )
        volume_set = next(
            payload
            for path, payload in self.fake.requests
            if path == "/sony/audio" and payload["method"] == "setAudioVolume"
        )
        self.assertEqual(volume_set["params"], [{"volume": "44", "output": ""}])
        self.assertEqual(
            next(
                payload
                for path, payload in self.fake.requests
                if path == "/sony/audio" and payload["method"] == "setAudioMute"
            )["params"],
            [{"mute": "on"}],
        )
        self.assertEqual(
            next(
                payload
                for path, payload in self.fake.requests
                if path == "/sony/system" and payload["method"] == "setPowerStatus"
            )["params"],
            [{"status": "off"}],
        )

    async def test_standby_skips_audio_queries_and_returns_unknown_audio_state(self):
        self.fake.power = "off"
        status = await self.driver.status()
        self.assertEqual(
            (status.power, status.volume, status.muted, status.mode),
            (False, None, None, None),
        )
        self.assertFalse(any(path == "/sony/audio" for path, _ in self.fake.requests))

    async def test_power_off_error_from_audio_is_reported_as_standby(self):
        self.fake.errors["getVolumeInformation"] = (40000, "Power is off")
        status = await self.driver.status()
        self.assertEqual(
            (status.power, status.volume, status.muted, status.mode),
            (False, None, None, None),
        )

    async def test_api_errors_map_to_unsupported_refused_and_driver_error(self):
        for code in (3, 12):
            self.fake.errors["setAudioMute"] = (code, "not supported")
            with self.assertRaises(Unsupported):
                await self.driver.set_mute(True)
        self.fake.errors["setAudioMute"] = (40000, "power off")
        with self.assertRaises(Refused):
            await self.driver.set_mute(True)
        self.fake.errors["setAudioMute"] = (99, "unexpected")
        with self.assertRaises(DriverError):
            await self.driver.set_mute(True)

    async def test_probe_accepts_audio_and_rejects_tv(self):
        found = await SonyDriver.probe({"host": "127.0.0.1", "port": self.port})
        self.assertEqual(found.driver, "sony")
        self.assertEqual(found.model, "STR-DN1080")
        self.assertEqual(found.id, "00:11:22:33:44:55")
        self.assertEqual(found.config["port"], self.port)
        self.fake.category = "videoScreen"
        self.assertIsNone(
            await SonyDriver.probe({"host": "127.0.0.1", "port": self.port})
        )

    async def test_probe_returns_none_for_another_protocol(self):
        self.assertIsNone(
            await SonyDriver.probe(
                {"host": "127.0.0.1", "port": self.port + 1}
            )
        )

    async def test_discovery_reads_scalar_base_url_and_uses_its_port(self):
        description = ThreadingHTTPServer(
            ("127.0.0.1", 0), SonyDescriptionHandler
        )
        description_thread = threading.Thread(
            target=description.serve_forever, daemon=True
        )
        description_thread.start()
        old_base_url = SonyDescriptionHandler.base_url
        old_video = SonyDescriptionHandler.include_video
        SonyDescriptionHandler.base_url = f"http://127.0.0.1:{self.port}/sony"
        SonyDescriptionHandler.include_video = False
        location = f"http://127.0.0.1:{description.server_port}/device.xml"
        loop = asyncio.get_running_loop()
        transport, responder = await loop.create_datagram_endpoint(
            lambda: FakeSsdpResponder(location), local_addr=("127.0.0.1", 0)
        )
        old_address = ssdp.SSDP_ADDRESS
        try:
            ssdp.SSDP_ADDRESS = transport.get_extra_info("sockname")
            found = await SonyDriver.discover(0.1)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].driver, "sony")
            self.assertEqual(found[0].model, "STR-DN1080")
            self.assertEqual(found[0].id, "00:11:22:33:44:55")
            self.assertEqual(found[0].host, "127.0.0.1")
            self.assertEqual(found[0].config, {"host": "127.0.0.1", "port": self.port})
            self.assertEqual(len(responder.requests), 1)
            self.assertTrue(
                any(
                    "ST: urn:schemas-sony-com:service:ScalarWebAPI:1" in request
                    for request in responder.requests
                )
            )
        finally:
            ssdp.SSDP_ADDRESS = old_address
            transport.close()
            SonyDescriptionHandler.base_url = old_base_url
            SonyDescriptionHandler.include_video = old_video
            description.shutdown()
            description.server_close()
            description_thread.join(timeout=2)

    async def test_discovery_rejects_video_devices(self):
        description = ThreadingHTTPServer(
            ("127.0.0.1", 0), SonyDescriptionHandler
        )
        description_thread = threading.Thread(
            target=description.serve_forever, daemon=True
        )
        description_thread.start()
        old_base_url = SonyDescriptionHandler.base_url
        old_video = SonyDescriptionHandler.include_video
        SonyDescriptionHandler.base_url = f"http://127.0.0.1:{self.port}/sony"
        SonyDescriptionHandler.include_video = True
        location = f"http://127.0.0.1:{description.server_port}/device.xml"
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: FakeSsdpResponder(location), local_addr=("127.0.0.1", 0)
        )
        old_address = ssdp.SSDP_ADDRESS
        try:
            ssdp.SSDP_ADDRESS = transport.get_extra_info("sockname")
            self.assertEqual(await SonyDriver.discover(0.1), [])
        finally:
            ssdp.SSDP_ADDRESS = old_address
            transport.close()
            SonyDescriptionHandler.base_url = old_base_url
            SonyDescriptionHandler.include_video = old_video
            description.shutdown()
            description.server_close()
            description_thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
