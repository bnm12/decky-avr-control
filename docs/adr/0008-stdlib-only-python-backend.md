# The Python backend uses only the standard library

Decky plugins cannot rely on `pip install` running at runtime, so the backend `main.py` imports only standard-library modules (`asyncio`, `json`, `os`, `re`, `socket`, `struct`, `time`) and Decky's own `decky` module. The eISCP and text protocols are hand-written on top of `socket` and `asyncio`.

This is why the Cast route was rejected (see 0001): Google Home-style control would need `pychromecast` and its dependencies. The collect-modes tool follows the same rule and needs only the standard library.

## Consequences

Each protocol needs its own hand-written packet framing and parsing. The eISCP header (`ISCP`, big-endian 16-byte header) is built by hand in `main.py`.
