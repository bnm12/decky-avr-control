"""The backend must only import modules that exist inside Decky's frozen Python.

Decky runs plugins in a PyInstaller build that bundles only the standard
library modules Decky itself uses (ADR-0020). Importing anything else, even
from the standard library, crashes the backend on load on the device while
every test on a developer machine passes. tests/decky_runtime_modules.txt is
generated from a PluginLoader release by scripts/decky_bundled_modules.py.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
OURS = ("avr_control", "decky", "__future__")


def _available() -> set[str]:
    with open(os.path.join(ROOT, "tests", "decky_runtime_modules.txt"), encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def _backend_files() -> list[str]:
    files = [os.path.join(ROOT, "main.py")]
    for folder, _, names in os.walk(os.path.join(ROOT, "py_modules")):
        files += [os.path.join(folder, n) for n in names if n.endswith(".py")]
    return files


def _is_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _imports(path: str) -> list[tuple[int, str]]:
    """Every absolute module an import statement loads, anywhere in the file."""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), path)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(node.lineno, alias.name) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append((node.lineno, node.module))
            for alias in node.names:
                submodule = f"{node.module}.{alias.name}"
                if _is_module(submodule):
                    found.append((node.lineno, submodule))
    return [(line, name) for line, name in found if name.split(".")[0] not in OURS]


class DeckyRuntimeImportTests(unittest.TestCase):
    maxDiff = None

    def test_backend_imports_exist_in_deckys_python(self):
        available = _available()
        missing = [
            f"{os.path.relpath(path, ROOT)}:{line} imports {name}"
            for path in _backend_files()
            for line, name in _imports(path)
            if name not in available
        ]
        self.assertEqual(
            missing,
            [],
            "Not bundled in Decky's Python, so the plugin would fail to load on device",
        )


if __name__ == "__main__":
    unittest.main()
