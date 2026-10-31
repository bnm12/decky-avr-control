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
    and UPnP), so results are de-duplicated by id and by host, keeping the
    first driver in REGISTRY order: brand drivers before generic ones.
    """
    results = await asyncio.gather(
        *(driver.discover(timeout) for driver in REGISTRY),
        return_exceptions=True,
    )
    found: list[Found] = []
    seen_ids: set[str] = set()
    seen_hosts: set[str] = set()
    for result in results:
        if isinstance(result, BaseException):
            continue
        for item in result:
            if item.id in seen_ids or (item.host and item.host in seen_hosts):
                continue
            seen_ids.add(item.id)
            if item.host:
                seen_hosts.add(item.host)
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
