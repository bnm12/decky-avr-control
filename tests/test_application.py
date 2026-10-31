from __future__ import annotations

import asyncio
import unittest

from tests import PY_MODULES  # noqa: F401
from avr_control.application import (
    ConfigureReceiver,
    ControlApplication,
    ForgetGame,
    GameStarted,
    GameStopped,
    RememberForGame,
    SelectReceiver,
    SetCaps,
    SetCurrentGame,
    SetLateNight,
    SetMode,
    SetMute,
    SetPower,
    SetVolume,
)
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
from avr_control.drivers.heos import HeosDriver
from avr_control.drivers.homeassistant import HomeAssistantDriver
from avr_control.drivers.pioneer_telnet import PioneerTelnetDriver
from avr_control.drivers.yamaha import YamahaDriver
from avr_control.settings import MemorySettingsStore, SettingsRepository
from avr_control.volume import VolumePolicy
from tests.test_heos import FakeHeos
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

    @classmethod
    def mode_label(cls, code):
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


class ApplicationClient:
    """Translate legacy scenario fixtures into calls across the new interface."""

    def __init__(self, application, repository):
        self.application = application
        self.repository = repository

    @property
    def registry(self):
        return self.application.registry

    @registry.setter
    def registry(self, value):
        self.application.registry = value

    @property
    def clock(self):
        return self.application.clock

    @clock.setter
    def clock(self, value):
        self.application.clock = value

    @property
    def sleep(self):
        return self.application.sleep

    @sleep.setter
    def sleep(self, value):
        self.application.sleep = value

    async def _scale(self):
        if self.application._active_driver is not None:
            return self.application._active_driver.scale
        data = self.repository.load()
        driver_class = next(
            (item for item in self.application.registry if item.key == data.receiver.driver),
            None,
        )
        return driver_class(dict(data.receiver.config)).scale

    async def _dispatch(self, intent):
        return await self.application.dispatch(intent)

    async def get_state(self):
        snapshot = await self.application.snapshot()
        if snapshot.get("error"):
            return {"ok": False, "error": snapshot["error"]}
        scale = await self._scale()
        caps = snapshot["caps"]
        current = snapshot["modes"]["current"]
        game = snapshot["game"]
        return {
            "ok": True,
            "volume": (
                snapshot["receiver"]["volume"]["raw"]
                if snapshot["receiver"]["volume"]
                else None
            ),
            "muted": snapshot["receiver"]["muted"],
            "power": snapshot["receiver"]["power"],
            "max": VolumePolicy.to_raw(caps["effective_max_display"], scale),
            "cap": VolumePolicy.to_raw(caps["cap_display"], scale),
            "late_cap": VolumePolicy.to_raw(caps["late_cap_display"], scale),
            "late": caps["late_night"],
            "mode": current["label"] if current else None,
            "mode_code": current["code"] if current else None,
            "modes": [
                {"code": item["code"], "name": item["label"]}
                for item in snapshot["modes"]["selectable"]
            ],
            "game": (
                {
                    "appid": game["appid"],
                    "name": game["name"],
                    "volume": game["remembers_volume"],
                    "mode": game["remembers_mode"],
                }
                if game
                else None
            ),
            "driver": snapshot["connection"]["driver"],
            "driver_label": snapshot["connection"]["label"],
            "scale": scale.as_dict(),
            "capabilities": snapshot["capabilities"],
        }

    async def get_connection(self):
        return (await self.application.snapshot())["connection"]

    async def get_host(self):
        return str(self.repository.load().receiver.config.get("host", "")).strip()

    async def configure(self, driver_key, config):
        result = await self._dispatch(ConfigureReceiver(driver_key, config))
        return {"ok": result["ok"], **({"error": result["error"]} if result.get("error") else {})}

    async def set_host(self, host):
        return await self.configure("auto", {"host": host})

    async def discover(self):
        return await self.application.discover()

    async def select_receiver(self, ip, ident):
        await self.application.discover()
        return await self._dispatch(SelectReceiver(ident))

    async def set_volume(self, raw):
        scale = await self._scale()
        result = await self._dispatch(
            SetVolume(VolumePolicy.to_display(raw, scale))
        )
        value = result.get("snapshot", {}).get("receiver", {}).get("volume")
        response = {"ok": result["ok"]}
        if value is not None:
            response["volume"] = value["raw"]
        if result.get("error"):
            response["error"] = result["error"]
        if result.get("confirmed") is not None:
            response["confirmed"] = result["confirmed"]
        return response

    async def set_mute(self, muted):
        result = await self._dispatch(SetMute(muted))
        response = {"ok": result["ok"]}
        value = result.get("snapshot", {}).get("receiver", {}).get("muted")
        if value is not None:
            response["muted"] = value
        if result.get("error"):
            response["error"] = result["error"]
        if result.get("confirmed") is not None:
            response["confirmed"] = result["confirmed"]
        return response

    async def set_power(self, on):
        result = await self._dispatch(SetPower(on))
        response = {"ok": result["ok"]}
        value = result.get("snapshot", {}).get("receiver", {}).get("power")
        if value is not None:
            response["power"] = value
        if result.get("error"):
            response["error"] = result["error"]
        if result.get("confirmed") is not None:
            response["confirmed"] = result["confirmed"]
        return response

    async def _set_caps(self, cap_raw, late_raw):
        scale = await self._scale()
        result = await self._dispatch(
            SetCaps(
                VolumePolicy.to_display(cap_raw, scale),
                VolumePolicy.to_display(late_raw, scale),
            )
        )
        return {"ok": result["ok"], **({"error": result["error"]} if result.get("error") else {})}

    async def set_max(self, raw):
        data = self.repository.load()
        scale = await self._scale()
        current_late = data.late.cap
        if current_late is None:
            current_late = VolumePolicy.default_late_cap(scale)
        return await self._set_caps(raw, current_late)

    async def set_late_max(self, raw):
        data = self.repository.load()
        scale = await self._scale()
        current_cap = data.cap.cap
        if current_cap is None:
            current_cap = VolumePolicy.default_cap(scale)
        return await self._set_caps(current_cap, raw)

    async def set_late_night(self, on):
        result = await self._dispatch(SetLateNight(on))
        return {"ok": result["ok"], **({"error": result["error"]} if result.get("error") else {})}

    async def set_mode(self, code):
        result = await self._dispatch(SetMode(code))
        response = {"ok": result["ok"]}
        current = result.get("snapshot", {}).get("modes", {}).get("current")
        if current:
            response.update({"mode": current["label"], "code": current["code"]})
        if result.get("error"):
            response["error"] = result["error"]
        if result.get("confirmed") is not None:
            response["confirmed"] = result["confirmed"]
        return response

    async def set_current_game(self, appid, name):
        return await self._dispatch(SetCurrentGame(appid, name))

    async def game_started(self, appid, name):
        result = await self._dispatch(GameStarted(appid, name))
        if not result["ok"]:
            return {"applied": False, **({"error": result["error"]} if result.get("error") else {})}
        data = self.repository.load()
        profile = data.games.get(str(appid))
        snapshot = result["snapshot"]
        driver = snapshot["connection"]["driver"]
        caps = snapshot["capabilities"]
        applied = bool(
            profile
            and profile.driver == driver
            and (
                (profile.volume is not None and "volume" in caps)
                or (profile.mode is not None and "mode" in caps)
            )
        )
        return {"applied": applied}

    async def game_stopped(self, appid):
        return await self._dispatch(GameStopped(appid))

    async def set_game_remember(self, what, enabled):
        result = await self._dispatch(RememberForGame(what, enabled))
        return {"ok": result["ok"], **({"error": result["error"]} if result.get("error") else {})}

    async def list_games(self):
        return (await self.application.snapshot())["profiles"]

    async def forget_game(self, appid):
        return await self._dispatch(ForgetGame(appid))


class ApplicationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = MemorySettingsStore()
        self.repository = SettingsRepository(self.store)
        self.clock = FakeClock()
        self.reset_driver()
        self.application = ControlApplication(
            self.repository,
            registry=[MemoryDriver],
            clock=self.clock,
            sleep=self.clock.sleep,
            reapply_delay=4,
            rediscover_interval=30,
        )
        self.client = ApplicationClient(self.application, self.repository)

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
                "version": 3,
                "receiver": {
                    "driver": "memory",
                    "config": {"host": "receiver"},
                    "id": "stable-id",
                    "binding": {"driver": "memory", "id": "STABLE-ID"},
                },
                "cap": None,
                "late_cap": None,
                "late": {"active": False},
                "games": {},
            }
        )

    async def test_snapshot_and_display_intents_cross_the_application_interface(self):
        self.save_settings()

        before = await self.application.snapshot()
        self.assertEqual(before["receiver"]["volume"]["raw"], 100)
        self.assertEqual(before["receiver"]["volume"]["display"], -32)
        self.assertEqual(before["scale"]["step_display"], 0.5)
        self.assertEqual(before["modes"]["current"], {"code": "00", "label": "Stereo"})
        self.assertEqual(
            before["modes"]["selectable"],
            [{"code": "00", "label": "Stereo"}, {"code": "11", "label": "Direct"}],
        )

        outcome = await self.application.dispatch(SetVolume(-32.25))

        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["snapshot"]["receiver"]["volume"]["raw"], 99)
        self.assertEqual(outcome["snapshot"]["receiver"]["volume"]["display"], -32.5)
        caps = outcome["snapshot"]["caps"]
        self.assertEqual(caps["cap_display"], -30)
        self.assertEqual(caps["effective_max_display"], -30)

    async def test_snapshot_keeps_unknown_current_mode_displayable_but_unselectable(self):
        class UnknownModeDriver(MemoryDriver):
            @classmethod
            def mode_label(cls, code):
                return f"Unlisted {code}"

            async def status(self):
                return Status(volume=100, muted=False, power=True, mode="99")

        self.client.registry = [UnknownModeDriver]
        self.save_settings(
            {
                "version": 3,
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

        snapshot = await self.application.snapshot()

        self.assertEqual(
            snapshot["modes"]["current"],
            {"code": "99", "label": "Unlisted 99"},
        )
        self.assertNotIn(
            "99", [item["code"] for item in snapshot["modes"]["selectable"]]
        )

    async def test_cap_clamping_late_night_restore_and_state_contract(self):
        self.save_settings()
        state = await self.client.get_state()
        self.assertTrue(state["ok"])
        self.assertEqual(state["driver"], "memory")
        self.assertEqual(state["driver_label"], "Memory Receiver")
        self.assertEqual(state["scale"]["zero"], 164)
        self.assertIn("power", state["capabilities"])
        self.assertEqual(state["power"], True)
        settings = self.store.load()
        settings["cap"] = 0
        self.store.save(settings)
        self.assertEqual((await self.client.get_state())["cap"], 104)
        self.assertEqual((await self.client.set_volume(180))["volume"], 104)

        await self.client.set_max(150)
        await self.client.set_late_max(70)
        result = await self.client.set_late_night(True)
        self.assertTrue(result["ok"])
        self.assertEqual(MemoryDriver.state.volume, 70)
        self.assertEqual(self.store.load()["late"], {"active": True, "prev_volume": 104})
        await self.client.set_late_night(False)
        self.assertEqual(MemoryDriver.state.volume, 104)
        self.assertEqual(self.store.load()["late"], {"active": False})

    async def test_steps_scale_defaults_are_fractions_of_its_range(self):
        class StepsDriver(Driver):
            key = "steps"
            label = "Steps Receiver"
            capabilities = frozenset({VOLUME})
            scale = VolumeScale(min=10, max=90, unit="steps")

            async def status(self):
                return Status(volume=50)

            async def set_volume(self, raw):
                return raw

        self.client.registry = [StepsDriver]
        self.save_settings(
            {
                "version": 2,
                "receiver": {
                    "driver": "steps",
                    "config": {"host": "receiver"},
                    "id": "steps-id",
                },
                "cap": None,
                "late_cap": None,
                "late": {"active": False},
                "games": {},
            }
        )

        state = await self.client.get_state()

        self.assertEqual(state["scale"]["unit"], "steps")
        self.assertEqual(state["scale"]["default_cap"], 42)
        self.assertEqual(state["cap"], 42)
        self.assertEqual(state["late_cap"], 26)

    async def test_late_night_restore_respects_a_lowered_normal_cap(self):
        self.save_settings()
        await self.client.get_state()
        await self.client.set_max(100)
        await self.client.set_late_max(70)
        await self.client.set_late_night(True)
        await self.client.set_max(60)

        result = await self.client.set_late_night(False)

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

        self.client.registry = [MemoryDriver, PercentDriver]
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

        result = await self.client.configure("percent", {"host": "new-receiver"})
        self.assertTrue(result["ok"])
        state = await self.client.get_state()

        self.assertEqual(state["cap"], 60)
        self.assertEqual(state["late_cap"], 20)
        self.assertFalse(state["late"])
        self.assertEqual((await self.client.set_volume(100))["volume"], 60)

    async def test_resaving_same_receiver_keeps_caps_and_late_night_restore(self):
        self.save_settings()
        await self.client.get_state()
        await self.client.set_max(50)
        await self.client.set_late_max(40)
        await self.client.set_late_night(True)
        self.assertEqual(MemoryDriver.state.volume, 40)

        result = await self.client.configure("memory", {"host": "receiver"})
        settings = self.store.load()

        self.assertTrue(result["ok"])
        self.assertEqual(settings["cap"], 50)
        self.assertEqual(settings["late_cap"], 40)
        self.assertEqual(settings["late"], {"active": True, "prev_volume": 100})

    async def test_driver_failover_metadata_does_not_replace_receiver_binding(self):
        class FailoverDriver(Driver):
            key = "failover"
            label = "Failover Receiver"
            capabilities = frozenset({VOLUME})
            scale = VolumeScale(min=0, max=100, unit="%", default_cap=100)

            async def status(self):
                self.config.update({"transport": "http", "http_port": 8080})
                return Status(volume=55)

        self.client.registry = [FailoverDriver]
        self.save_settings(
            {
                "version": 2,
                "receiver": {
                    "driver": "failover",
                    "config": {"host": "receiver"},
                    "id": None,
                },
                "cap": 80,
                "late_cap": 40,
                "late": {"active": True, "prev_volume": 65},
                "games": {},
            }
        )

        state = await self.client.get_state()
        settings = self.store.load()

        self.assertTrue(state["ok"])
        self.assertEqual(settings["receiver"]["config"]["transport"], "http")
        self.assertEqual(state["cap"], 80)
        self.assertEqual(state["late_cap"], 40)
        self.assertTrue(state["late"])
        self.assertEqual(settings["late"]["prev_volume"], 65)

    async def test_homeassistant_identity_change_replaces_receiver_binding(self):
        class HomeAssistantLikeDriver(Driver):
            key = "ha_like"
            label = "Home Assistant-like Receiver"
            identity_keys = ("url", "entity_id")
            config_fields = ()
            capabilities = frozenset({VOLUME})
            scale = VolumeScale(min=0, max=100, unit="%", default_cap=60)

            async def status(self):
                return Status(volume=50)

        self.client.registry = [HomeAssistantLikeDriver]
        self.save_settings(
            {
                "version": 2,
                "receiver": {
                    "driver": "ha_like",
                    "config": {
                        "url": "http://homeassistant.local:8123",
                        "entity_id": "media_player.living_room",
                    },
                    "id": None,
                },
                "cap": 80,
                "late_cap": 40,
                "late": {"active": True, "prev_volume": 65},
                "games": {},
            }
        )
        await self.client.get_state()

        result = await self.client.configure(
            "ha_like", {"entity_id": "media_player.bedroom"}
        )
        settings = self.store.load()

        self.assertTrue(result["ok"])
        self.assertIsNone(settings["cap"])
        self.assertIsNone(settings["late_cap"])
        self.assertEqual(settings["late"], {"active": False})

    async def test_legacy_config_hash_migrates_without_resetting_receiver_state(self):
        class LegacyDriver(Driver):
            key = "legacy"
            label = "Legacy Receiver"
            config_fields = ()
            capabilities = frozenset({VOLUME})
            scale = VolumeScale(min=0, max=100, unit="%", default_cap=100)

            async def status(self):
                return Status(volume=55)

        self.client.registry = [LegacyDriver]
        legacy_binding = {
            "driver": "legacy",
            "config_hash": "f8fca4025c3fcc1e8f60ecc7035093a33e21656184128c532f94dbd2753b7672",
        }
        self.save_settings(
            {
                "version": 2,
                "receiver": {
                    "driver": "legacy",
                    "config": {
                        "host": "receiver",
                        "transport": "http",
                        "http_port": 8080,
                    },
                    "id": None,
                },
                "cap": 80,
                "cap_receiver": legacy_binding,
                "late_cap": 40,
                "late_cap_receiver": legacy_binding,
                "late": {"active": True, "prev_volume": 65},
                "late_receiver": legacy_binding,
                "games": {},
            }
        )

        state = await self.client.get_state()
        settings = self.store.load()

        self.assertEqual(state["cap"], 80)
        self.assertEqual(state["late_cap"], 40)
        self.assertTrue(state["late"])
        self.assertEqual(settings["late"]["prev_volume"], 65)
        self.assertEqual(
            settings["cap_receiver"],
            {
                "driver": "legacy",
                "config_hash": "97240f857488c3f87ff7c4624cbd843d92532ae32a25dc3b0518a7dacdbc8fc6",
            },
        )
        self.assertEqual(
            settings["late_cap_receiver"], settings["cap_receiver"]
        )
        self.assertEqual(settings["late_receiver"], settings["cap_receiver"])

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

        result = await self.client.configure("auto", {"host": "receiver"})

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
        state = await self.client.get_state()

        self.assertTrue(state["ok"], state)
        self.assertEqual(self.store.load()["receiver"]["config"]["host"], "new-receiver")
        self.assertEqual(state["cap"], VolumePolicy.default_cap(MemoryDriver.scale))

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
        self.client.registry = [ConcurrentProbeDriver]
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
            self.client.configure("auto", {"host": "receiver"})
        )
        await probe_started.wait()

        state = await self.client.get_state()
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

        self.client.registry = [MemoryDriver, PercentDriver]
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
        await self.client.set_max(50)
        await self.client.set_late_max(40)
        await self.client.set_late_night(True)

        MemoryDriver.discovered = [
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
        await self.client.discover()
        await self.client.select_receiver("receiver-b", "receiver-b-id")
        state_b = await self.client.get_state()
        self.assertEqual(state_b["cap"], 60)
        self.assertEqual(state_b["late_cap"], 20)
        self.assertFalse(state_b["late"])

        await self.client.select_receiver("receiver-a", "receiver-a-id")
        state_a = await self.client.get_state()
        self.assertEqual(state_a["cap"], 104)
        self.assertEqual(state_a["late_cap"], 64)
        self.assertFalse(state_a["late"])

    async def test_late_night_requires_a_known_volume(self):
        self.save_settings()
        MemoryDriver.state = Status(volume=None, muted=None, power=False, mode=None)

        result = await self.client.set_late_night(True)

        self.assertEqual(result, {"ok": False, "error": "Turn the receiver on first"})
        self.assertEqual(self.store.load()["late"], {"active": False})

    async def test_per_game_volume_mode_and_combined_opt_in(self):
        self.save_settings()
        await self.client.set_current_game(11, "Volume only")
        self.assertTrue((await self.client.set_game_remember("volume", True))["ok"])
        profile = self.store.load()["games"]["11"]
        self.assertEqual(profile["volume"], 100)
        self.assertNotIn("mode", profile)

        await self.client.set_current_game(12, "Mode only")
        self.assertTrue((await self.client.set_game_remember("mode", True))["ok"])
        profile = self.store.load()["games"]["12"]
        self.assertEqual(profile["mode"], "00")
        self.assertNotIn("volume", profile)

        await self.client.set_current_game(13, "Both")
        await self.client.set_game_remember("volume", True)
        await self.client.set_game_remember("mode", True)
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

        result = await self.client.game_started(14, "Legacy profile")

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

        result = await self.client.game_started(15, "Moved receiver profile")

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

        result = await self.client.game_started(16, "Unavailable profile")
        self.assertFalse(result["applied"])

        MemoryDriver.fail_hosts = set()
        await self.client.get_state()

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

        self.client.registry = [OldReceiver, NewReceiver]
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
            self.client.game_started(101, "Game A")
        )
        await status_started.wait()

        await self.client.configure("new", {"host": "receiver-b"})
        await self.client.set_current_game(202, "Game B")
        self.clock.value += 7
        release_status.set()
        await applying_old_profile

        state = await self.client.get_state()

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
        self.client.registry = [LearningDriver]
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

        result = await self.client.game_started(81, "Percent profile")

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
        self.client.registry = [LearningDriver]
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

        result = await self.client.game_started(82, "Mode profile")

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

        self.client.registry = [NoModeDriver]
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

        result = await self.client.game_started(
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
        await self.client.set_current_game(21, "Remembered")
        MemoryDriver.state.volume = 85
        MemoryDriver.state.mode = "11"
        await self.client.get_state()
        profile = self.store.load()["games"]["21"]
        self.assertEqual((profile["volume"], profile["mode"]), (85, "11"))

        data = self.store.load()
        data["late"] = {"active": True, "prev_volume": 85}
        self.store.save(data)
        MemoryDriver.state.volume = 60
        await self.client.get_state()
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
        await self.client.game_started(22, "Suppressed")
        await asyncio.sleep(0)
        self.assertEqual(MemoryDriver.state.volume, 60)
        MemoryDriver.state.volume = 80
        MemoryDriver.state.mode = "00"
        await self.client.get_state()
        profile = self.store.load()["games"]["22"]
        self.assertEqual((profile["volume"], profile["mode"]), (60, "11"))
        self.clock.value += 6
        await self.client.get_state()
        profile = self.store.load()["games"]["22"]
        self.assertEqual((profile["volume"], profile["mode"]), (80, "00"))

        await self.client.game_stopped(22)
        data = self.store.load()
        data["games"]["22"]["driver"] = "other-driver"
        data["games"]["22"]["volume"] = 40
        data["games"]["22"]["mode"] = "FF"
        self.store.save(data)
        MemoryDriver.actions.clear()
        result = await self.client.game_started(22, "Suppressed")
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
        state = await self.client.get_state()
        self.assertTrue(state["ok"])
        self.assertEqual(self.store.load()["receiver"]["config"]["host"], "new")
        self.assertEqual(MemoryDriver.discovery_calls, 1)

        data = self.store.load()
        data["receiver"]["config"]["host"] = "old"
        self.store.save(data)
        self.clock.value += 5
        state = await self.client.get_state()
        self.assertFalse(state["ok"])
        self.assertEqual(MemoryDriver.discovery_calls, 1)

        self.clock.value += 30
        state = await self.client.get_state()
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

        self.client.registry = [UnresolvedMemoryDriver]
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

        state = await self.client.get_state()

        self.assertTrue(state["ok"])
        self.assertEqual(self.store.load()["receiver"]["config"]["host"], "new")
        self.assertEqual(UnresolvedMemoryDriver.discovery_calls, 1)

    async def test_refused_and_unsupported_commands_do_not_trigger_rediscovery(self):
        class NonConnectivityMemoryDriver(MemoryDriver):
            error_to_raise = None

            async def set_mode(self, code):
                raise self.error_to_raise

        self.client.registry = [NonConnectivityMemoryDriver]
        self.save_settings()
        for error in (Refused("mode unavailable"), Unsupported("not supported")):
            with self.subTest(error=type(error).__name__):
                NonConnectivityMemoryDriver.error_to_raise = error
                result = await self.client.set_mode("80")
                self.assertFalse(result["ok"])
                self.assertEqual(NonConnectivityMemoryDriver.discovery_calls, 0)

    async def test_mode_codes_remain_opaque_to_the_application(self):
        class OpaqueModeDriver(MemoryDriver):
            async def set_mode(self, code):
                self.actions.append(("mode", code))
                return code

        self.client.registry = [OpaqueModeDriver]
        self.save_settings()

        result = await self.client.set_mode("  stereoX  ")

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

        self.client.registry = [ObservedDriver]
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
            await self.client.set_volume(70),
            {"ok": True, "volume": 40, "confirmed": False},
        )
        self.assertEqual(
            await self.client.set_mute(True),
            {"ok": True, "muted": False, "confirmed": False},
        )
        self.assertEqual(
            await self.client.set_power(False),
            {"ok": True, "power": True, "confirmed": False},
        )
        self.assertEqual(
            await self.client.set_mode("11"),
            {
                "ok": True,
                "mode": "Mode 00",
                "code": "00",
                "confirmed": False,
            },
        )
        self.assertEqual(
            await self.client.set_volume(40), {"ok": True, "volume": 40}
        )
        await self.client.set_max(30)
        self.assertEqual(
            await self.client.set_volume(70),
            {"ok": True, "volume": 30, "confirmed": False},
        )

    async def test_auto_probe_on_manual_host_replaces_old_id(self):
        self.save_settings()
        data = self.store.load()
        data["receiver"]["id"] = "old-id"
        self.store.save(data)
        result = await self.client.configure("auto", {"host": "manual-host"})
        self.assertTrue(result["ok"])
        self.assertEqual(MemoryDriver.probe_calls[-1]["host"], "manual-host")
        settings = self.store.load()
        self.assertEqual(settings["receiver"]["id"], "NEW-ID")
        self.assertEqual(settings["receiver"]["driver"], "memory")

    async def test_driver_added_port_default_does_not_replace_validated_instance(self):
        class LocalTelnetDriver(PioneerTelnetDriver):
            async def status(self):
                return Status(volume=40, muted=False, power=True)

        self.client.registry = [LocalTelnetDriver]
        result = await self.client.configure(
            "pioneer_telnet", {"host": "receiver"}
        )
        self.assertTrue(result["ok"])

        state = await self.client.get_state()

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
                self.client.registry = [driver_class]
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
                await self.application.snapshot()

                self.assertIn("port", self.store.load()["receiver"]["config"])
                legacy = self.store.load()
                legacy["receiver"]["config"] = {"host": "receiver"}
                self.store.save(legacy)
                await self.application.snapshot()
                self.assertIn("port", self.store.load()["receiver"]["config"])

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
        found = await self.client.discover()
        self.assertEqual(found[0]["driver"], "memory")
        self.assertEqual(found[0]["label"], "Memory Receiver")
        await self.client.select_receiver("192.0.2.2", "abc")
        self.assertEqual(self.store.load()["receiver"]["driver"], "memory")
        self.assertEqual(self.store.load()["receiver"]["config"]["port"], 23)

    async def test_configure_keeps_omitted_saved_fields_for_same_driver(self):
        self.save_settings()
        data = self.store.load()
        data["receiver"]["config"].update(
            {"host": "old-host", "token": "saved-secret", "port": 1234}
        )
        self.store.save(data)

        result = await self.client.configure("memory", {"host": "new-host"})

        self.assertTrue(result["ok"])
        config = self.store.load()["receiver"]["config"]
        self.assertEqual(config, {"host": "new-host", "token": "saved-secret", "port": 1234})

    async def test_homeassistant_config_uses_driver_fields_and_refreshes_full_config(self):
        first = FakeHomeAssistant()
        first_port = first.start()
        second = FakeHomeAssistant()
        second.attributes["volume_level"] = 0.73
        second_port = second.start()
        client = ApplicationClient(
            ControlApplication(self.repository, registry=[HomeAssistantDriver]),
            self.repository,
        )
        config = {
            "url": f"http://127.0.0.1:{first_port}",
            "token": TOKEN,
            "entity_id": ENTITY_ID,
        }
        try:
            missing = await client.configure(
                "homeassistant",
                {"url": config["url"], "entity_id": ENTITY_ID},
            )
            self.assertFalse(missing["ok"])
            self.assertIn("Long-lived access token", missing["error"])
            missing_auto_host = await client.configure("auto", {})
            self.assertEqual(
                missing_auto_host,
                {"ok": False, "error": "Enter the receiver IP address"},
            )

            result = await client.configure("homeassistant", config)
            self.assertTrue(result["ok"])
            state = await client.get_state()
            self.assertTrue(state["ok"])
            self.assertEqual(state["volume"], 42)

            result = await client.configure(
                "homeassistant",
                {**config, "token": ""},
            )
            self.assertTrue(result["ok"])
            state = await client.get_state()
            self.assertTrue(state["ok"])
            self.assertEqual(self.store.load()["receiver"]["config"]["token"], TOKEN)

            data = self.store.load()
            data["receiver"]["config"]["url"] = f"http://127.0.0.1:{second_port}"
            self.store.save(data)
            state = await client.get_state()
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

        self.client.registry = [BlockingMemoryDriver]
        self.save_settings()
        command = asyncio.create_task(self.client.set_mute(True))
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
        await self.client.discover()
        await self.client.select_receiver("receiver-b", "new-id")
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

        self.client.registry = [OldDriver, NewDriver]
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
        await self.client.set_current_game(91, "New receiver profile")
        lookup = asyncio.create_task(self.client.get_state())
        await started.wait()

        await self.client.configure("new", {"host": "receiver-b"})
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
            async def discover(cls, timeout):
                return list(cls.discovered)

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

        self.client.registry = [ProbeDriver]
        self.save_settings()
        ProbeDriver.discovered = [
            Found(
                driver="probe",
                model="Probe Receiver",
                id="receiver-b-id",
                host="receiver-b",
                config={"host": "receiver-b"},
            )
        ]
        await self.client.discover()
        probing = asyncio.create_task(
            self.client.configure("auto", {"host": "receiver-a"})
        )
        await started.wait()

        await self.client.select_receiver("receiver-b", "receiver-b-id")
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
        self.client.registry = [ConcurrentProbeDriver]
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
            self.client.configure("auto", {"host": "receiver"})
        )
        await configure_probe_started.wait()

        state = await self.client.get_state()
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

        self.client.registry = [ProfileDriver, NewReceiverDriver]
        self.client.sleep = hold_reapply
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

        result = await self.client.game_started(92, "Mode profile")
        self.assertTrue(result["applied"])
        await delay_started.wait()
        await self.client.configure(
            "denon_fake", {"host": "receiver-b"}
        )
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
        await self.client.set_mute(True)

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

        profiles = await self.client.list_games()

        self.assertEqual(profiles[0]["driver"], "memory")

    async def test_connection_summary_supports_home_assistant_without_exposing_token(self):
        client = ApplicationClient(
            ControlApplication(self.repository, registry=[HomeAssistantDriver]),
            self.repository,
        )
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

        connection = await client.get_connection()

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
    async def test_heos_player_change_drops_late_night_and_caps_of_previous_player(self):
        fake = FakeHeos()
        port = await fake.start()
        self.addAsyncCleanup(fake.close)
        for player in fake.players:
            player.pop("ip")
        self.client.registry = [HeosDriver]
        heos = {"host": "127.0.0.1", "port": port}

        await self.client.configure("heos", {**heos, "player": "Living Room"})
        await self.client.set_max(100)
        await self.client.set_volume(85)
        await self.client.set_late_max(40)
        self.assertTrue((await self.client.set_late_night(True))["ok"])
        self.assertEqual(fake.players[0]["level"], 40)

        result = await self.client.configure("heos", {**heos, "player": "Kitchen"})
        state = await self.client.get_state()
        self.assertTrue(result["ok"])
        self.assertFalse(state["late"])
        self.assertEqual(state["cap"], 60)

        self.assertTrue((await self.client.set_late_night(False))["ok"])
        self.assertEqual(fake.players[1]["level"], 58)
        self.assertEqual(fake.players[0]["level"], 40)

    async def test_heos_player_edit_controls_new_player_not_serial_learned_earlier(self):
        fake = FakeHeos()
        port = await fake.start()
        self.addAsyncCleanup(fake.close)
        for player in fake.players:
            player.pop("ip")
        self.client.registry = [HeosDriver]
        heos = {"host": "127.0.0.1", "port": port}
        await self.client.configure("heos", {**heos, "player": "Living Room"})
        settings = self.store.load()
        settings["receiver"]["config"].update(
            {"serial": "HEOS-101", "serial_for": "living room"}
        )
        self.store.save(settings)

        await self.client.configure("heos", {**heos, "player": "Kitchen"})
        result = await self.client.set_volume(50)

        self.assertTrue(result["ok"])
        self.assertEqual(fake.players[1]["level"], 50)
        self.assertEqual(fake.players[0]["level"], 34)


if __name__ == "__main__":
    unittest.main()
