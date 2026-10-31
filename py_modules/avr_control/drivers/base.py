"""The driver seam: everything the controller and UI know about a receiver.

A driver speaks one control protocol (eISCP, Denon telnet, Yamaha YXC, ...).
Volumes are raw receiver units (ints); `VolumeScale` turns them into what the
user sees. Drivers hold no global state and never import `decky`, so tests can
run them against fake receivers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar, Literal

VOLUME = "volume"
MUTE = "mute"
POWER = "power"
MODE = "mode"


class DriverError(Exception):
    """The receiver can't be reached or answered nonsense. Message is user-facing."""


class Unsupported(DriverError):
    """This driver/receiver doesn't offer the capability."""


class Refused(DriverError):
    """The receiver understood but refused (e.g. a mode unavailable for this input)."""


@dataclass(frozen=True)
class VolumeScale:
    """Raw volume range plus how to display it.

    display = (raw - zero) / steps_per_unit, shown with `unit`.
    eISCP on a VSX-935: zero=164, steps_per_unit=2 (0.5 dB steps).
    Absolute step displays: unit="steps", zero=0, steps_per_unit=1.
    Percent-only drivers: unit="%", zero=0, steps_per_unit=1.
    """

    min: int
    max: int
    unit: Literal["dB", "%", "steps"]
    zero: int = 0
    steps_per_unit: int = 1
    default_cap: int | None = None  # default slider cap; None = max

    def display(self, raw: int) -> float:
        return (raw - self.zero) / self.steps_per_unit

    def to_raw(self, value: float) -> int:
        raw = round(self.zero + value * self.steps_per_unit)
        return max(self.min, min(self.max, raw))

    def as_dict(self) -> dict:
        default_cap = self.default_cap
        if default_cap is None:
            default_cap = (
                self.min + round((self.max - self.min) * 0.4)
                if self.unit in ("%", "steps")
                else self.max
            )
        return {
            "min": self.min,
            "max": self.max,
            "unit": self.unit,
            "zero": self.zero,
            "steps_per_unit": self.steps_per_unit,
            "default_cap": default_cap,
        }


@dataclass(frozen=True)
class Mode:
    code: str  # driver-specific, opaque to everything above the driver
    name: str


@dataclass
class Status:
    """A snapshot. None = unknown or unsupported."""

    volume: int | None = None
    muted: bool | None = None
    power: bool | None = None
    mode: str | None = None  # Mode.code


@dataclass(frozen=True)
class Found:
    """A receiver found by discovery or probing.

    `config` is everything the driver needs to connect (always includes "host"
    for network drivers).
    """

    driver: str  # Driver.key
    model: str
    id: str  # stable unique id (MAC, serial, UDN): re-finds it after an IP change
    host: str
    config: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ConfigField:
    """A setting the UI must ask for (e.g. Home Assistant URL and token)."""

    key: str
    label: str
    secret: bool = False
    required: bool = True


class Driver(ABC):
    """One receiver, reached through one protocol.

    Setters return the value the receiver confirmed. Every method raises
    DriverError (or a subclass) on failure, never bare socket/HTTP errors.
    Optional capabilities raise Unsupported by default.
    """

    key: ClassVar[str]  # stable id stored in settings, e.g. "eiscp"
    label: ClassVar[str]  # shown in the UI, e.g. "Onkyo / Pioneer / Integra (eISCP)"
    config_fields: ClassVar[tuple[ConfigField, ...]] = (
        ConfigField("host", "Receiver IP address"),
    )

    def __init__(self, config: dict):
        self.config = dict(config)

    # ---------------------------------------------------------- description

    @property
    @abstractmethod
    def capabilities(self) -> frozenset[str]:
        """Subset of {VOLUME, MUTE, POWER, MODE}. May narrow after the first status()."""

    @property
    @abstractmethod
    def scale(self) -> VolumeScale:
        """May be refined after the first status() (e.g. Yamaha reads its range)."""

    # ---------------------------------------------------------------- state

    @abstractmethod
    async def status(self) -> Status:
        """Everything at once, in as few round trips as the protocol allows."""

    async def get_volume(self) -> int:
        return _require((await self.status()).volume, VOLUME)

    async def get_mute(self) -> bool:
        return _require((await self.status()).muted, MUTE)

    async def get_power(self) -> bool:
        return _require((await self.status()).power, POWER)

    async def get_mode(self) -> str:
        return _require((await self.status()).mode, MODE)

    async def set_volume(self, raw: int) -> int:
        raise Unsupported("This receiver doesn't support setting the volume")

    async def set_mute(self, muted: bool) -> bool:
        raise Unsupported("This receiver doesn't support mute")

    async def set_power(self, on: bool) -> bool:
        raise Unsupported("This receiver doesn't support power control")

    async def set_mode(self, code: str) -> str:
        raise Unsupported("This receiver doesn't support listening modes")

    async def list_modes(self) -> list[Mode]:
        """Modes the user can pick. May be empty even if MODE is supported."""
        return []

    def mode_name(self, code: str) -> str:
        """Display name for a mode code, including codes not in list_modes()."""
        return f"Mode {code}"

    async def close(self) -> None:
        """Release connections. Safe to call more than once."""

    # ------------------------------------------------------------- finding

    @classmethod
    async def discover(cls, timeout: float) -> list[Found]:
        """Find receivers on the LAN. Default: this driver can't discover."""
        return []

    @classmethod
    async def probe(cls, config: dict) -> Found | None:
        """Does a receiver speaking this protocol answer with this config?

        Used when the user types an IP: drivers are probed in registry order.
        Must be quick (about 2 s), must not change receiver state, and returns
        None (not an exception) when the receiver doesn't speak this protocol.
        """
        return None


def _require(value, what: str):
    if value is None:
        raise Unsupported(f"This receiver doesn't report {what}")
    return value
