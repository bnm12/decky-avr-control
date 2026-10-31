from __future__ import annotations

import ast
import asyncio
import importlib.util
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from tests import PY_MODULES  # noqa: F401
from avr_control.settings import FileSettingsStore


class Logger:
    def info(self, _message):
        pass

    def error(self, _message):
        pass


class MainSmokeTests(unittest.TestCase):
    def test_ast_parses_and_main_imports_with_decky_stub(self):
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
        main_path = os.path.join(root, "main.py")
        with open(main_path, "r", encoding="utf-8") as file:
            ast.parse(file.read())

        with tempfile.TemporaryDirectory() as directory:
            settings_dir = os.path.join(directory, "avr-control")
            decky_stub = types.ModuleType("decky")
            decky_stub.DECKY_PLUGIN_SETTINGS_DIR = settings_dir
            decky_stub.logger = Logger()
            emitted = []

            async def emit(event, *args):
                emitted.append((event, args))

            decky_stub.emit = emit
            old_decky = sys.modules.get("decky")
            old_main = sys.modules.pop("avr_control_main_smoke", None)
            sys.modules["decky"] = decky_stub
            try:
                spec = importlib.util.spec_from_file_location(
                    "avr_control_main_smoke", main_path
                )
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = module
                spec.loader.exec_module(module)
                smoke_snapshot = {"revision": 3, "push": True}
                asyncio.run(module._publish_snapshot(smoke_snapshot))
                self.assertEqual(
                    emitted, [("avr_snapshot", (smoke_snapshot,))]
                )
                plugin = module.Plugin()
                public_rpcs = {
                    name
                    for name, value in vars(module.Plugin).items()
                    if not name.startswith("_") and callable(value)
                }
                self.assertEqual(
                    public_rpcs,
                    {"snapshot", "dispatch", "discover", "list_drivers", "setup_options"},
                )
                snapshot = asyncio.run(plugin.snapshot())
                self.assertEqual(
                    snapshot["connection"],
                    {
                        "configured": False,
                        "driver": None,
                        "label": None,
                        "summary": "",
                        "fields": {},
                    },
                )
                self.assertEqual(snapshot["schedule"]["enabled"], False)
                self.assertEqual(snapshot["sleep_wake"]["supports_power"], False)
                self.assertFalse(snapshot["push"])
                panel_opened = asyncio.run(
                    plugin.dispatch({"type": "panel_opened"})
                )
                self.assertTrue(panel_opened["ok"])
                panel_closed = asyncio.run(
                    plugin.dispatch({"type": "panel_closed"})
                )
                self.assertTrue(panel_closed["ok"])
                from avr_control.drivers import REGISTRY

                drivers = asyncio.run(plugin.list_drivers())
                self.assertEqual(
                    [d["key"] for d in drivers], [d.key for d in REGISTRY]
                )
                bad_type = asyncio.run(plugin.dispatch({"type": "future_intent"}))
                self.assertEqual(
                    bad_type,
                    {
                        "ok": False,
                        "error": "Unknown intent type: 'future_intent'",
                    },
                )
                bad_fields = asyncio.run(
                    plugin.dispatch({"type": "set_volume", "display": "loud"})
                )
                self.assertFalse(bad_fields["ok"])
                self.assertIn("Invalid display", bad_fields["error"])
                configured_schedule = asyncio.run(
                    plugin.dispatch(
                        {
                            "type": "set_schedule",
                            "enabled": True,
                            "start": "22:00",
                            "end": "07:00",
                            "mode": None,
                        }
                    )
                )
                self.assertTrue(configured_schedule["ok"])
                self.assertTrue(configured_schedule["snapshot"]["schedule"]["enabled"])
                bad_schedule = asyncio.run(
                    plugin.dispatch(
                        {
                            "type": "set_schedule",
                            "enabled": True,
                            "start": 2200,
                            "end": "07:00",
                            "mode": None,
                        }
                    )
                )
                self.assertFalse(bad_schedule["ok"])
                async def lifecycle():
                    await plugin._main()
                    await plugin._unload()

                asyncio.run(lifecycle())
            finally:
                sys.modules.pop("avr_control_main_smoke", None)
                if old_main is not None:
                    sys.modules["avr_control_main_smoke"] = old_main
                if old_decky is None:
                    sys.modules.pop("decky", None)
                else:
                    sys.modules["decky"] = old_decky

    def test_main_keeps_migrated_settings_in_memory_when_save_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            settings_dir = os.path.join(directory, "avr-control")
            legacy_dir = os.path.join(directory, "pioneer-volume")
            os.makedirs(legacy_dir)
            with open(
                os.path.join(legacy_dir, "settings.json"), "w", encoding="utf-8"
            ) as file:
                file.write('{"host": "192.0.2.7", "proto": "eiscp"}')

            decky_stub = types.ModuleType("decky")
            decky_stub.DECKY_PLUGIN_SETTINGS_DIR = settings_dir
            decky_stub.logger = Logger()
            old_decky = sys.modules.get("decky")
            old_main = sys.modules.pop("avr_control_main_smoke", None)
            sys.modules["decky"] = decky_stub
            try:
                with patch.object(
                    FileSettingsStore, "save", side_effect=OSError("disk full")
                ):
                    spec = importlib.util.spec_from_file_location(
                        "avr_control_main_smoke", os.path.join(
                            os.path.dirname(__file__), os.pardir, "main.py"
                        )
                    )
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[spec.name] = module
                    spec.loader.exec_module(module)
                    self.assertIsNone(module.application.publish)
                    plugin = module.Plugin()
                    snapshot = asyncio.run(plugin.snapshot())
                    self.assertEqual(snapshot["connection"]["summary"], "192.0.2.7")
            finally:
                sys.modules.pop("avr_control_main_smoke", None)
                if old_main is not None:
                    sys.modules["avr_control_main_smoke"] = old_main
                if old_decky is None:
                    sys.modules.pop("decky", None)
                else:
                    sys.modules["decky"] = old_decky


if __name__ == "__main__":
    unittest.main()
