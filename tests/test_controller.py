from __future__ import annotations

import asyncio
import os
import tempfile
import unittest

from tests import PY_MODULES  # noqa: F401
from avr_control.controller import Controller
from avr_control.drivers.base import MODE, MUTE, POWER, VOLUME, Driver, DriverError, Found, Mode, Status, VolumeScale
from avr_control.settings import SettingsStore


class FakeClock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value

    async def sleep(self, seconds: float):
        self.value += seconds
        await asyncio.sleep(0)


class MemoryDriver(Driver):
    key = "memory"
    label = "Memory Receiver"
    capabilities = frozenset({VOLUME, MUTE, POWER, MODE})
    scale = VolumeScale(min=0, max=185, unit="dB", zero=164, steps_per_unit=2, default_cap=100)
    state = Status(volume=100, muted=False, power=True, mode="00")
    fail_hosts: set[str] = set()
    discovered: list[Found] = []
    probe_calls: list[dict] = []
    discovery_calls = 0
    actions: list[tuple] = []

    async def status(self):
        if self.config.get("host") in self.fail_hosts:
            raise DriverError("receiver unreachable")
        self.actions.append(("status", self.config.get("host")))
        return Status(self.state.volume, self.state.muted, self.state.power, self.state.mode)

    async def set_volume(self, raw):
        self.actions.append(("volume", raw))
        self.state.volume = raw
        return raw

    async def set_mute(self, muted):
        self.actions.append(("mute", muted))
        self.state.muted = muted
        return muted

    async def set_power(self, on):
        self.actions.append(("power", on))
        self.state.power = on
        return on

    async def set_mode(self, code):
        self.actions.append(("mode", code))
        self.state.mode = code
        return code

    async def list_modes(self):
        return [Mode("00", "Stereo"), Mode("11", "Direct")]

    def mode_name(self, code):
        return {"00": "Stereo", "11": "Direct"}.get(code, f"Mode {code}")

    @classmethod
    async def discover(cls, timeout):
        cls.discovery_calls += 1
        return list(cls.discovered)

    @classmethod
    async def probe(cls, config):
        cls.probe_calls.append(dict(config))
        return Found(
            driver=cls.key,
            model="Memory Receiver",
            id="new-id",
            host=config["host"],
            config={**config, "port": 1234},
        )


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, "settings.json")
        self.store = SettingsStore(self.path)
        self.clock = FakeClock()
        self.reset_driver()
        self.controller = Controller(
            self.store,
            registry=[MemoryDriver],
            clock=self.clock,
            sleep=self.clock.sleep,
            reapply_delay=4,
            rediscover_interval=30,
        )

    def tearDown(self):
        self.temp.cleanup()

    def reset_driver(self):
        MemoryDriver.state = Status(100, False, True, "00")
        MemoryDriver.fail_hosts = set()
        MemoryDriver.discovered = []
        MemoryDriver.probe_calls = []
        MemoryDriver.discovery_calls = 0
        MemoryDriver.actions = []

    def save_settings(self, data=None):
        self.store.save(
            data
            or {
                "version": 2,
                "receiver": {
                    "driver": "memory",
                    "config": {"host": "receiver"},
                    "id": "stable-id",
                },
                "cap": None,
                "late_cap": None,
                "late": {"active": False},
                "games": {},
            }
        )

    async def test_cap_clamping_late_night_restore_and_state_contract(self):
        self.save_settings()
        state = await self.controller.get_state()
        self.assertTrue(state["ok"])
        self.assertEqual(state["driver"], "memory")
        self.assertEqual(state["driver_label"], "Memory Receiver")
        self.assertEqual(state["scale"]["zero"], 164)
        self.assertIn("power", state["capabilities"])
        self.assertEqual(state["power"], True)
        settings = self.store.load()
        settings["cap"] = 0
        self.store.save(settings)
        self.assertEqual((await self.controller.get_state())["cap"], 100)
        self.assertEqual((await self.controller.set_volume(180))["volume"], 100)

        await self.controller.set_max(150)
        await self.controller.set_late_max(70)
        result = await self.controller.set_late_night(True)
        self.assertTrue(result["ok"])
        self.assertEqual(MemoryDriver.state.volume, 70)
        self.assertEqual(self.store.load()["late"], {"active": True, "prev_volume": 100})
        await self.controller.set_late_night(False)
        self.assertEqual(MemoryDriver.state.volume, 100)
        self.assertEqual(self.store.load()["late"], {"active": False})

    async def test_per_game_volume_mode_and_combined_opt_in(self):
        self.save_settings()
        await self.controller.set_current_game(11, "Volume only")
        self.assertTrue((await self.controller.set_game_remember("volume", True))["ok"])
        profile = self.store.load()["games"]["11"]
        self.assertEqual(profile["volume"], 100)
        self.assertNotIn("mode", profile)

        await self.controller.set_current_game(12, "Mode only")
        self.assertTrue((await self.controller.set_game_remember("mode", True))["ok"])
        profile = self.store.load()["games"]["12"]
        self.assertEqual(profile["mode"], "00")
        self.assertNotIn("volume", profile)

        await self.controller.set_current_game(13, "Both")
        await self.controller.set_game_remember("volume", True)
        await self.controller.set_game_remember("mode", True)
        profile = self.store.load()["games"]["13"]
        self.assertEqual((profile["volume"], profile["mode"]), (100, "00"))
        self.assertEqual(profile["driver"], "memory")

    async def test_running_profile_tracks_changes_and_late_night_does_not_leak(self):
        self.save_settings(
            {
                "version": 2,
                "receiver": {"driver": "memory", "config": {"host": "receiver"}},
                "cap": 150,
                "late_cap": 60,
                "late": {"active": False},
                "games": {
                    "21": {
                        "name": "Remembered",
                        "driver": "memory",
                        "volume": 80,
                        "mode": "00",
                    }
                },
            }
        )
        await self.controller.set_current_game(21, "Remembered")
        MemoryDriver.state.volume = 85
        MemoryDriver.state.mode = "11"
        await self.controller.get_state()
        profile = self.store.load()["games"]["21"]
        self.assertEqual((profile["volume"], profile["mode"]), (85, "11"))

        data = self.store.load()
        data["late"] = {"active": True, "prev_volume": 85}
        self.store.save(data)
        MemoryDriver.state.volume = 60
        await self.controller.get_state()
        self.assertEqual(self.store.load()["games"]["21"]["volume"], 85)

    async def test_six_second_suppression_and_driver_mismatch_skip_both_settings(self):
        self.save_settings(
            {
                "version": 2,
                "receiver": {"driver": "memory", "config": {"host": "receiver"}},
                "cap": 150,
                "late_cap": None,
                "late": {"active": False},
                "games": {
                    "22": {
                        "name": "Suppressed",
                        "driver": "memory",
                        "volume": 60,
                        "mode": "11",
                    }
                },
            }
        )
        await self.controller.game_started(22, "Suppressed")
        await asyncio.sleep(0)
        self.assertEqual(MemoryDriver.state.volume, 60)
        MemoryDriver.state.volume = 80
        MemoryDriver.state.mode = "00"
        await self.controller.get_state()
        profile = self.store.load()["games"]["22"]
        self.assertEqual((profile["volume"], profile["mode"]), (60, "11"))
        self.clock.value += 6
        await self.controller.get_state()
        profile = self.store.load()["games"]["22"]
        self.assertEqual((profile["volume"], profile["mode"]), (80, "00"))

        await self.controller.game_stopped(22)
        data = self.store.load()
        data["games"]["22"]["driver"] = "other-driver"
        data["games"]["22"]["volume"] = 40
        data["games"]["22"]["mode"] = "FF"
        self.store.save(data)
        MemoryDriver.actions.clear()
        result = await self.controller.game_started(22, "Suppressed")
        self.assertEqual(result, {"applied": False})
        self.assertEqual(MemoryDriver.actions, [])

    async def test_rediscovery_matches_stable_id_and_throttles_retries(self):
        self.save_settings()
        MemoryDriver.fail_hosts = {"old"}
        data = self.store.load()
        data["receiver"]["config"]["host"] = "old"
        self.store.save(data)
        MemoryDriver.discovered = [
            Found(
                driver="memory",
                model="Memory Receiver",
                id="stable-id",
                host="new",
                config={"host": "new"},
            )
        ]
        state = await self.controller.get_state()
        self.assertTrue(state["ok"])
        self.assertEqual(self.store.load()["receiver"]["config"]["host"], "new")
        self.assertEqual(MemoryDriver.discovery_calls, 1)

        data = self.store.load()
        data["receiver"]["config"]["host"] = "old"
        self.store.save(data)
        self.controller._active_driver = None
        self.clock.value += 5
        state = await self.controller.get_state()
        self.assertFalse(state["ok"])
        self.assertEqual(MemoryDriver.discovery_calls, 1)

        self.clock.value += 30
        state = await self.controller.get_state()
        self.assertTrue(state["ok"])
        self.assertEqual(MemoryDriver.discovery_calls, 2)

    async def test_auto_probe_on_manual_host_replaces_old_id(self):
        self.save_settings()
        data = self.store.load()
        data["receiver"]["id"] = "old-id"
        self.store.save(data)
        result = await self.controller.configure("auto", {"host": "manual-host"})
        self.assertTrue(result["ok"])
        self.assertEqual(MemoryDriver.probe_calls[-1]["host"], "manual-host")
        settings = self.store.load()
        self.assertIsNone(settings["receiver"]["id"])
        self.assertEqual(settings["receiver"]["driver"], "memory")

    async def test_discover_returns_driver_metadata_and_selection_remembers_driver(self):
        self.save_settings()
        MemoryDriver.discovered = [
            Found(
                driver="memory",
                model="Memory Receiver",
                id="abc",
                host="192.0.2.2",
                config={"host": "192.0.2.2", "port": 23},
            )
        ]
        found = await self.controller.discover()
        self.assertEqual(found[0]["driver"], "memory")
        self.assertEqual(found[0]["label"], "Memory Receiver")
        await self.controller.select_receiver("192.0.2.2", "abc")
        self.assertEqual(self.store.load()["receiver"]["driver"], "memory")
        self.assertEqual(self.store.load()["receiver"]["config"]["port"], 23)


if __name__ == "__main__":
    unittest.main()
