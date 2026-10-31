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
    Settings,
    SettingsRepository,
    SettingsStore,
    migrate,
)


class Logger:
    def __init__(self):
        self.errors = []

    def error(self, message):
        self.errors.append(message)


class SettingsRepositoryTests(unittest.TestCase):
    def _stores(self, root, data=None, legacy_data=None):
        memory = MemorySettingsStore(data, legacy_data=legacy_data)
        path = os.path.join(root, "avr-control", "settings.json")
        legacy_path = os.path.join(root, "pioneer-volume", "settings.json")
        if data is not None:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as file:
                json.dump(data, file)
        file_store = FileSettingsStore(path, legacy_paths=[legacy_path])
        if legacy_data is not None:
            os.makedirs(os.path.dirname(legacy_path), exist_ok=True)
            with open(legacy_path, "w", encoding="utf-8") as file:
                json.dump(legacy_data, file)
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
                    cap=30,
                    cap_binding={"driver": "homeassistant", "id": "RECEIVER-ID"},
                    binding={"driver": "homeassistant", "id": "RECEIVER-ID"},
                    extra={"future_late_option": "kept"},
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

        self._exercise_both(verify)

    def test_v1_migration_is_ordered_current_and_idempotent(self):
        flat = {
            "host": "192.0.2.1",
            "port": 60128,
            "proto": "eiscp",
            "id": "ABC",
            "max": 100,
            "late_max": 64,
            "late": {"active": True, "prev_volume": "90"},
            "games": {"10": {"name": "Game", "volume": "80", "mode": "00"}},
            "future_setting": "preserved",
        }
        migrated_once = migrate(flat)
        self.assertEqual(migrated_once["version"], CURRENT_VERSION)
        self.assertEqual(migrate(migrated_once), migrated_once)
        self.assertEqual(migrated_once["receiver"]["driver"], "eiscp")
        self.assertEqual(migrated_once["receiver"]["config"]["host"], "192.0.2.1")
        self.assertEqual(migrated_once["games"]["10"]["driver"], "eiscp")

        def verify(store, _root):
            store.save(flat)
            loaded = SettingsRepository(store).load()
            self.assertEqual(loaded.version, CURRENT_VERSION)
            self.assertEqual(loaded.receiver.id, "ABC")
            self.assertEqual(loaded.cap.cap, 100)
            self.assertEqual(loaded.late.cap, 64)
            self.assertEqual(loaded.late.prev_volume, 90)
            self.assertEqual(loaded.games["10"].volume, 80)
            self.assertEqual(loaded.extra["future_setting"], "preserved")
            self.assertEqual(SettingsRepository(store).load().to_dict(), loaded.to_dict())

        self._exercise_both(verify)

    def test_legacy_folder_adoption_uses_injected_path_for_both_adapters(self):
        legacy = {"host": "192.0.2.9", "proto": "telnet", "max": 140}

        with tempfile.TemporaryDirectory() as root:
            for name, store in self._stores(root, legacy_data=legacy):
                with self.subTest(adapter=name):
                    settings = SettingsRepository(store).load()
                    self.assertEqual(settings.receiver.driver, "pioneer_telnet")
                    self.assertEqual(settings.cap.cap, 140)
                    if isinstance(store, FileSettingsStore):
                        self.assertTrue(os.path.isfile(store.path))
                        with open(store.path, "r", encoding="utf-8") as file:
                            self.assertEqual(json.load(file)["version"], CURRENT_VERSION)

    def test_failed_migration_save_keeps_normalized_settings_in_memory(self):
        legacy = {
            "host": "192.0.2.7",
            "proto": "eiscp",
            "private_credential": "never-log-this",
        }
        with tempfile.TemporaryDirectory() as root:
            for name, store in self._stores(root, legacy_data=legacy):
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
