"""Available receiver drivers and common driver discovery helpers."""

from __future__ import annotations

import asyncio

from .base import Driver, Found
from .denon import DenonDriver
from .eiscp import EiscpDriver
from .heos import HeosDriver
from .homeassistant import HomeAssistantDriver
from .pioneer_telnet import PioneerTelnetDriver
from .sony import SonyDriver
from .upnp import UpnpDriver
from .yamaha import YamahaDriver
from .yamaha_ync import YamahaYNCDriver

# Order matters twice. Probes (manual IP) run in this order and discovery keeps
# the first driver per host, so brand drivers come before generic ones:
# - Yamaha YXC before legacy YNC (a MusicCast unit answers both).
# - Denon before HEOS (AVRs with HEOS built-in answer both) and before legacy
#   Pioneer (both use port 23; Denon's `PW?` is the more specific question).
# - UPnP is the generic volume/mute fallback that many brands also expose.
# - Home Assistant only probes with a URL and token, so it goes last.
REGISTRY: list[type[Driver]] = [
    EiscpDriver,
    YamahaDriver,
    YamahaYNCDriver,
    SonyDriver,
    DenonDriver,
    HeosDriver,
    PioneerTelnetDriver,
    UpnpDriver,
    HomeAssistantDriver,
]


def get_driver_class(key: str) -> type[Driver] | None:
    return next((driver for driver in REGISTRY if driver.key == key), None)


async def discover_all(timeout: float) -> list[Found]:
    """Every driver's discovery at once.

    One receiver often answers several protocols (a Denon AVR also speaks HEOS
    and UPnP), so a host claimed by an earlier driver in REGISTRY order is
    skipped by later ones: brand drivers win over generic ones. One driver may
    still report several devices on one host (embedded UPnP renderers), and
    duplicate ids are always dropped.
    """
    results = await asyncio.gather(
        *(driver.discover(timeout) for driver in REGISTRY),
        return_exceptions=True,
    )
    found: list[Found] = []
    seen_ids: set[str] = set()
    host_owner: dict[str, str] = {}
    for result in results:
        if isinstance(result, BaseException):
            continue
        for item in result:
            owner = host_owner.get(item.host) if item.host else None
            if item.id in seen_ids or (owner is not None and owner != item.driver):
                continue
            seen_ids.add(item.id)
            if item.host:
                host_owner.setdefault(item.host, item.driver)
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
