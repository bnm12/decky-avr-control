from __future__ import annotations

import asyncio
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from tests import PY_MODULES  # noqa: F401
from tests.fakes import FakeSsdpResponder
from avr_control import ssdp
from avr_control.drivers.base import MUTE, VOLUME, DriverError, ModeState
from avr_control.drivers.heos import HEOS_SSDP_ST, HeosDriver


class FakeHeos:
    def __init__(self):
        self.players = [
            {
                "pid": 101,
                "name": "Living Room",
                "model": "HEOS AVR",
                "serial": "HEOS-101",
                "ip": "127.0.0.1",
                "level": 34,
                "mute": "off",
            },
            {
                "pid": 202,
                "name": "Kitchen",
                "model": "HEOS 1",
                "serial": "HEOS-202",
                "ip": "127.0.0.2",
                "level": 58,
                "mute": "on",
            },
        ]
        self.commands: list[str] = []
        self.server = None

    async def start(self):
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def close(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def send(self, writer, command, payload=None, *, result="success", message=""):
        response = {"heos": {"command": command, "result": result, "message": message}}
        if payload is not None:
            response["payload"] = payload
        writer.write((json.dumps(response) + "\r\n").encode("ascii"))
        await writer.drain()

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            while line := await reader.readline():
                request = line.decode("ascii").strip()
                if not request.startswith("heos://"):
                    continue
                path, _, query = request[len("heos://") :].partition("?")
                args = parse_qs(query)
                self.commands.append(path)
                await self.send(
                    writer,
                    path,
                    result="success",
                    message="command under process",
                )
                if path == "system/heart_beat":
                    payload = {}
                elif path == "player/get_players":
                    payload = [
                        {
                            key: player[key]
                            for key in ("pid", "name", "model", "serial", "ip")
                            if key in player
                        }
                        for player in self.players
                    ]
                elif path.startswith("player/"):
                    pid = int(args["pid"][0])
                    player = next(item for item in self.players if item["pid"] == pid)
                    if path == "player/get_volume":
                        await self.send(
                            writer,
                            path,
                            message=f"pid='{pid}'&level='{player['level']}'",
                        )
                        continue
                    elif path == "player/set_volume":
                        player["level"] = int(args["level"][0])
                        payload = {}
                    elif path == "player/get_mute":
                        await self.send(
                            writer,
                            path,
                            message=f"pid='{pid}'&state='{player['mute']}'",
                        )
                        continue
                    elif path == "player/set_mute":
                        player["mute"] = args["state"][0]
                        payload = {}
                    else:
                        await self.send(
                            writer, path, result="fail", message="unknown command"
                        )
                        continue
                else:
                    await self.send(writer, path, result="fail", message="unknown command")
                    continue
                await self.send(writer, path, payload)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


class HeosDescriptionHandler(BaseHTTPRequestHandler):
    friendly_name = "Denon Living Room"

    def do_GET(self):
        body = (
            '<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0">'
            f"<device><friendlyName>{self.friendly_name}</friendlyName>"
            "<manufacturer>Denon</manufacturer><modelName>AVR-X2800H</modelName>"
            "<serialNumber>DENON-2800</serialNumber>"
            "<UDN>uuid:heos-receiver-1</UDN></device></root>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/xml")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *args):
        pass


class HeosDriverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fake = FakeHeos()
        self.port = await self.fake.start()
        self.driver = HeosDriver({"host": "127.0.0.1", "port": self.port})

    async def asyncTearDown(self):
        await self.fake.close()

    async def test_status_and_setters_skip_interim_replies_and_read_back(self):
        self.assertEqual(self.driver.capabilities, {VOLUME, MUTE})
        self.assertEqual(
            self.driver.scale.as_dict(),
            {
                "min": 0,
                "max": 100,
                "unit": "%",
                "zero": 0,
                "steps_per_unit": 1,
                "default_cap": 60,
            },
        )

        status = await self.driver.status()
        self.assertEqual((status.volume, status.muted, status.power), (34, False, None))
        self.assertEqual(await self.driver.set_volume(67), 67)
        self.assertTrue(await self.driver.set_mute(True))
        self.assertEqual(self.fake.players[0]["level"], 67)
        self.assertEqual(self.fake.players[0]["mute"], "on")
        self.assertGreaterEqual(self.fake.commands.count("player/get_volume"), 2)
        self.assertGreaterEqual(self.fake.commands.count("player/get_mute"), 2)
        self.assertNotIn("system/register_for_change_events", self.fake.commands)

    async def test_mode_state_has_no_io_for_driver_without_mode_support(self):
        driver = HeosDriver({})
        self.assertEqual(await driver.mode_state(), ModeState(None, (), False))
        self.assertEqual(self.fake.commands, [])
        self.assertEqual(HeosDriver.mode_label("custom"), "Mode custom")

    async def test_configured_player_name_beats_the_player_at_the_host_ip(self):
        by_name = HeosDriver(
            {"host": "127.0.0.1", "port": self.port, "player": "Kitchen"}
        )
        self.assertEqual((await by_name.status()).volume, 58)

        by_ip = HeosDriver({"host": "127.0.0.1", "port": self.port})
        self.assertEqual((await by_ip.status()).volume, 34)

    async def test_discovered_config_selects_its_player_when_players_have_no_ip(self):
        for player in self.fake.players:
            player.pop("ip")
        found, _ = await self._discover("Kitchen")
        self.assertEqual(len(found), 1)
        driver = HeosDriver({**found[0].config, "port": self.port})
        self.assertEqual((await driver.status()).volume, 58)

    async def test_serial_selects_player_after_it_is_renamed(self):
        for player in self.fake.players:
            player.pop("ip")
        self.fake.players[1]["name"] = "Garage"
        driver = HeosDriver(
            {
                "host": "127.0.0.1",
                "port": self.port,
                "player": "Kitchen",
                "serial": "HEOS-202",
                "serial_for": "kitchen",
            }
        )
        self.assertEqual((await driver.status()).volume, 58)

    async def test_serial_learned_for_another_player_is_ignored_and_relearned(self):
        for player in self.fake.players:
            player.pop("ip")
        driver = HeosDriver(
            {
                "host": "127.0.0.1",
                "port": self.port,
                "player": "Kitchen",
                "serial": "HEOS-101",
                "serial_for": "living room",
            }
        )

        self.assertEqual((await driver.status()).volume, 58)
        self.assertEqual(driver.config["serial"], "HEOS-202")
        self.assertEqual(driver.config["serial_for"], "kitchen")

    async def test_serial_learned_for_a_host_is_ignored_once_a_player_is_named(self):
        for player in self.fake.players:
            player.pop("ip")
        driver = HeosDriver(
            {
                "host": "127.0.0.1",
                "port": self.port,
                "player": "Kitchen",
                "serial": "HEOS-101",
                "serial_for": "127.0.0.1",
            }
        )

        self.assertEqual((await driver.status()).volume, 58)

    async def test_only_player_is_selected_and_ambiguous_selection_lists_names(self):
        self.fake.players = self.fake.players[:1]
        self.assertEqual((await self.driver.status()).volume, 34)

        self.fake.players.append(
            {
                "pid": 303,
                "name": "Office",
                "model": "HEOS 3",
                "serial": "HEOS-303",
                "ip": "127.0.0.4",
                "level": 12,
                "mute": "off",
            }
        )
        self.fake.players[0]["ip"] = "127.0.0.3"
        with self.assertRaisesRegex(DriverError, "Living Room.*Office"):
            await HeosDriver({"host": "127.0.0.1", "port": self.port}).status()

    async def test_probe_uses_configured_port_and_returns_none_for_no_heos_reply(self):
        found = await HeosDriver.probe({"host": "127.0.0.1", "port": self.port})
        self.assertIsNotNone(found)
        self.assertEqual(found.driver, "heos")
        self.assertEqual(found.config["port"], self.port)
        self.assertEqual(found.config["model"], "HEOS AVR")
        self.assertEqual(found.config["player"], "Living Room")
        self.assertEqual(found.config["serial"], "HEOS-101")
        self.assertEqual(found.config["serial_for"], "living room")
        self.assertEqual(found.config["ip"], "127.0.0.1")
        self.assertNotIn("name", found.config)

        async def other_protocol(reader, writer):
            await reader.readline()
            writer.write(b"not a HEOS response\r\n")
            await writer.drain()
            writer.close()

        other_server = await asyncio.start_server(other_protocol, "127.0.0.1", 0)
        other_port = other_server.sockets[0].getsockname()[1]
        try:
            self.assertIsNone(
                await HeosDriver.probe({"host": "127.0.0.1", "port": other_port})
            )
        finally:
            other_server.close()
            await other_server.wait_closed()

    async def _discover(self, friendly_name: str = "Denon Living Room"):
        handler = type(
            "Handler", (HeosDescriptionHandler,), {"friendly_name": friendly_name}
        )
        http_server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
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
            found = await HeosDriver.discover(0.1)
        finally:
            ssdp.SSDP_ADDRESS = old_address
            transport.close()
            http_server.shutdown()
            http_server.server_close()
            thread.join(timeout=2)
        return found, responder.requests

    async def test_discovery_uses_heos_ssdp_target_and_stable_description_identity(self):
        found, requests = await self._discover()
        self.assertIn(f"ST: {HEOS_SSDP_ST}", requests[0])

        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].model, "AVR-X2800H")
        self.assertEqual(found[0].id, "heos:DENON-2800")
        self.assertEqual(found[0].host, "127.0.0.1")
        self.assertEqual(found[0].config["player"], "Denon Living Room")
        self.assertEqual(found[0].config["serial"], "DENON-2800")
        self.assertEqual(found[0].config["serial_for"], "denon living room")
        self.assertNotIn("name", found[0].config)


if __name__ == "__main__":
    unittest.main()
