from __future__ import annotations

import asyncio
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.etree import ElementTree as ET

from tests import PY_MODULES  # noqa: F401
from tests.fakes import FakeSsdpResponder
from avr_control import ssdp
from avr_control.drivers.base import MUTE, Refused, VOLUME
from avr_control.drivers.upnp import UpnpDriver


DEVICE_NS = "urn:schemas-upnp-org:device-1-0"
SERVICE_TYPE = "urn:schemas-upnp-org:service:RenderingControl:1"
SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"


class FakeUpnpRenderer:
    def __init__(
        self,
        *,
        nested: bool = False,
        multiple_nested: bool = False,
        sonos: bool = False,
        include_service: bool = True,
        fault: tuple[str, str] | None = None,
        volume_range: tuple[int, int, int] = (0, 100, 1),
        host: str = "127.0.0.1",
        description_path: str = "/device.xml",
        control_url: str | None = None,
        include_url_base: bool = True,
        expected_control_path: str = "",
    ):
        self.nested = nested
        self.multiple_nested = multiple_nested
        self.sonos = sonos
        self.include_service = include_service
        self.fault = fault
        self.volume_range = volume_range
        self.host = host
        self.description_path = description_path
        self.control_url = control_url
        self.include_url_base = include_url_base
        self.expected_control_path = expected_control_path
        self.volume = 35
        self.nested_volumes = {"living": 35, "kitchen": 75}
        self.muted = False
        self.requests: list[tuple[str, str]] = []
        self.request_bodies: list[str] = []
        self.server = None

    def description(self, base: str) -> bytes:
        def service_for(control_url: str) -> str:
            if not self.include_service:
                return ""
            return (
                "<service><serviceType>"
                f"{SERVICE_TYPE}</serviceType><serviceId>"
                "urn:upnp-org:serviceId:RenderingControl</serviceId>"
                f"<controlURL>{control_url}</controlURL>"
                "<SCPDURL>/scpd.xml</SCPDURL></service>"
            )

        living_control = self.control_url or (
            "/MediaRenderer/RenderingControl/Control"
            if self.sonos
            else "/control/living" if self.multiple_nested else "/control"
        )
        kitchen_control = "/control/kitchen"
        secondary_renderer = (
            "<device><friendlyName>Kitchen</friendlyName>"
            "<manufacturer>Sonos</manufacturer><modelName>Play:3</modelName>"
            "<UDN>uuid:RINCON_SECOND</UDN>"
            f"<serviceList>{service_for(kitchen_control)}</serviceList></device>"
            if self.multiple_nested
            else ""
        )
        renderer = (
            "<device><friendlyName>Living Room</friendlyName>"
            "<manufacturer>Sonos</manufacturer><modelName>Play:1</modelName>"
            "<UDN>uuid:RINCON_123456789</UDN>"
            f"<serviceList>{service_for(living_control)}</serviceList></device>"
        )
        device = (
            "<device><friendlyName>Root Device</friendlyName>"
            "<manufacturer>Example Audio</manufacturer><modelName>Gateway</modelName>"
            "<UDN>uuid:root-device</UDN><deviceList>"
            f"{renderer}{secondary_renderer}</deviceList></device>"
            if self.nested
            else renderer
        )
        url_base = f"<URLBase>{base}</URLBase>" if self.include_url_base else ""
        return (
            '<?xml version="1.0"?>'
            f'<root xmlns="{DEVICE_NS}">{url_base}{device}</root>'
        ).encode()

    def scpd(self) -> bytes:
        minimum, maximum, step = self.volume_range
        return (
            '<?xml version="1.0"?>'
            '<scpd xmlns="urn:schemas-upnp-org:service-1-0"><serviceStateTable>'
            '<stateVariable sendEvents="no"><name>Volume</name><dataType>ui2</dataType>'
            f"<allowedValueRange><minimum>{minimum}</minimum>"
            f"<maximum>{maximum}</maximum><step>{step}</step></allowedValueRange>"
            "</stateVariable>"
            "</serviceStateTable></scpd>"
        ).encode()

    def soap_response(self, action: str, path: str) -> bytes:
        if self.fault:
            code, description = self.fault
            return (
                f'<s:Envelope xmlns:s="{SOAP_NS}"><s:Body><s:Fault>'
                "<faultcode>s:Client</faultcode><faultstring>UPnPError</faultstring>"
                "<detail><UPnPError xmlns=\"urn:schemas-upnp-org:control-1-0\">"
                f"<errorCode>{code}</errorCode><errorDescription>{description}</errorDescription>"
                "</UPnPError></detail></s:Fault></s:Body></s:Envelope>"
            ).encode()

        if action == "GetVolume":
            volume = (
                self.nested_volumes[path.rsplit("/", 1)[-1]]
                if self.multiple_nested
                else self.volume
            )
            result = f"<CurrentVolume>{volume}</CurrentVolume>"
        elif action == "GetMute":
            result = f"<CurrentMute>{int(self.muted)}</CurrentMute>"
        elif action == "SetVolume":
            if self.multiple_nested:
                self.nested_volumes[path.rsplit("/", 1)[-1]] = int(
                    self._argument("DesiredVolume")
                )
            else:
                self.volume = int(self._argument("DesiredVolume"))
            result = ""
        elif action == "SetMute":
            self.muted = self._argument("DesiredMute") == "1"
            result = ""
        else:
            result = ""
        return (
            f'<s:Envelope xmlns:s="{SOAP_NS}" xmlns:u="{SERVICE_TYPE}">'
            f"<s:Body><u:{action}Response>{result}</u:{action}Response>"
            "</s:Body></s:Envelope>"
        ).encode()

    def _argument(self, name: str) -> str:
        return self._soap_body.findtext(f".//{{*}}{name}", default="0")

    async def start(self) -> int:
        renderer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/scpd.xml":
                    body = renderer.scpd()
                elif self.path == renderer.description_path or (
                    renderer.sonos and self.path == "/xml/device_description.xml"
                ):
                    base = f"http://{renderer.host}:{renderer.server.server_port}/"
                    body = renderer.description(base)
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/xml; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                if (
                    renderer.expected_control_path
                    and self.path != renderer.expected_control_path
                ):
                    self.send_error(404)
                    return
                action_header = self.headers.get("SOAPACTION", "")
                action = action_header.strip('"').rsplit("#", 1)[-1]
                renderer.requests.append((action, self.path))
                content_length = int(self.headers.get("Content-Length", "0"))
                request_body = self.rfile.read(content_length)
                renderer.request_bodies.append(request_body.decode())
                renderer._soap_body = ET.fromstring(request_body)
                body = renderer.soap_response(action, self.path)
                self.send_response(500 if renderer.fault else 200)
                self.send_header("Content-Type", "text/xml; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format, *args):
                pass

        server_class = ThreadingHTTPServer
        if ":" in self.host:
            class IPv6ThreadingHTTPServer(ThreadingHTTPServer):
                address_family = socket.AF_INET6

            server_class = IPv6ThreadingHTTPServer
        self.server = server_class((self.host, 0), Handler)
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


class UpnpDriverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fake = FakeUpnpRenderer()
        self.port = await self.fake.start()
        self.location = f"http://127.0.0.1:{self.port}/device.xml"
        self.driver = UpnpDriver({"host": "127.0.0.1", "location": self.location})

    async def asyncTearDown(self):
        await self.fake.close()

    async def test_volume_and_mute_use_rendering_control_soap(self):
        self.assertEqual(self.driver.capabilities, {VOLUME, MUTE})
        status = await self.driver.status()
        self.assertEqual((status.volume, status.muted), (35, False))
        self.assertEqual(
            (self.driver.scale.min, self.driver.scale.max, self.driver.scale.unit),
            (0, 100, "%"),
        )
        self.assertEqual(self.driver.scale.default_cap, 40)

        self.assertEqual(await self.driver.set_volume(62), 62)
        self.assertTrue(await self.driver.set_mute(True))
        self.assertEqual((self.fake.volume, self.fake.muted), (62, True))
        self.assertCountEqual(
            [action for action, _ in self.fake.requests],
            ["GetVolume", "GetMute", "SetVolume", "GetVolume", "SetMute", "GetMute"],
        )
        self.assertTrue(all(path == "/control" for _, path in self.fake.requests))
        self.assertIn("<InstanceID>0</InstanceID>", self.fake.request_bodies[0])
        self.assertIn("<Channel>Master</Channel>", self.fake.request_bodies[0])

    async def test_scpd_volume_range_and_step_are_honored(self):
        await self.fake.close()
        self.fake = FakeUpnpRenderer(volume_range=(10, 90, 5))
        self.port = await self.fake.start()
        self.location = f"http://127.0.0.1:{self.port}/device.xml"
        driver = UpnpDriver({"host": "127.0.0.1", "location": self.location})

        await driver.status()
        self.assertEqual(
            (driver.scale.min, driver.scale.max, driver.scale.default_cap),
            (10, 90, 40),
        )
        self.assertEqual(await driver.set_volume(43), 40)
        self.assertEqual(self.fake.volume, 40)

    async def test_volume_step_rounds_down_without_crossing_lower_targets(self):
        await self.fake.close()
        self.fake = FakeUpnpRenderer(volume_range=(10, 90, 10))
        self.port = await self.fake.start()
        self.location = f"http://127.0.0.1:{self.port}/device.xml"
        driver = UpnpDriver({"host": "127.0.0.1", "location": self.location})

        await driver.status()
        self.assertEqual(await driver.set_volume(26), 20)
        self.assertEqual(await driver.set_volume(15), 10)
        self.assertEqual(await driver.set_volume(9), 10)

    async def test_sonos_probe_uses_description_path_and_returns_stable_identity(self):
        await self.fake.close()
        self.fake = FakeUpnpRenderer(sonos=True)
        self.port = await self.fake.start()

        found = await UpnpDriver.probe({"host": "127.0.0.1", "port": self.port})

        self.assertEqual(found.driver, "upnp")
        self.assertEqual(found.model, "Sonos Play:1")
        self.assertEqual(found.id, "uuid:RINCON_123456789")
        self.assertEqual(found.host, "127.0.0.1")
        self.assertEqual(
            found.config,
            {
                "host": "127.0.0.1",
                "location": (
                    f"http://127.0.0.1:{self.port}/xml/device_description.xml"
                ),
                "udn": "uuid:RINCON_123456789",
            },
        )
        self.assertEqual(
            self.fake.requests[0],
            ("GetVolume", "/MediaRenderer/RenderingControl/Control"),
        )

    async def test_probe_falls_back_to_unicast_ssdp(self):
        loop = asyncio.get_running_loop()
        transport, responder = await loop.create_datagram_endpoint(
            lambda: FakeSsdpResponder(self.location),
            local_addr=("127.0.0.1", 0),
        )
        try:
            ssdp_port = transport.get_extra_info("sockname")[1]
            found = await UpnpDriver.probe(
                {
                    "host": "127.0.0.1",
                    "port": self.port,
                    "ssdp_port": ssdp_port,
                }
            )
            self.assertEqual(found.id, "uuid:RINCON_123456789")
            self.assertEqual(found.config["location"], self.location)
            self.assertEqual(len(responder.requests), 1)
            request = responder.requests[0]
            self.assertIn("M-SEARCH * HTTP/1.1", request)
            self.assertIn(f"ST: {SERVICE_TYPE}", request)
        finally:
            transport.close()

    async def test_discovery_uses_the_nested_renderer_identity(self):
        await self.fake.close()
        self.fake = FakeUpnpRenderer(nested=True, multiple_nested=True)
        self.port = await self.fake.start()
        self.location = f"http://127.0.0.1:{self.port}/device.xml"
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: FakeSsdpResponder(self.location),
            local_addr=("127.0.0.1", 0),
        )
        old_address = ssdp.SSDP_ADDRESS
        try:
            ssdp.SSDP_ADDRESS = transport.get_extra_info("sockname")
            found = await UpnpDriver.discover(0.1)
            self.assertEqual(
                {item.id: item.model for item in found},
                {
                    "uuid:RINCON_123456789": "Sonos Play:1",
                    "uuid:RINCON_SECOND": "Sonos Play:3",
                },
            )
            self.assertTrue(all(item.host == "127.0.0.1" for item in found))
            self.assertTrue(
                all(item.config["location"] == self.location for item in found)
            )
            kitchen = next(
                item for item in found if item.id == "uuid:RINCON_SECOND"
            )
            self.assertEqual(kitchen.config["udn"], kitchen.id)
            driver = UpnpDriver(kitchen.config)
            status = await driver.status()
            self.assertEqual(status.volume, 75)
            self.assertEqual(await driver.set_volume(68), 68)
            self.assertEqual(self.fake.nested_volumes, {"living": 35, "kitchen": 68})
        finally:
            ssdp.SSDP_ADDRESS = old_address
            transport.close()

    async def test_saved_location_on_previous_host_is_discarded(self):
        previous = FakeUpnpRenderer(host="127.0.0.1")
        previous_port = await previous.start()
        current = FakeUpnpRenderer(sonos=True, host="127.0.0.2")
        current.volume = 73
        current_port = await current.start()
        try:
            driver = UpnpDriver(
                {
                    "host": "127.0.0.2",
                    "location": f"http://127.0.0.1:{previous_port}/device.xml",
                    "port": current_port,
                }
            )

            self.assertEqual((await driver.status()).volume, 73)
            self.assertEqual(await driver.set_volume(44), 44)
            self.assertEqual(previous.requests, [])
            self.assertTrue(current.requests)
        finally:
            await previous.close()
            await current.close()

    async def test_discovery_accepts_location_hostname_matching_sender_address(self):
        await self.fake.close()
        self.fake = FakeUpnpRenderer()
        self.port = await self.fake.start()
        location = f"http://localhost:{self.port}/device.xml"
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: FakeSsdpResponder(location),
            local_addr=("127.0.0.1", 0),
        )
        old_address = ssdp.SSDP_ADDRESS
        try:
            ssdp.SSDP_ADDRESS = transport.get_extra_info("sockname")
            found = await UpnpDriver.discover(0.1)
            self.assertEqual(
                [(item.id, item.host) for item in found],
                [("uuid:RINCON_123456789", "127.0.0.1")],
            )
        finally:
            ssdp.SSDP_ADDRESS = old_address
            transport.close()

    async def test_probe_accepts_equivalent_expanded_ipv6_location(self):
        await self.fake.close()
        self.fake = FakeUpnpRenderer(host="::1")
        self.port = await self.fake.start()
        location = f"http://[0:0:0:0:0:0:0:1]:{self.port}/device.xml"

        found = await UpnpDriver.probe({"host": "::1", "location": location})

        self.assertIsNotNone(found)
        self.assertEqual(found.host, "::1")
        self.assertEqual(found.id, "uuid:RINCON_123456789")

    async def test_non_renderers_are_not_probed_or_discovered(self):
        await self.fake.close()
        self.fake = FakeUpnpRenderer(include_service=False)
        self.port = await self.fake.start()
        self.location = f"http://127.0.0.1:{self.port}/device.xml"
        self.assertIsNone(
            await UpnpDriver.probe(
                {"host": "127.0.0.1", "location": self.location}
            )
        )

        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: FakeSsdpResponder(self.location),
            local_addr=("127.0.0.1", 0),
        )
        old_address = ssdp.SSDP_ADDRESS
        try:
            ssdp.SSDP_ADDRESS = transport.get_extra_info("sockname")
            self.assertEqual(await UpnpDriver.discover(0.1), [])
        finally:
            ssdp.SSDP_ADDRESS = old_address
            transport.close()

    async def test_soap_fault_is_reported_as_refused(self):
        self.fake.fault = ("701", "Transition not available")
        with self.assertRaisesRegex(Refused, "error 701: Transition not available"):
            await self.driver.status()


if __name__ == "__main__":
    unittest.main()
