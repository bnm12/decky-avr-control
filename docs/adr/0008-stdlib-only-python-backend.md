# The Python backend uses only the standard library

Decky plugins cannot rely on `pip install` running at runtime, so the Python backend uses only the standard library, alongside Decky's own `decky` module in the adapter. Protocol framing and parsing use `socket` and `asyncio`, not external runtime packages.

This is why the Cast route was rejected (see 0001): Google Home-style control would need `pychromecast` and its dependencies. The collect-modes tool follows the same rule and needs only the standard library.

## Consequences

Each protocol needs its own hand-written packet framing and parsing. The eISCP header (`ISCP`, big-endian 16-byte header) is built by hand in `py_modules/avr_control/drivers/eiscp.py`.
