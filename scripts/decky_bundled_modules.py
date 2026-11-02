"""List the Python modules available to plugins inside Decky Loader.

Decky runs plugin backends in its own PyInstaller-frozen Python, which only
contains the standard-library modules Decky itself imports. A module missing
from that bundle (e.g. xml.etree) crashes the plugin on load even though it is
"stdlib". This dev-only script reads a PluginLoader binary and writes the
module list that tests/test_decky_runtime.py checks our imports against.

    gh release download -R SteamDeckHomebrew/decky-loader -p PluginLoader
    uv run --with pyinstaller python scripts/decky_bundled_modules.py PluginLoader

Needs PyInstaller (not a plugin dependency).
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

# Compiled into libpython on a standard Linux CPython build (in no archive).
BUILTINS = {
    "_abc", "_ast", "_codecs", "_collections", "_contextvars", "_datetime",
    "_functools", "_imp", "_io", "_locale", "_opcode", "_operator", "_signal",
    "_sre", "_stat", "_string", "_suggestions", "_symtable", "_sysconfig",
    "_thread", "_tokenize", "_tracemalloc", "_types", "_typing", "_warnings",
    "_weakref", "atexit", "builtins", "errno", "faulthandler", "gc",
    "itertools", "marshal", "posix", "pwd", "sys", "time",
}


def bundled_modules(binary: str) -> set[str]:
    archive = CArchiveReader(binary)
    modules = set(BUILTINS)
    for name in archive.toc:
        if name.endswith(".pyz"):
            Path("_bundle.pyz").write_bytes(archive.extract(name))
            modules |= set(ZlibArchiveReader("_bundle.pyz").toc)
            Path("_bundle.pyz").unlink()
        elif name == "base_library.zip":
            with zipfile.ZipFile(io.BytesIO(archive.extract(name))) as base:
                for entry in base.namelist():
                    if entry.endswith(".pyc"):
                        module = entry[:-4].replace("/", ".")
                        modules.add(module.removesuffix(".__init__"))
        elif "lib-dynload/" in name:
            modules.add(name.split("lib-dynload/")[1].split(".")[0])
        elif ".cpython-" in name:
            modules.add(name.split(".cpython-")[0].replace("/", "."))
        elif "/" not in name and "." not in name:
            modules.add(name)  # frozen bootstrap modules such as `struct`
    return modules


if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent / "tests" / "decky_runtime_modules.txt"
    modules = sorted(bundled_modules(sys.argv[1]))
    out.write_text("\n".join(modules) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(modules)} modules -> {out}")
