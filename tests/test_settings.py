from __future__ import annotations

import json
import os
import tempfile
import unittest

from tests import PY_MODULES  # noqa: F401
from avr_control.settings import SettingsStore, load_with_legacy, migrate


class SettingsTests(unittest.TestCase):
    def test_migrates_flat_schema_and_preserves_receiver_and_profiles(self):
        migrated = migrate(
            {
                "host": "192.0.2.1",
                "port": 60128,
                "proto": "eiscp",
                "id": "ABC",
                "max": 100,
                "late_max": 64,
                "late": {"active": True, "prev_volume": 90},
                "games": {"10": {"name": "Game", "volume": 80, "mode": "00"}},
            }
        )
        self.assertEqual(migrated["version"], 2)
        self.assertEqual(
            migrated["receiver"],
            {
                "driver": "eiscp",
                "config": {"host": "192.0.2.1", "port": 60128},
                "id": "ABC",
            },
        )
        self.assertEqual(migrated["cap"], 100)
        self.assertEqual(migrated["late_cap"], 64)
        self.assertEqual(migrated["late"], {"active": True, "prev_volume": 90})
        self.assertEqual(
            migrated["games"]["10"],
            {"name": "Game", "driver": "eiscp", "volume": 80, "mode": "00"},
        )

    def test_telnet_and_missing_proto_migration(self):
        telnet = migrate(
            {
                "host": "a",
                "proto": "telnet",
                "games": {"1": {"name": "x", "mode": "00", "volume": 50}},
            }
        )
        self.assertEqual(telnet["receiver"]["driver"], "pioneer_telnet")
        self.assertEqual(telnet["games"]["1"]["driver"], "pioneer_telnet")
        old_no_proto = migrate(
            {"host": "a", "games": {"2": {"name": "y", "mode": "11"}}}
        )
        self.assertIsNone(old_no_proto["receiver"]["driver"])
        self.assertIsNone(old_no_proto["games"]["2"]["driver"])

    def test_store_atomic_roundtrip_and_legacy_folder_adoption(self):
        with tempfile.TemporaryDirectory() as root:
            current = os.path.join(root, "avr-control", "settings.json")
            legacy = os.path.join(root, "pioneer-volume", "settings.json")
            os.makedirs(os.path.dirname(legacy))
            with open(legacy, "w", encoding="utf-8") as file:
                json.dump({"host": "192.0.2.9", "proto": "telnet", "max": 140}, file)
            settings = load_with_legacy(current, [legacy])
            self.assertEqual(settings["receiver"]["driver"], "pioneer_telnet")
            self.assertTrue(os.path.isfile(current))
            loaded = SettingsStore(current).load()
            self.assertEqual(loaded, settings)
            loaded["cap"] = 121
            SettingsStore(current).save(loaded)
            self.assertEqual(SettingsStore(current).load()["cap"], 121)


if __name__ == "__main__":
    unittest.main()
