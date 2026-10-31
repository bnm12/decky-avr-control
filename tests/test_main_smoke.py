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
                plugin = module.Plugin()
                self.assertEqual(
                    asyncio.run(plugin.get_connection()),
                    {
                        "configured": False,
                        "driver": None,
                        "label": None,
                        "summary": "",
                        "fields": {},
                    },
                )
                state = asyncio.run(plugin.get_state())
                self.assertFalse(state["ok"])
                from avr_control.drivers import REGISTRY

                drivers = asyncio.run(plugin.list_drivers())
                self.assertEqual(
                    [d["key"] for d in drivers], [d.key for d in REGISTRY]
                )
                asyncio.run(plugin._main())
                asyncio.run(plugin._unload())
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
                    plugin = module.Plugin()
                    self.assertEqual(asyncio.run(plugin.get_host()), "192.0.2.7")
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
