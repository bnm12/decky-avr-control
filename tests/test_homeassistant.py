from __future__ import annotations

import asyncio
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from tests import PY_MODULES  # noqa: F401
from avr_control.drivers.base import (
    MODE,
    MUTE,
    POWER,
    VOLUME,
    DriverError,
    Refused,
    Unsupported,
)
from avr_control.drivers.homeassistant import (
    SELECT_SOUND_MODE,
    TURN_OFF,
    TURN_ON,
    VOLUME_MUTE,
    VOLUME_SET,
    HomeAssistantDriver,
)


TOKEN = "test-long-lived-token"
ENTITY_ID = "media_player.living_room_receiver"
ALL_FEATURES = VOLUME_SET | VOLUME_MUTE | TURN_ON | TURN_OFF | SELECT_SOUND_MODE


class FakeHomeAssistant:
    def __init__(self):
        self.state = "playing"
        self.attributes = {
            "friendly_name": "Living Room Receiver",
            "volume_level": 0.42,
            "is_volume_muted": False,
            "sound_mode": "Stereo",
            "sound_mode_list": ["Stereo", "Direct", "Dolby"],
            "supported_features": ALL_FEATURES,
        }
        self.services: list[tuple[str, dict]] = []
        self.httpd = None
        self.thread = None

    def start(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format, *_args):
                return

            def _respond(self, status: int, value):
                body = json.dumps(value).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authorized(self) -> bool:
                if self.headers.get("Authorization") == f"Bearer {TOKEN}":
                    return True
                self._respond(401, {"message": "unauthorized"})
                return False

            def do_GET(self):
                if not self._authorized():
                    return
                path = urlsplit(self.path).path
                if path == "/api/":
                    self._respond(200, {"message": "API running."})
                    return
                if path.startswith("/api/states/"):
                    requested_id = unquote(path[len("/api/states/") :])
                    if requested_id != ENTITY_ID:
                        self._respond(404, {"message": "not found"})
                        return
                    self._respond(
                        200,
                        {
                            "entity_id": requested_id,
                            "state": fake.state,
                            "attributes": dict(fake.attributes),
                        },
                    )
                    return
                self._respond(404, {"message": "not found"})

            def do_POST(self):
                if not self._authorized():
                    return
                path = urlsplit(self.path).path
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                if payload.get("entity_id") != ENTITY_ID:
                    self._respond(404, {"message": "not found"})
                    return
                service = path.rsplit("/", 1)[-1]
                fake.services.append((service, dict(payload)))
                if service == "volume_set":
                    fake.attributes["volume_level"] = payload["volume_level"]
                elif service == "volume_mute":
                    fake.attributes["is_volume_muted"] = payload["is_volume_muted"]
                elif service == "turn_on":
                    fake.state = "on"
                elif service == "turn_off":
                    fake.state = "off"
                elif service == "select_sound_mode":
                    mode = payload["sound_mode"]
                    if mode not in fake.attributes["sound_mode_list"]:
                        self._respond(400, {"message": "unknown sound mode"})
                        return
                    fake.attributes["sound_mode"] = mode
                else:
                    self._respond(404, {"message": "unknown service"})
                    return
                self._respond(200, [])

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self.httpd.server_address[1]

    def close(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
        if self.thread:
            self.thread.join(timeout=2)


class HomeAssistantDriverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fake = FakeHomeAssistant()
        self.port = self.fake.start()
        self.url = f"http://127.0.0.1:{self.port}"
        self.config = {
            "url": self.url,
            "token": TOKEN,
            "entity_id": ENTITY_ID,
        }
        self.driver = HomeAssistantDriver(self.config)

    async def asyncTearDown(self):
        await asyncio.to_thread(self.fake.close)

    async def test_config_scale_status_and_capabilities(self):
        self.assertEqual(self.driver.key, "homeassistant")
        self.assertEqual(self.driver.label, "Home Assistant")
        self.assertEqual(
            [field.key for field in self.driver.config_fields],
            ["url", "token", "entity_id"],
        )
        self.assertTrue(self.driver.config_fields[1].secret)
        self.assertEqual(
            (self.driver.scale.min, self.driver.scale.max, self.driver.scale.unit,
             self.driver.scale.zero, self.driver.scale.steps_per_unit,
             self.driver.scale.default_cap),
            (0, 100, "%", 0, 1, 60),
        )

        status = await self.driver.status()
        self.assertEqual((status.volume, status.muted, status.power, status.mode),
                         (42, False, True, "Stereo"))
        self.assertEqual(self.driver.capabilities, {VOLUME, MUTE, POWER, MODE})

        self.fake.attributes["supported_features"] = VOLUME_SET | TURN_ON | SELECT_SOUND_MODE
        await self.driver.status()
        self.assertEqual(self.driver.capabilities, {VOLUME, POWER, MODE})
        with self.assertRaisesRegex(Unsupported, "doesn't support turning off"):
            await self.driver.set_power(False)

    async def test_off_standby_unavailable_and_unknown_state_mapping(self):
        self.fake.state = "off"
        self.assertFalse((await self.driver.status()).power)
        self.fake.state = "standby"
        self.assertFalse((await self.driver.status()).power)
        for state in ("unavailable", "unknown"):
            self.fake.state = state
            with self.subTest(state=state), self.assertRaisesRegex(
                DriverError, f"entity {ENTITY_ID} is {state}"
            ):
                await self.driver.status()

    async def test_setters_send_services_and_confirm_values(self):
        self.assertEqual(await self.driver.set_volume(77), 77)
        self.assertTrue(await self.driver.set_mute(True))
        self.assertFalse(await self.driver.set_power(False))
        self.assertTrue(await self.driver.set_power(True))
        self.assertEqual(await self.driver.set_mode("Direct"), "Direct")
        self.assertEqual(
            [service for service, _ in self.fake.services],
            ["volume_set", "volume_mute", "turn_off", "turn_on", "select_sound_mode"],
        )
        self.assertEqual(self.fake.services[0][1]["volume_level"], 0.77)
        self.assertTrue(self.fake.services[1][1]["is_volume_muted"])
        self.assertEqual(self.fake.services[-1][1]["sound_mode"], "Direct")

    async def test_modes_and_unknown_mode_refusal(self):
        modes = await self.driver.list_modes()
        self.assertEqual([(mode.code, mode.name) for mode in modes],
                         [("Stereo", "Stereo"), ("Direct", "Direct"), ("Dolby", "Dolby")])
        self.assertEqual(self.driver.mode_name("anything"), "anything")
        before = len(self.fake.services)
        with self.assertRaisesRegex(Refused, "doesn't offer that sound mode"):
            await self.driver.set_mode("Not a mode")
        self.assertEqual(len(self.fake.services), before)

    async def test_authentication_and_missing_entity_errors_do_not_leak_token(self):
        bad_token = "do-not-leak-this-token"
        bad_driver = HomeAssistantDriver({**self.config, "token": bad_token})
        with self.assertRaises(DriverError) as error:
            await bad_driver.status()
        self.assertEqual(str(error.exception), "Home Assistant rejected the token")
        self.assertNotIn(bad_token, str(error.exception))

        missing = HomeAssistantDriver(
            {**self.config, "entity_id": "media_player.missing"}
        )
        with self.assertRaisesRegex(DriverError, "Entity media_player.missing not found"):
            await missing.status()

    async def test_probe_configuration_and_found_result(self):
        self.assertIsNone(await HomeAssistantDriver.probe({"url": self.url}))
        found = await HomeAssistantDriver.probe(self.config)
        self.assertIsNotNone(found)
        self.assertEqual(found.driver, "homeassistant")
        self.assertEqual(found.model, "Living Room Receiver")
        self.assertEqual(found.id, f"ha:{ENTITY_ID}")
        self.assertEqual(found.host, "127.0.0.1")
        self.assertEqual(found.config, self.config)
        self.assertNotIn(TOKEN, found.model)
        self.assertEqual(await HomeAssistantDriver.discover(0.01), [])

    async def test_token_is_redacted_from_error_and_found_model(self):
        url_with_token = self.url.replace("127.0.0.1", TOKEN)
        driver = HomeAssistantDriver(
            {"url": url_with_token, "token": TOKEN, "entity_id": ENTITY_ID}
        )
        with self.assertRaises(DriverError) as error:
            await driver.status()
        self.assertNotIn(TOKEN, str(error.exception))

        self.fake.attributes["friendly_name"] = f"Receiver {TOKEN}"
        found = await HomeAssistantDriver.probe(self.config)
        self.assertEqual(found.model, "Receiver [redacted]")
        self.assertNotIn(TOKEN, found.model)


if __name__ == "__main__":
    unittest.main()
