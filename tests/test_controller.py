from __future__ import annotations

import asyncio
import os
import tempfile
import unittest

from tests import PY_MODULES  # noqa: F401
from avr_control.controller import Controller
from avr_control.drivers.base import (
    MODE,
    MUTE,
    POWER,
    VOLUME,
    Driver,
    DriverError,
    Found,
    Mode,
    Refused,
    Status,
    Unsupported,
    VolumeScale,
)
from avr_control.drivers.denon import DenonDriver
from avr_control.drivers.eiscp import EiscpDriver
from avr_control.drivers.homeassistant import HomeAssistantDriver
from avr_control.drivers.pioneer_telnet import PioneerTelnetDriver
from avr_control.drivers.yamaha import YamahaDriver
from avr_control.settings import SettingsStore
from tests.test_homeassistant import ENTITY_ID, TOKEN, FakeHomeAssistant


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

    async def test_late_night_restore_respects_a_lowered_normal_cap(self):
        self.save_settings()
        await self.controller.get_state()
        await self.controller.set_max(100)
        await self.controller.set_late_max(70)
        await self.controller.set_late_night(True)
        await self.controller.set_max(60)

        result = await self.controller.set_late_night(False)

        self.assertTrue(result["ok"])
        self.assertEqual(MemoryDriver.state.volume, 60)

    async def test_caps_and_late_night_do_not_carry_to_a_different_receiver(self):
        class PercentDriver(Driver):
            key = "percent"
            label = "Percent Receiver"
            scale = VolumeScale(
                min=0, max=100, unit="%", zero=0, steps_per_unit=1, default_cap=60
            )
            capabilities = frozenset({VOLUME})

            def __init__(self, config):
                super().__init__(config)
                self.volume = 10

            async def status(self):
                return Status(volume=self.volume)

            async def set_volume(self, raw):
                self.volume = raw
                return raw

        self.controller.registry = [MemoryDriver, PercentDriver]
        self.save_settings(
            {
                "version": 2,
                "receiver": {
                    "driver": "memory",
                    "config": {"host": "old-receiver"},
                    "id": "old-id",
                },
                "cap": 100,
                "late_cap": 70,
                "late": {"active": True, "prev_volume": 50},
                "games": {},
            }
        )

        result = await self.controller.configure("percent", {"host": "new-receiver"})
        self.assertTrue(result["ok"])
        state = await self.controller.get_state()

        self.assertEqual(state["cap"], 60)
        self.assertEqual(state["late_cap"], 20)
        self.assertFalse(state["late"])
        self.assertEqual((await self.controller.set_volume(100))["volume"], 60)

    async def test_resaving_same_receiver_keeps_caps_and_late_night_restore(self):
        self.save_settings()
        await self.controller.get_state()
        await self.controller.set_max(50)
        await self.controller.set_late_max(40)
        await self.controller.set_late_night(True)
        self.assertEqual(MemoryDriver.state.volume, 40)

        result = await self.controller.configure("memory", {"host": "receiver"})
        settings = self.store.load()

        self.assertTrue(result["ok"])
        self.assertEqual(settings["cap"], 50)
        self.assertEqual(settings["late_cap"], 40)
        self.assertEqual(settings["late"], {"active": True, "prev_volume": 100})

    async def test_auto_configure_rebinds_state_when_same_host_has_a_new_receiver_id(self):
        old_binding = {"driver": "memory", "id": "STABLE-ID"}
        self.save_settings(
            {
                "version": 2,
                "receiver": {
                    "driver": "memory",
                    "config": {"host": "receiver"},
                    "id": "stable-id",
                },
                "cap": 50,
                "cap_receiver": old_binding,
                "late_cap": 40,
                "late_cap_receiver": old_binding,
                "late": {"active": True, "prev_volume": 80},
                "late_receiver": old_binding,
                "games": {},
            }
        )

        result = await self.controller.configure("auto", {"host": "receiver"})

        self.assertTrue(result["ok"])
        settings = self.store.load()
        self.assertEqual(settings["receiver"]["id"], "NEW-ID")
        self.assertIsNone(settings["cap"])
        self.assertIsNone(settings["late_cap"])
        self.assertEqual(settings["late"], {"active": False})

        MemoryDriver.fail_hosts = {"receiver"}
        MemoryDriver.discovered = [
            Found(
                driver="memory",
                model="Old Receiver",
                id="stable-id",
                host="old-receiver",
                config={"host": "old-receiver"},
            ),
            Found(
                driver="memory",
                model="New Receiver",
                id="new-id",
                host="new-receiver",
                config={"host": "new-receiver"},
            ),
        ]
        state = await self.controller.get_state()

        self.assertTrue(state["ok"], state)
        self.assertEqual(self.store.load()["receiver"]["config"]["host"], "new-receiver")
        self.assertEqual(state["cap"], MemoryDriver.scale.default_cap)

    async def test_auto_configure_updates_receiver_id_after_concurrent_resolution(self):
        probe_started = asyncio.Event()
        release_probe = asyncio.Event()

        class ConcurrentProbeDriver(Driver):
            key = "concurrent_probe"
            label = "Concurrent Probe"
            capabilities = frozenset({VOLUME})
            scale = VolumeScale(min=0, max=100, unit="%", default_cap=60)
            probe_calls = 0

            async def status(self):
                return Status(volume=40)

            @classmethod
            async def probe(cls, config):
                cls.probe_calls += 1
                if cls.probe_calls == 1:
                    probe_started.set()
                    await release_probe.wait()
                return Found(
                    driver=cls.key,
                    model=cls.label,
                    id="new-id",
                    host=config["host"],
                    config=dict(config),
                )

        ConcurrentProbeDriver.probe_calls = 0
        self.controller.registry = [ConcurrentProbeDriver]
        old_binding = {"driver": "concurrent_probe", "id": "OLD-ID"}
        self.save_settings(
            {
                "version": 2,
                "receiver": {
                    "driver": "concurrent_probe",
                    "config": {"host": "receiver"},
                    "id": "old-id",
                },
                "cap": 30,
                "cap_receiver": old_binding,
                "late_cap": 20,
                "late_cap_receiver": old_binding,
                "late": {"active": False},
                "games": {},
            }
        )
        configuring = asyncio.create_task(
            self.controller.configure("auto", {"host": "receiver"})
        )
        await probe_started.wait()

        state = await self.controller.get_state()
        release_probe.set()
        result = await configuring

        self.assertTrue(state["ok"], state)
        self.assertTrue(result["ok"], result)
        settings = self.store.load()
        self.assertEqual(settings["receiver"]["id"], "NEW-ID")
        self.assertIsNone(settings["cap"])
        self.assertIsNone(settings["late_cap"])

    async def test_switching_a_to_b_to_a_resets_caps_to_each_receivers_defaults(self):
        class PercentDriver(Driver):
            key = "percent"
            label = "Percent Receiver"
            scale = VolumeScale(
                min=0, max=100, unit="%", zero=0, steps_per_unit=1, default_cap=60
            )
            capabilities = frozenset({VOLUME})

            async def status(self):
                return Status(volume=10)

            async def set_volume(self, raw):
                return raw

        self.controller.registry = [MemoryDriver, PercentDriver]
        self.save_settings(
            {
                "version": 2,
                "receiver": {
                    "driver": "memory",
                    "config": {"host": "receiver-a"},
                    "id": "receiver-a-id",
                },
                "cap": None,
                "late_cap": None,
                "late": {"active": False},
                "games": {},
            }
        )
        await self.controller.set_max(50)
        await self.controller.set_late_max(40)
        await self.controller.set_late_night(True)

        self.controller._last_found = [
            Found(
                driver="memory",
                model="Memory Receiver",
                id="receiver-a-id",
                host="receiver-a",
                config={"host": "receiver-a"},
            ),
            Found(
                driver="percent",
                model="Percent Receiver",
                id="receiver-b-id",
                host="receiver-b",
                config={"host": "receiver-b"},
            ),
        ]
        await self.controller.select_receiver("receiver-b", "receiver-b-id")
        state_b = await self.controller.get_state()
        self.assertEqual(state_b["cap"], 60)
        self.assertEqual(state_b["late_cap"], 20)
        self.assertFalse(state_b["late"])

        await self.controller.select_receiver("receiver-a", "receiver-a-id")
        state_a = await self.controller.get_state()
        self.assertEqual(state_a["cap"], 100)
        self.assertEqual(state_a["late_cap"], 64)
        self.assertFalse(state_a["late"])

    async def test_late_night_requires_a_known_volume(self):
        self.save_settings()
        MemoryDriver.state = Status(volume=None, muted=None, power=False, mode=None)

        result = await self.controller.set_late_night(True)

        self.assertEqual(result, {"ok": False, "error": "Turn the receiver on first"})
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

    async def test_game_start_adopts_an_unbound_profile_for_the_resolved_driver(self):
        self.save_settings(
            {
                "version": 2,
                "receiver": {"driver": "memory", "config": {"host": "receiver"}},
                "cap": 150,
                "late_cap": None,
                "late": {"active": False},
                "games": {
                    "14": {"name": "Legacy profile", "driver": None, "volume": 70}
                },
            }
        )

        result = await self.controller.game_started(14, "Legacy profile")

        self.assertTrue(result["applied"])
        self.assertEqual(MemoryDriver.state.volume, 70)
        self.assertEqual(self.store.load()["games"]["14"]["driver"], "memory")

    async def test_game_start_initialization_rediscovery_applies_profile_to_new_ip(self):
        MemoryDriver.fail_hosts = {"receiver-old"}
        MemoryDriver.state.volume = 40
        MemoryDriver.discovered = [
            Found(
                driver="memory",
                model="Memory Receiver",
                id="stable-id",
                host="receiver-new",
                config={"host": "receiver-new"},
            )
        ]
        self.save_settings(
            {
                "version": 2,
                "receiver": {
                    "driver": "memory",
                    "config": {"host": "receiver-old"},
                    "id": "stable-id",
                },
                "cap": 150,
                "late_cap": None,
                "late": {"active": False},
                "games": {
                    "15": {
                        "name": "Moved receiver profile",
                        "driver": "memory",
                        "volume": 70,
                    }
                },
            }
        )

        result = await self.controller.game_started(15, "Moved receiver profile")

        self.assertTrue(result["applied"], result)
        self.assertEqual(MemoryDriver.discovery_calls, 1)
        self.assertEqual(
            self.store.load()["receiver"]["config"]["host"], "receiver-new"
        )
        self.assertEqual(MemoryDriver.state.volume, 70)

    async def test_failed_game_profile_apply_suppresses_the_next_poll_save(self):
        MemoryDriver.fail_hosts = {"receiver"}
        MemoryDriver.state.volume = 40
        self.save_settings(
            {
                "version": 2,
                "receiver": {
                    "driver": "memory",
                    "config": {"host": "receiver"},
                    "id": "stable-id",
                },
                "cap": 150,
                "late_cap": None,
                "late": {"active": False},
                "games": {
                    "16": {
                        "name": "Unavailable profile",
                        "driver": "memory",
                        "volume": 70,
                    }
                },
            }
        )

        result = await self.controller.game_started(16, "Unavailable profile")
        self.assertFalse(result["applied"])

        MemoryDriver.fail_hosts = set()
        await self.controller.get_state()

        self.assertEqual(self.store.load()["games"]["16"]["volume"], 70)

    async def test_stale_profile_failure_does_not_suppress_new_receiver_game_save(self):
        status_started = asyncio.Event()
        release_status = asyncio.Event()

        class OldReceiver(Driver):
            key = "old"
            label = "Old Receiver"
            capabilities = frozenset({VOLUME})
            scale = VolumeScale(min=0, max=100, unit="%", default_cap=60)

            async def status(self):
                status_started.set()
                await release_status.wait()
                raise RuntimeError("Old receiver failed")

        class NewReceiver(Driver):
            key = "new"
            label = "New Receiver"
            capabilities = frozenset({VOLUME})
            scale = VolumeScale(min=0, max=100, unit="%", default_cap=60)

            async def status(self):
                return Status(volume=40)

            async def set_volume(self, raw):
                return raw

        self.controller.registry = [OldReceiver, NewReceiver]
        self.save_settings(
            {
                "version": 2,
                "receiver": {
                    "driver": "old",
                    "config": {"host": "receiver-a"},
                    "id": "receiver-a-id",
                },
                "cap": None,
                "late_cap": None,
                "late": {"active": False},
                "games": {
                    "101": {
                        "name": "Game A",
                        "driver": "old",
                        "volume": 30,
                    },
                    "202": {
                        "name": "Game B",
                        "driver": "new",
                        "volume": 70,
                    },
                },
            }
        )
        applying_old_profile = asyncio.create_task(
            self.controller.game_started(101, "Game A")
        )
        await status_started.wait()

        await self.controller.configure("new", {"host": "receiver-b"})
        await self.controller.set_current_game(202, "Game B")
        self.clock.value += 7
        release_status.set()
        await applying_old_profile

        state = await self.controller.get_state()

        self.assertTrue(state["ok"], state)
        self.assertEqual(self.store.load()["games"]["202"]["volume"], 40)

    async def test_game_profile_volume_uses_scale_learned_from_initial_status(self):
        class LearningDriver(Driver):
            key = "learning"
            label = "Learning Receiver"
            capabilities = frozenset({VOLUME})
            scale = VolumeScale(
                min=0, max=100, unit="%", zero=0, steps_per_unit=1, default_cap=100
            )
            status_calls = 0
            sent_volumes = []

            async def status(self):
                type(self).status_calls += 1
                type(self).scale = VolumeScale(
                    min=0,
                    max=100,
                    unit="%",
                    zero=0,
                    steps_per_unit=1,
                    default_cap=70,
                )
                return Status(volume=40)

            async def set_volume(self, raw):
                type(self).sent_volumes.append(raw)
                return raw

        LearningDriver.status_calls = 0
        LearningDriver.sent_volumes = []
        self.controller.registry = [LearningDriver]
        self.save_settings(
            {
                "version": 2,
                "receiver": {"driver": "learning", "config": {"host": "receiver"}},
                "cap": None,
                "late_cap": None,
                "late": {"active": False},
                "games": {
                    "81": {
                        "name": "Percent profile",
                        "driver": "learning",
                        "volume": 90,
                    }
                },
            }
        )

        result = await self.controller.game_started(81, "Percent profile")

        self.assertTrue(result["applied"])
        self.assertEqual(LearningDriver.sent_volumes, [70])
        self.assertEqual(LearningDriver.status_calls, 1)

    async def test_game_profile_mode_waits_for_discovered_capabilities(self):
        class LearningDriver(Driver):
            key = "learning"
            label = "Learning Receiver"
            scale = VolumeScale(min=0, max=100, unit="%", default_cap=60)
            status_calls = 0
            sent_modes = []

            def __init__(self, config):
                super().__init__(config)
                self._capabilities = frozenset({VOLUME})

            @property
            def capabilities(self):
                return self._capabilities

            async def status(self):
                type(self).status_calls += 1
                self._capabilities = frozenset({VOLUME, MODE})
                return Status(volume=40, mode="Movie")

            async def set_mode(self, code):
                type(self).sent_modes.append(code)
                return code

        LearningDriver.status_calls = 0
        LearningDriver.sent_modes = []
        self.controller.registry = [LearningDriver]
        self.save_settings(
            {
                "version": 2,
                "receiver": {"driver": "learning", "config": {"host": "receiver"}},
                "cap": None,
                "late_cap": None,
                "late": {"active": False},
                "games": {
                    "82": {
                        "name": "Mode profile",
                        "driver": "learning",
                        "mode": "Movie",
                    }
                },
            }
        )

        result = await self.controller.game_started(82, "Mode profile")

        self.assertTrue(result["applied"])
        self.assertEqual(LearningDriver.sent_modes, ["Movie"])
        self.assertEqual(LearningDriver.status_calls, 1)

    async def test_game_profile_reports_not_applied_when_no_action_is_supported(self):
        class NoModeDriver(Driver):
            key = "no_mode"
            label = "Volume Receiver"
            capabilities = frozenset({VOLUME})
            scale = VolumeScale(min=0, max=100, unit="%", default_cap=60)

            async def status(self):
                return Status(volume=40)

        self.controller.registry = [NoModeDriver]
        self.save_settings(
            {
                "version": 2,
                "receiver": {"driver": "no_mode", "config": {"host": "receiver"}},
                "cap": None,
                "late_cap": None,
                "late": {"active": False},
                "games": {
                    "83": {
                        "name": "Unsupported mode profile",
                        "driver": "no_mode",
                        "mode": "Direct",
                    }
                },
            }
        )

        result = await self.controller.game_started(
            83, "Unsupported mode profile"
        )

        self.assertEqual(result, {"applied": False})

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

    async def test_unresolved_driver_rediscovery_runs_on_initial_state_lookup(self):
        class UnresolvedMemoryDriver(MemoryDriver):
            @classmethod
            async def probe(cls, config):
                return None

            @classmethod
            async def discover(cls, timeout):
                cls.discovery_calls += 1
                return list(cls.discovered)

        self.controller.registry = [UnresolvedMemoryDriver]
        self.save_settings(
            {
                "version": 2,
                "receiver": {
                    "driver": None,
                    "config": {"host": "old"},
                    "id": "stable-id",
                },
                "cap": None,
                "late_cap": None,
                "late": {"active": False},
                "games": {},
            }
        )
        UnresolvedMemoryDriver.fail_hosts = {"old"}
        UnresolvedMemoryDriver.discovered = [
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
        self.assertEqual(UnresolvedMemoryDriver.discovery_calls, 1)

    async def test_refused_and_unsupported_commands_do_not_trigger_rediscovery(self):
        class NonConnectivityMemoryDriver(MemoryDriver):
            error_to_raise = None

            async def set_mode(self, code):
                raise self.error_to_raise

        self.controller.registry = [NonConnectivityMemoryDriver]
        self.save_settings()
        for error in (Refused("mode unavailable"), Unsupported("not supported")):
            with self.subTest(error=type(error).__name__):
                NonConnectivityMemoryDriver.error_to_raise = error
                result = await self.controller.set_mode("80")
                self.assertFalse(result["ok"])
                self.assertEqual(NonConnectivityMemoryDriver.discovery_calls, 0)

    async def test_mode_codes_are_opaque_to_the_controller(self):
        class OpaqueModeDriver(MemoryDriver):
            async def set_mode(self, code):
                self.actions.append(("mode", code))
                return code

        self.controller.registry = [OpaqueModeDriver]
        self.save_settings()

        result = await self.controller.set_mode("  stereoX  ")

        self.assertEqual(result["code"], "stereoX")
        self.assertEqual(OpaqueModeDriver.actions[-1], ("mode", "stereoX"))

    async def test_setters_return_observed_values_and_flag_unconfirmed_requests(self):
        class ObservedDriver(Driver):
            key = "observed"
            label = "Observed Receiver"
            capabilities = frozenset({VOLUME, MUTE, POWER, MODE})
            scale = VolumeScale(min=0, max=100, unit="%", default_cap=100)

            async def status(self):
                return Status(volume=40, muted=False, power=True, mode="00")

            async def set_volume(self, raw):
                return 40 if raw == 70 else raw

            async def set_mute(self, muted):
                return False

            async def set_power(self, on):
                return True

            async def set_mode(self, code):
                return "00"

        self.controller.registry = [ObservedDriver]
        self.save_settings(
            {
                "version": 2,
                "receiver": {
                    "driver": "observed",
                    "config": {"host": "receiver"},
                },
                "cap": None,
                "late_cap": None,
                "late": {"active": False},
                "games": {},
            }
        )

        self.assertEqual(
            await self.controller.set_volume(70),
            {"ok": True, "volume": 40, "confirmed": False},
        )
        self.assertEqual(
            await self.controller.set_mute(True),
            {"ok": True, "muted": False, "confirmed": False},
        )
        self.assertEqual(
            await self.controller.set_power(False),
            {"ok": True, "power": True, "confirmed": False},
        )
        self.assertEqual(
            await self.controller.set_mode("11"),
            {
                "ok": True,
                "mode": "Mode 00",
                "code": "00",
                "confirmed": False,
            },
        )
        self.assertEqual(
            await self.controller.set_volume(40), {"ok": True, "volume": 40}
        )
        await self.controller.set_max(30)
        self.assertEqual(
            await self.controller.set_volume(70),
            {"ok": True, "volume": 30, "confirmed": False},
        )

    async def test_auto_probe_on_manual_host_replaces_old_id(self):
        self.save_settings()
        data = self.store.load()
        data["receiver"]["id"] = "old-id"
        self.store.save(data)
        result = await self.controller.configure("auto", {"host": "manual-host"})
        self.assertTrue(result["ok"])
        self.assertEqual(MemoryDriver.probe_calls[-1]["host"], "manual-host")
        settings = self.store.load()
        self.assertEqual(settings["receiver"]["id"], "NEW-ID")
        self.assertEqual(settings["receiver"]["driver"], "memory")

    async def test_driver_added_port_default_does_not_replace_validated_instance(self):
        class LocalTelnetDriver(PioneerTelnetDriver):
            async def status(self):
                return Status(volume=40, muted=False, power=True)

        self.controller.registry = [LocalTelnetDriver]
        result = await self.controller.configure(
            "pioneer_telnet", {"host": "receiver"}
        )
        self.assertTrue(result["ok"])

        state = await self.controller.get_state()

        self.assertTrue(state["ok"], state)
        self.assertEqual(state["driver"], "pioneer_telnet")
        self.assertEqual(state["volume"], 40)
        self.assertEqual(self.store.load()["receiver"]["config"]["port"], 23)

    async def test_implicit_driver_ports_share_normalized_receiver_identity(self):
        for driver_class in (
            EiscpDriver,
            DenonDriver,
            YamahaDriver,
            PioneerTelnetDriver,
        ):
            with self.subTest(driver=driver_class.key):
                self.controller.registry = [driver_class]
                self.controller._active_driver = None
                self.save_settings(
                    {
                        "version": 2,
                        "receiver": {
                            "driver": driver_class.key,
                            "config": {"host": "receiver"},
                        },
                        "cap": None,
                        "late_cap": None,
                        "late": {"active": False},
                        "games": {},
                    }
                )
                generation = self.controller._receiver_generation

                driver = await self.controller._get_driver()

                self.assertIn("port", self.store.load()["receiver"]["config"])
                self.assertEqual(self.controller._receiver_generation, generation)
                legacy = self.store.load()
                legacy["receiver"]["config"] = {"host": "receiver"}
                self.store.save(legacy)
                self.assertIs(await self.controller._get_driver(), driver)
                self.assertEqual(self.controller._receiver_generation, generation)

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

    async def test_configure_keeps_omitted_saved_fields_for_same_driver(self):
        self.save_settings()
        data = self.store.load()
        data["receiver"]["config"].update(
            {"host": "old-host", "token": "saved-secret", "port": 1234}
        )
        self.store.save(data)

        result = await self.controller.configure("memory", {"host": "new-host"})

        self.assertTrue(result["ok"])
        config = self.store.load()["receiver"]["config"]
        self.assertEqual(config, {"host": "new-host", "token": "saved-secret", "port": 1234})

    async def test_homeassistant_config_uses_driver_fields_and_refreshes_full_config(self):
        first = FakeHomeAssistant()
        first_port = first.start()
        second = FakeHomeAssistant()
        second.attributes["volume_level"] = 0.73
        second_port = second.start()
        controller = Controller(self.store, registry=[HomeAssistantDriver])
        config = {
            "url": f"http://127.0.0.1:{first_port}",
            "token": TOKEN,
            "entity_id": ENTITY_ID,
        }
        try:
            missing = await controller.configure(
                "homeassistant",
                {"url": config["url"], "entity_id": ENTITY_ID},
            )
            self.assertFalse(missing["ok"])
            self.assertIn("Long-lived access token", missing["error"])
            missing_auto_host = await controller.configure("auto", {})
            self.assertEqual(
                missing_auto_host,
                {"ok": False, "error": "Enter the receiver IP address"},
            )

            result = await controller.configure("homeassistant", config)
            self.assertTrue(result["ok"])
            state = await controller.get_state()
            self.assertTrue(state["ok"])
            self.assertEqual(state["volume"], 42)

            result = await controller.configure(
                "homeassistant",
                {**config, "token": ""},
            )
            self.assertTrue(result["ok"])
            state = await controller.get_state()
            self.assertTrue(state["ok"])
            self.assertEqual(self.store.load()["receiver"]["config"]["token"], TOKEN)

            data = self.store.load()
            data["receiver"]["config"]["url"] = f"http://127.0.0.1:{second_port}"
            self.store.save(data)
            state = await controller.get_state()
            self.assertTrue(state["ok"])
            self.assertEqual(state["volume"], 73)
        finally:
            await asyncio.to_thread(first.close)
            await asyncio.to_thread(second.close)

    async def test_in_flight_command_does_not_overwrite_new_receiver_selection(self):
        started = asyncio.Event()
        release = asyncio.Event()

        class BlockingMemoryDriver(MemoryDriver):
            @classmethod
            async def discover(cls, timeout):
                return list(cls.discovered)

            async def set_mute(self, muted):
                started.set()
                await release.wait()
                return await super().set_mute(muted)

        self.controller.registry = [BlockingMemoryDriver]
        self.save_settings()
        command = asyncio.create_task(self.controller.set_mute(True))
        await started.wait()

        BlockingMemoryDriver.discovered = [
            Found(
                driver="memory",
                model="Memory Receiver",
                id="new-id",
                host="receiver-b",
                config={"host": "receiver-b"},
            )
        ]
        await self.controller.discover()
        await self.controller.select_receiver("receiver-b", "new-id")
        release.set()

        result = await command
        self.assertFalse(result["ok"])
        self.assertIn("Receiver changed", result["error"])
        receiver = self.store.load()["receiver"]
        self.assertEqual(receiver["config"]["host"], "receiver-b")
        self.assertEqual(receiver["id"], "NEW-ID")

    async def test_stale_status_cannot_update_new_receivers_game_profile(self):
        started = asyncio.Event()
        release = asyncio.Event()

        class OldDriver(Driver):
            key = "old"
            label = "Old Receiver"
            capabilities = frozenset({VOLUME})
            scale = VolumeScale(min=0, max=185, unit="dB", default_cap=100)

            async def status(self):
                started.set()
                await release.wait()
                return Status(volume=100)

        class NewDriver(Driver):
            key = "new"
            label = "New Receiver"
            capabilities = frozenset({VOLUME})
            scale = VolumeScale(min=0, max=100, unit="%", default_cap=60)

            async def status(self):
                return Status(volume=15)

        self.controller.registry = [OldDriver, NewDriver]
        self.save_settings(
            {
                "version": 2,
                "receiver": {"driver": "old", "config": {"host": "receiver-a"}},
                "cap": None,
                "late_cap": None,
                "late": {"active": False},
                "games": {
                    "91": {
                        "name": "New receiver profile",
                        "driver": "new",
                        "volume": 15,
                    }
                },
            }
        )
        await self.controller.set_current_game(91, "New receiver profile")
        lookup = asyncio.create_task(self.controller.get_state())
        await started.wait()

        self.controller._last_found = [
            Found(
                driver="new",
                model="New Receiver",
                id="new-id",
                host="receiver-b",
                config={"host": "receiver-b"},
            )
        ]
        await self.controller.select_receiver("receiver-b", "new-id")
        release.set()
        await lookup

        self.assertEqual(self.store.load()["games"]["91"]["volume"], 15)

    async def test_stale_auto_probe_cannot_replace_a_new_receiver_selection(self):
        started = asyncio.Event()
        release = asyncio.Event()

        class ProbeDriver(Driver):
            key = "probe"
            label = "Probe Receiver"
            capabilities = frozenset({VOLUME})
            scale = VolumeScale(min=0, max=100, unit="%", default_cap=60)

            async def status(self):
                return Status(volume=10)

            @classmethod
            async def probe(cls, config):
                started.set()
                await release.wait()
                return Found(
                    driver=cls.key,
                    model="Probe Receiver",
                    id="receiver-a-id",
                    host=config["host"],
                    config=dict(config),
                )

        self.controller.registry = [ProbeDriver]
        self.save_settings()
        self.controller._last_found = [
            Found(
                driver="probe",
                model="Probe Receiver",
                id="receiver-b-id",
                host="receiver-b",
                config={"host": "receiver-b"},
            )
        ]
        probing = asyncio.create_task(
            self.controller.configure("auto", {"host": "receiver-a"})
        )
        await started.wait()

        await self.controller.select_receiver("receiver-b", "receiver-b-id")
        release.set()
        await probing

        receiver = self.store.load()["receiver"]
        self.assertEqual(receiver["config"]["host"], "receiver-b")
        self.assertEqual(receiver["id"], "RECEIVER-B-ID")

    async def test_auto_configure_reuses_same_generation_concurrent_resolution(self):
        configure_probe_started = asyncio.Event()
        release_configure_probe = asyncio.Event()

        class ConcurrentProbeDriver(Driver):
            key = "concurrent_probe"
            label = "Concurrent Probe"
            capabilities = frozenset({VOLUME, MUTE, POWER, MODE})
            scale = VolumeScale(min=0, max=100, unit="%", default_cap=60)
            probe_calls = 0

            async def status(self):
                return Status(volume=40, muted=False, power=True, mode="00")

            async def set_volume(self, raw):
                return raw

            async def set_mute(self, muted):
                return muted

            async def set_power(self, on):
                return on

            async def set_mode(self, code):
                return code

            @classmethod
            async def probe(cls, config):
                cls.probe_calls += 1
                if cls.probe_calls == 1:
                    configure_probe_started.set()
                    await release_configure_probe.wait()
                return Found(
                    driver=cls.key,
                    model=cls.label,
                    id="same-receiver",
                    host=config["host"],
                    config=dict(config),
                )

        ConcurrentProbeDriver.probe_calls = 0
        self.controller.registry = [ConcurrentProbeDriver]
        self.save_settings(
            {
                "version": 2,
                "receiver": {"driver": None, "config": {}},
                "cap": None,
                "late_cap": None,
                "late": {"active": False},
                "games": {},
            }
        )
        configuring = asyncio.create_task(
            self.controller.configure("auto", {"host": "receiver"})
        )
        await configure_probe_started.wait()

        state = await self.controller.get_state()
        release_configure_probe.set()
        result = await configuring

        self.assertTrue(state["ok"], state)
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.store.load()["receiver"]["driver"], "concurrent_probe")

    async def test_delayed_profile_mode_does_not_run_on_a_new_receiver(self):
        delay_started = asyncio.Event()
        release_delay = asyncio.Event()
        new_receiver_mode = asyncio.Event()

        class ProfileDriver(Driver):
            key = "profile"
            label = "Profile Receiver"
            capabilities = frozenset({MODE})
            scale = VolumeScale(min=0, max=100, unit="%", default_cap=60)

            async def status(self):
                return Status(mode="Other")

            async def set_mode(self, code):
                return code

        class NewReceiverDriver(Driver):
            key = "denon_fake"
            label = "Denon Receiver"
            capabilities = frozenset({MODE})
            scale = VolumeScale(min=0, max=100, unit="%", default_cap=60)

            async def status(self):
                return Status(mode="Stereo")

            async def set_mode(self, code):
                new_receiver_mode.set()
                return code

        async def hold_reapply(_seconds):
            delay_started.set()
            await release_delay.wait()

        self.controller.registry = [ProfileDriver, NewReceiverDriver]
        self.controller.sleep = hold_reapply
        self.save_settings(
            {
                "version": 2,
                "receiver": {"driver": "profile", "config": {"host": "receiver-a"}},
                "cap": None,
                "late_cap": None,
                "late": {"active": False},
                "games": {
                    "92": {
                        "name": "Mode profile",
                        "driver": "profile",
                        "mode": "Direct",
                    }
                },
            }
        )

        result = await self.controller.game_started(92, "Mode profile")
        self.assertTrue(result["applied"])
        await delay_started.wait()
        self.controller._last_found = [
            Found(
                driver="denon_fake",
                model="Denon Receiver",
                id="denon-id",
                host="receiver-b",
                config={"host": "receiver-b"},
            )
        ]
        await self.controller.select_receiver("receiver-b", "denon-id")
        release_delay.set()
        await asyncio.sleep(0.02)

        self.assertFalse(new_receiver_mode.is_set())

    async def test_command_does_not_save_unchanged_driver_config(self):
        self.save_settings()
        original_save = self.store.save
        writes = []

        def track_save(data):
            writes.append(data)
            original_save(data)

        self.store.save = track_save
        await self.controller.set_mute(True)

        self.assertEqual(writes, [])

    async def test_list_games_includes_profile_driver(self):
        self.save_settings()
        data = self.store.load()
        data["games"] = {
            "42": {
                "name": "Example",
                "driver": "memory",
                "volume": 100,
                "mode": None,
            }
        }
        self.store.save(data)

        profiles = await self.controller.list_games()

        self.assertEqual(profiles[0]["driver"], "memory")

    async def test_connection_summary_supports_home_assistant_without_exposing_token(self):
        controller = Controller(self.store, registry=[HomeAssistantDriver])
        self.store.save(
            {
                "version": 2,
                "receiver": {
                    "driver": "homeassistant",
                    "config": {
                        "url": "http://ha.local:8123",
                        "token": "never-return-this",
                        "entity_id": ENTITY_ID,
                    },
                },
                "games": {},
            }
        )

        connection = await controller.get_connection()

        self.assertEqual(
            connection,
            {
                "configured": True,
                "driver": "homeassistant",
                "label": "Home Assistant",
                "summary": ENTITY_ID,
                "fields": {
                    "url": "http://ha.local:8123",
                    "entity_id": ENTITY_ID,
                },
            },
        )


if __name__ == "__main__":
    unittest.main()
