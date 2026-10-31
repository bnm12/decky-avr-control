"""Legacy Pioneer text protocol driver for pre-2016 receivers."""

from __future__ import annotations

import asyncio
import re
from typing import ClassVar

from .base import MUTE, POWER, VOLUME, Driver, DriverError, Found, Status, VolumeScale
from .receiver_session import shared_session

TELNET_PORTS = (23, 8102)


async def _read_line(reader: asyncio.StreamReader) -> str:
    line = await reader.readline()
    if not line:
        raise DriverError("AVR closed the connection")
    return line.decode("ascii", errors="ignore")


async def _request(host: str, port: int, command: str, pattern: str):
    matcher = re.compile(pattern)

    def classify(line: str) -> str | None:
        return "reply" if matcher.search(line) else None

    frames = await shared_session(host, port, _read_line).transact(
        [(command + "\r\n").encode("ascii")],
        classify,
        frozenset({"reply"}),
        timeout=2.0,
    )
    return matcher.search(frames["reply"])


class PioneerTelnetDriver(Driver):
    key: ClassVar[str] = "pioneer_telnet"
    label: ClassVar[str] = "Pioneer (legacy telnet, pre-2016)"
    capabilities = frozenset({VOLUME, MUTE, POWER})
    scale = VolumeScale(min=0, max=185, unit="dB", zero=161, steps_per_unit=2, default_cap=161)

    def __init__(self, config: dict):
        super().__init__(config)
        if "port" not in self.config:
            self.config["port"] = TELNET_PORTS[0]

    @property
    def host(self) -> str:
        return str(self.config.get("host", ""))

    async def _call(self, command: str, pattern: str):
        ports = list(TELNET_PORTS)
        configured = int(self.config.get("port", TELNET_PORTS[0]))
        if configured in ports:
            ports.remove(configured)
            ports.insert(0, configured)
        else:
            ports.insert(0, configured)
        last_error: Exception | None = None
        for port in ports:
            try:
                match = await _request(self.host, port, command, pattern)
                self.config["port"] = port
                return match
            except Exception as exc:
                last_error = exc
        raise DriverError(
            f"Can't reach AVR (tried ports 23, 8102): {last_error}"
        ) from last_error

    async def status(self) -> Status:
        volume = await self._call(r"?V", r"VOL(\d+)")
        mute = await self._call(r"?M", r"MUT([01])")
        power = await self._call(r"?P", r"PWR([012])")
        return Status(
            volume=max(0, min(185, int(volume.group(1)))),
            muted=volume is not None and mute.group(1) == "0",
            # The legacy reply flag is inverted: PWR0 means ON, PWR1 means OFF.
            power=power.group(1) == "0",
        )

    async def set_volume(self, raw: int) -> int:
        value = max(self.scale.min, min(self.scale.max, int(raw)))
        match = await self._call(f"{value:03d}VL", r"VOL(\d+)")
        return max(0, min(185, int(match.group(1))))

    async def set_mute(self, muted: bool) -> bool:
        match = await self._call("MO" if muted else "MF", r"MUT([01])")
        return match.group(1) == "0"

    async def set_power(self, on: bool) -> bool:
        match = await self._call("PO" if on else "PF", r"PWR([012])")
        return match.group(1) == "0"

    @classmethod
    async def probe(cls, config: dict) -> Found | None:
        host = str(config.get("host", ""))
        if not host:
            return None
        ports = list(TELNET_PORTS)
        configured = config.get("port")
        if configured in ports:
            ports.remove(configured)
            ports.insert(0, configured)
        elif configured is not None:
            ports.insert(0, int(configured))
        for port in ports:
            try:
                await _request(host, int(port), "?V", r"VOL(\d+)")
                return Found(
                    driver=cls.key,
                    model=str(config.get("model", "Pioneer receiver")),
                    id=str(config.get("id", host)),
                    host=host,
                    config={**config, "host": host, "port": int(port)},
                )
            except Exception:
                continue
        return None
