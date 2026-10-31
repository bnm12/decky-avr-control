from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from tests import PY_MODULES  # noqa: F401
from tests.fakes import InMemoryDuplex
from avr_control.drivers.base import Status
from avr_control.drivers.denon import (
    DenonDriver,
    HttpTransport,
    TelnetTransport,
    _read_telnet_line,
)
from avr_control.drivers.eiscp import EiscpDriver, _read_packet, packet
from avr_control.drivers.heos import HeosDriver, _read_json_line
from avr_control.drivers.pioneer_telnet import PioneerTelnetDriver, _read_line
from avr_control.drivers.receiver_session import ReceiverSession
from avr_control.drivers.homeassistant import HomeAssistantDriver
from avr_control.drivers.sony import SonyDriver
from avr_control.drivers.upnp import UpnpDriver
from avr_control.drivers.yamaha import YamahaDriver
from avr_control.drivers.yamaha_ync import YamahaYNCDriver


class PushWatchTests(unittest.IsolatedAsyncioTestCase):
    async def _wait_for(self, queue, count):
        return [
            await asyncio.wait_for(queue.get(), 1)
            for _ in range(count)
        ]

    async def test_eiscp_watch_emits_known_partial_status_and_ignores_noise(self):
        async def handle(_reader, writer):
            writer.write(packet("!1NJA2-http://receiver/album_art.cgi"))
            writer.write(packet("!1XYZignored"))
            writer.write(packet("!1MVL30"))
            writer.write(packet("!1AMT01"))
            await writer.drain()
            await _reader.readline()

        duplex = InMemoryDuplex(handle)
        session = ReceiverSession(duplex.connect, _read_packet)
        events = asyncio.Queue()
        driver = EiscpDriver({"host": "memory"})
        with patch("avr_control.drivers.eiscp.shared_session", return_value=session):
            unsubscribe = await driver.watch(events.put_nowait)
            first, second = await self._wait_for(events, 2)
            self.assertEqual(first, Status(volume=0x30))
            self.assertEqual(second, Status(muted=True))
            await unsubscribe()
        self.assertTrue(duplex.connections[0][1].is_closing())
        await session.close()
        await duplex.close()

    async def test_denon_telnet_watch_emits_partial_state_and_ignores_noise(self):
        async def handle(_reader, writer):
            writer.write(b"MV455\rNOISE\rMUON\r")
            await writer.drain()
            await _reader.readline()

        duplex = InMemoryDuplex(handle)
        session = ReceiverSession(duplex.connect, _read_telnet_line)
        driver = DenonDriver({"host": "memory"})
        with patch("avr_control.drivers.denon.shared_session", return_value=session):
            driver._transport_adapter = TelnetTransport("memory", 23)
            events = asyncio.Queue()
            unsubscribe = await driver.watch(events.put_nowait)
            first, second = await self._wait_for(events, 2)
            self.assertEqual(first, Status(volume=91))
            self.assertEqual(second, Status(muted=True))
            await unsubscribe()
        self.assertTrue(duplex.connections[0][1].is_closing())
        await session.close()
        await duplex.close()

    async def test_legacy_pioneer_watch_emits_partial_state_and_ignores_noise(self):
        async def handle(_reader, writer):
            writer.write(b"VOL071\r\nUNKNOWN\r\nPWR2\r\n")
            await writer.drain()
            await _reader.readline()

        duplex = InMemoryDuplex(handle)
        session = ReceiverSession(duplex.connect, _read_line)
        driver = PioneerTelnetDriver({"host": "memory", "port": 8102})
        with patch(
            "avr_control.drivers.pioneer_telnet.shared_session",
            return_value=session,
        ):
            events = asyncio.Queue()
            unsubscribe = await driver.watch(events.put_nowait)
            first, second = await self._wait_for(events, 2)
            self.assertEqual(first, Status(volume=71))
            self.assertEqual(second, Status(power=False))
            await unsubscribe()
        self.assertTrue(duplex.connections[0][1].is_closing())
        await session.close()
        await duplex.close()

    async def test_heos_watch_registers_and_filters_player_events(self):
        registration_count = 0

        async def handle(reader, writer):
            nonlocal registration_count
            while line := await reader.readline():
                command = line.decode("ascii").strip()
                if command.startswith("heos://player/get_players"):
                    response = {
                        "heos": {
                            "command": "player/get_players",
                            "result": "success",
                            "message": "",
                        },
                        "payload": {
                            "players": [
                                {"name": "Living Room", "pid": 42, "ip": "memory"}
                            ]
                        },
                    }
                    writer.write((json.dumps(response) + "\r\n").encode("ascii"))
                    await writer.drain()
                elif command.startswith("heos://player/get_volume"):
                    response = {
                        "heos": {
                            "command": "player/get_volume",
                            "result": "success",
                            "message": "pid='42'&level='63'",
                        }
                    }
                    writer.write((json.dumps(response) + "\r\n").encode("ascii"))
                    await writer.drain()
                elif command.startswith("heos://player/get_mute"):
                    response = {
                        "heos": {
                            "command": "player/get_mute",
                            "result": "success",
                            "message": "pid='42'&state='off'",
                        }
                    }
                    writer.write((json.dumps(response) + "\r\n").encode("ascii"))
                    await writer.drain()
                elif command.startswith(
                    "heos://system/register_for_change_events?enable=on"
                ):
                    registration_count += 1
                    response = {
                        "heos": {
                            "command": "system/register_for_change_events",
                            "result": "success",
                            "message": "",
                        }
                    }
                    event = {
                        "heos": {
                            "command": "event/player_volume_changed",
                            "result": "success",
                            "message": "pid='42'&level='63'&mute='off'",
                        }
                    }
                    other = {
                        "heos": {
                            "command": "event/player_volume_changed",
                            "result": "success",
                            "message": "pid='99'&level='10'",
                        }
                    }
                    writer.write((json.dumps(response) + "\r\n").encode("ascii"))
                    writer.write((json.dumps(other) + "\r\n").encode("ascii"))
                    writer.write((json.dumps(event) + "\r\n").encode("ascii"))
                    await writer.drain()
            writer.close()

        duplex = InMemoryDuplex(handle)
        session = ReceiverSession(duplex.connect, _read_json_line)
        driver = HeosDriver({"host": "memory", "port": 1255})
        events = asyncio.Queue()
        with patch("avr_control.drivers.heos.shared_session", return_value=session):
            unsubscribe = await driver.watch(events.put_nowait)
            self.assertEqual(
                await asyncio.wait_for(events.get(), 1),
                Status(volume=63, muted=False),
            )
            self.assertEqual(registration_count, 1)
            await session.close()
            self.assertEqual(await driver.status(), Status(volume=63, muted=False))
            for _ in range(100):
                if registration_count == 2:
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(registration_count, 2)
            await unsubscribe()
        self.assertTrue(duplex.connections[0][1].is_closing())
        await session.close()
        await duplex.close()

    async def test_http_and_rest_drivers_remain_polling_only(self):
        on_change = lambda _status: None
        denon = DenonDriver({"host": "memory"})
        denon._transport_adapter = HttpTransport("memory", 80)
        self.assertIsNone(await denon.watch(on_change))
        for driver in (
            HomeAssistantDriver({}),
            SonyDriver({}),
            UpnpDriver({}),
            YamahaDriver({}),
            YamahaYNCDriver({}),
        ):
            self.assertIsNone(await driver.watch(on_change))


if __name__ == "__main__":
    unittest.main()
