"""Available receiver drivers and common driver discovery helpers."""

from __future__ import annotations

import asyncio

from .base import Driver, Found
from .denon import DenonDriver
from .eiscp import EiscpDriver
from .homeassistant import HomeAssistantDriver
from .pioneer_telnet import PioneerTelnetDriver
from .yamaha import YamahaDriver

# Probe order: specific ports first; Denon and legacy Pioneer both use port 23,
# and Denon's `PW?` is the more specific question. Home Assistant only probes
# when it has a URL and token, so it goes last.
REGISTRY: list[type[Driver]] = [
    EiscpDriver,
    YamahaDriver,
    DenonDriver,
    PioneerTelnetDriver,
    HomeAssistantDriver,
]


def get_driver_class(key: str) -> type[Driver] | None:
    return next((driver for driver in REGISTRY if driver.key == key), None)


async def discover_all(timeout: float) -> list[Found]:
    results = await asyncio.gather(
        *(driver.discover(timeout) for driver in REGISTRY),
        return_exceptions=True,
    )
    found: list[Found] = []
    seen: set[str] = set()
    for result in results:
        if isinstance(result, BaseException):
            continue
        for item in result:
            if item.id not in seen:
                seen.add(item.id)
                found.append(item)
    return found


async def probe_all(config: dict) -> Found | None:
    for driver in REGISTRY:
        try:
            found = await driver.probe(dict(config))
        except Exception:
            continue
        if found is not None:
            return found
    return None


__all__ = [
    "Driver",
    "Found",
    "REGISTRY",
    "get_driver_class",
    "discover_all",
    "probe_all",
]
