from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from tests import PY_MODULES  # noqa: F401
from avr_control.settings import (
    CURRENT_VERSION,
    CapSettings,
    FileSettingsStore,
    GameProfile,
    LateNightSettings,
    MemorySettingsStore,
    ReceiverSettings,
    ScheduleSettings,
    Settings,
    SettingsRepository,
    SettingsStore,
    SleepWakeSettings,
    migrate,
)


class Logger:
    def __init__(self):
        self.errors = []

    def error(self, message):
        self.errors.append(message)


class SettingsRepositoryTests(unittest.TestCase):
    def _stores(self, root, data=None):
        memory = MemorySettingsStore(data)
        path = os.path.join(root, "avr-control", "settings.json")
        if data is not None:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as file:
                json.dump(data, file)
        file_store = FileSettingsStore(path)
        return (("memory", memory), ("filesystem", file_store))

    def _exercise_both(self, callback):
        with tempfile.TemporaryDirectory() as root:
            for name, store in self._stores(root):
                with self.subTest(adapter=name):
                    callback(store, root)

    def test_round_trip_preserves_domain_values_and_masks_secrets(self):
        def verify(store, _root):
            repository = SettingsRepository(
                store, secret_keys={"private_credential"}
            )
            value = Settings(
                receiver=ReceiverSettings(
                    driver="homeassistant",
                    config={
                        "url": "http://receiver",
                        "private_credential": "do-not-print",
                    },
                    id="receiver-id",
                    binding={"driver": "homeassistant", "id": "RECEIVER-ID"},
                    extra={"future_receiver_option": {"kept": True}},
                ),
                cap=CapSettings(
                    cap=42,
                    binding={"driver": "homeassistant", "id": "RECEIVER-ID"},
                ),
                late=LateNightSettings(
                    active=True,
                    prev_volume=61,
                    auto=True,
                    cap=30,
                    cap_binding={"driver": "homeassistant", "id": "RECEIVER-ID"},
                    binding={"driver": "homeassistant", "id": "RECEIVER-ID"},
                    extra={"future_late_option": "kept"},
                ),
                schedule=ScheduleSettings(
                    enabled=True,
                    start="21:30",
                    end="06:45",
                    mode="11",
                    override_window="2026-10-31",
                    last_late_window="2026-10-31",
                    last_mode_window="2026-10-30",
                    extra={"future_schedule_option": "kept"},
                ),
                sleep_wake=SleepWakeSettings(
                    wake_on_resume=True,
                    off_on_suspend=True,
                    extra={"future_sleep_option": "kept"},
                ),
                games={
                    "123": GameProfile(
                        appid=123,
                        name="A game",
                        driver="homeassistant",
                        volume=61,
                        mode="stereo",
                        extra={"future_profile_option": "kept"},
                    )
                },
                extra={"future_top_level": {"kept": True}},
            )
            repository.save(value)
            loaded = repository.load()
            self.assertEqual(loaded.version, CURRENT_VERSION)
            self.assertEqual(loaded.receiver.id, "receiver-id")
            self.assertEqual(
                loaded.receiver.config["private_credential"], "do-not-print"
            )
            self.assertEqual(loaded.cap.cap, 42)
            self.assertEqual(loaded.cap.binding, value.cap.binding)
            self.assertEqual(loaded.late.prev_volume, 61)
            self.assertTrue(loaded.late.active)
            self.assertTrue(loaded.late.auto)
            self.assertEqual(loaded.late.cap, 30)
            self.assertEqual(loaded.late.cap_binding, value.late.cap_binding)
            self.assertEqual(loaded.late.binding, value.late.binding)
            self.assertEqual(loaded.games["123"].volume, 61)
            self.assertEqual(loaded.games["123"].mode, "stereo")
            self.assertEqual(loaded.extra["future_top_level"], {"kept": True})
            self.assertEqual(
                loaded.receiver.extra["future_receiver_option"], {"kept": True}
            )
            self.assertEqual(loaded.late.extra["future_late_option"], "kept")
            self.assertTrue(loaded.schedule.enabled)
            self.assertEqual((loaded.schedule.start, loaded.schedule.end), ("21:30", "06:45"))
            self.assertEqual(loaded.schedule.mode, "11")
            self.assertEqual(loaded.schedule.override_window, "2026-10-31")
            self.assertEqual(loaded.schedule.last_late_window, "2026-10-31")
            self.assertEqual(loaded.schedule.last_mode_window, "2026-10-30")
            self.assertEqual(
                loaded.schedule.extra["future_schedule_option"], "kept"
            )
            self.assertEqual(loaded.sleep_wake.wake_on_resume, True)
            self.assertEqual(loaded.sleep_wake.off_on_suspend, True)
            self.assertEqual(
                loaded.sleep_wake.extra["future_sleep_option"], "kept"
            )
            self.assertEqual(
                loaded.games["123"].extra["future_profile_option"], "kept"
            )
            self.assertNotIn("do-not-print", repr(loaded))
            self.assertNotIn("do-not-print", str(loaded.receiver))

        self._exercise_both(verify)

    def test_malformed_nested_values_are_normalized(self):
        malformed = {
            "version": CURRENT_VERSION,
            "receiver": {"driver": [], "config": "not a config", "id": 12},
            "cap": {"not": "an integer"},
            "late_cap": "not an integer",
            "late": {
                "active": "yes",
                "prev_volume": "bad",
                "future_late_option": "preserved",
            },
            "schedule": {
                "enabled": "yes",
                "start": "25:61",
                "end": "07:00",
                "mode": 9,
                "future_schedule_option": "preserved",
            },
            "sleep_wake": {
                "wake_on_resume": "yes",
                "off_on_suspend": True,
                "future_sleep_option": "preserved",
            },
            "games": {
                "10": {"name": 9, "driver": [], "volume": "bad", "mode": 4},
                "11": {"name": "valid", "driver": "memory", "volume": "81"},
                "not-an-appid": {"volume": 4},
                "12": None,
            },
        }

        def verify(store, _root):
            store.save(malformed)
            settings = SettingsRepository(store).load()
            self.assertEqual(settings.version, CURRENT_VERSION)
            self.assertIsNone(settings.receiver.driver)
            self.assertEqual(settings.receiver.config, {})
            self.assertIsNone(settings.receiver.id)
            self.assertIsNone(settings.cap.cap)
            self.assertIsNone(settings.late.cap)
            self.assertFalse(settings.late.active)
            self.assertIsNone(settings.late.prev_volume)
            self.assertEqual(settings.games["10"].name, "")
            self.assertIsNone(settings.games["10"].volume)
            self.assertIsNone(settings.games["10"].mode)
            self.assertEqual(settings.games["11"].volume, 81)
            self.assertNotIn("not-an-appid", settings.games)
            self.assertEqual(settings.late.extra["future_late_option"], "preserved")
            self.assertFalse(settings.schedule.enabled)
            self.assertEqual(settings.schedule.start, "22:00")
            self.assertEqual(settings.schedule.end, "07:00")
            self.assertIsNone(settings.schedule.mode)
            self.assertEqual(
                settings.schedule.extra["future_schedule_option"], "preserved"
            )
            self.assertFalse(settings.sleep_wake.wake_on_resume)
            self.assertTrue(settings.sleep_wake.off_on_suspend)
            self.assertEqual(
                settings.sleep_wake.extra["future_sleep_option"], "preserved"
            )

        self._exercise_both(verify)

    def test_unversioned_flat_settings_load_as_defaults(self):
        flat = {
            "host": "192.0.2.1",
            "port": 60128,
            "proto": "telnet",
            "id": "ABC",
            "max": 100,
            "late_max": 64,
            "late": {"active": True, "prev_volume": "90"},
            "games": {"10": {"name": "Game", "volume": "80", "mode": "00"}},
            "future_setting": "preserved",
        }
        self.assertEqual(migrate(flat), {})

        with tempfile.TemporaryDirectory() as root:
            for name, store in self._stores(root, data=flat):
                with self.subTest(adapter=name):
                    loaded = SettingsRepository(store).load()
                    self.assertEqual(loaded.version, CURRENT_VERSION)
                    self.assertIsNone(loaded.receiver.driver)
                    self.assertEqual(loaded.receiver.config, {})
                    self.assertIsNone(loaded.receiver.id)
                    self.assertIsNone(loaded.cap.cap)
                    self.assertIsNone(loaded.late.cap)
                    self.assertEqual(loaded.games, {})
                    self.assertFalse(loaded.late.active)
                    self.assertFalse(loaded.schedule.enabled)
                    self.assertFalse(loaded.sleep_wake.wake_on_resume)

    def test_v3_migration_adds_opt_ins_and_preserves_schedule_values(self):
        data = {
            "version": 3,
            "receiver": {"driver": "eiscp", "config": {"host": "receiver"}},
            "late": {"active": False},
            "schedule": {
                "enabled": True,
                "start": "20:15",
                "end": "23:45",
                "mode": "11",
            },
            "sleep_wake": {
                "wake_on_resume": True,
                "off_on_suspend": False,
            },
        }

        migrated = migrate(data)
        settings = SettingsRepository(MemorySettingsStore(data)).load()

        self.assertEqual(migrated["version"], CURRENT_VERSION)
        self.assertEqual(settings.schedule.start, "20:15")
        self.assertEqual(settings.schedule.mode, "11")
        self.assertTrue(settings.sleep_wake.wake_on_resume)
        self.assertFalse(settings.sleep_wake.off_on_suspend)

    def test_failed_migration_save_keeps_normalized_settings_in_memory(self):
        data = {
            "version": 3,
            "receiver": {
                "driver": "eiscp",
                "config": {
                    "host": "192.0.2.7",
                    "private_credential": "never-log-this",
                },
            },
        }
        with tempfile.TemporaryDirectory() as root:
            for name, store in self._stores(root, data=data):
                with self.subTest(adapter=name):
                    logger = Logger()
                    repository = SettingsRepository(
                        store, logger=logger, secret_keys={"private_credential"}
                    )
                    with patch.object(store, "save", side_effect=OSError("disk full")):
                        loaded = repository.load()
                    self.assertEqual(loaded.receiver.config["host"], "192.0.2.7")
                    self.assertEqual(repository.load().receiver.driver, "eiscp")
                    self.assertEqual(len(logger.errors), 1)
                    self.assertNotIn("never-log-this", repr(logger.errors))
                    self.assertNotIn("never-log-this", repr(loaded))

    def test_filesystem_adapter_writes_atomically_and_compatibility_names_work(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "settings.json")
            repository = SettingsRepository(path)
            repository.save(Settings(receiver=ReceiverSettings(driver="eiscp")))
            self.assertEqual(SettingsRepository(SettingsStore(path)).load().receiver.driver, "eiscp")
            self.assertEqual(
                [name for name in os.listdir(root) if name.endswith(".tmp")], []
            )


if __name__ == "__main__":
    unittest.main()
