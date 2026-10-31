from __future__ import annotations

import asyncio
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests import PY_MODULES  # noqa: F401
from tests.fakes import FakeSsdpResponder
from avr_control import ssdp


class DescriptionHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = (
            '<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0">'
            "<device><friendlyName>Living Room AVR</friendlyName>"
            "<manufacturer>Example Audio</manufacturer><modelName>AVR 1</modelName>"
            "<UDN>uuid:receiver-1</UDN></device></root>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/xml")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *args):
        pass


class SsdpTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_deduplicates_and_fetches_upnp_description(self):
        http_server = ThreadingHTTPServer(("127.0.0.1", 0), DescriptionHandler)
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
            results = await ssdp.search("upnp:rootdevice", 0.1)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["location"], location)
            self.assertEqual(results[0]["ip"], "127.0.0.1")
            self.assertIn("ST: upnp:rootdevice", responder.requests[0])

            description = await ssdp.fetch_description(location)
            self.assertEqual(description["friendlyName"], "Living Room AVR")
            self.assertEqual(description["manufacturer"], "Example Audio")
            self.assertEqual(description["modelName"], "AVR 1")
            self.assertEqual(description["UDN"], "uuid:receiver-1")
            self.assertIn("<friendlyName>", description["raw_xml"])
        finally:
            ssdp.SSDP_ADDRESS = old_address
            transport.close()
            http_server.shutdown()
            http_server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
