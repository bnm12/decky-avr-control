"""Backend tests; make the Decky py_modules package importable from the repo."""

from __future__ import annotations

import os
import sys

PY_MODULES = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir, "py_modules")
)
if PY_MODULES not in sys.path:
    sys.path.insert(0, PY_MODULES)
