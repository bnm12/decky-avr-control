"""Legacy Pioneer text protocol driver for pre-2016 receivers."""

from __future__ import annotations

import asyncio
import re
from typing import Awaitable, Callable, ClassVar

from .base import (
    MUTE,
    POWER,
    VOLUME,
    Driver,
    DriverError,
    Found,
    ModeState,
    Status,
    VolumeScale,
)
from .receiver_session import ReceiverSessionError, shared_session

TELNET_PORTS = (23, 8102)


async def _read_line(reader: asyncio.StreamReader) -> str:
    line = await reader.readline()
    if not line:
        raise DriverError("AVR closed the connection")
    return line.decode("ascii", errors="ignore")


async def _request(
    host: str,
    port: int,
    command: str,
    pattern: str,
    expected: Callable[[re.Match[str]], bool] | None = None,
):
    matcher = re.compile(pattern)
    last_observed: re.Match[str] | None = None

    def classify(line: str) -> str | None:
        nonlocal last_observed
        match = matcher.search(line)
        if match is None:
            return None
        last_observed = match
        return "reply" if expected is None or expected(match) else None

    try:
        frames = await shared_session(host, port, _read_line).transact(
            [(command + "\r\n").encode("ascii")],
            classify,
            frozenset({"reply"}),
            timeout=2.0,
        )
    except ReceiverSessionError as exc:
        if expected is not None and exc.kind == "confirmation" and last_observed:
            return last_observed
        raise
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

    async def _call(
        self,
        command: str,
        pattern: str,
        expected: Callable[[re.Match[str]], bool] | None = None,
    ):
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
                match = await _request(
                    self.host, port, command, pattern, expected=expected
                )
                self.config["port"] = port
                return match
            except ReceiverSessionError as exc:
                if exc.kind == "confirmation":
                    raise DriverError("AVR didn't confirm the command") from exc
                last_error = exc
            except Exception as exc:
                last_error = exc
        raise DriverError(
            f"Can't reach AVR (tried ports 23, 8102): {last_error}"
        ) from last_error

    async def status(self) -> Status:
        power = await self._call(r"?P", r"PWR([012])")
        powered_on = power.group(1) == "0"
        if not powered_on:
            return Status(power=False)
        volume = await self._call(r"?V", r"VOL(\d+)")
        mute = await self._call(r"?M", r"MUT([01])")
        return Status(
            volume=max(0, min(185, int(volume.group(1)))),
            muted=mute.group(1) == "0",
            # The legacy reply flag is inverted: PWR0 means ON, PWR1 means OFF.
            power=powered_on,
        )

    async def watch(
        self, on_change: Callable[[Status], None]
    ) -> Callable[[], Awaitable[None]]:
        port = int(self.config.get("port", TELNET_PORTS[0]))
        session = shared_session(self.host, port, _read_line)

        def receive(line: str) -> None:
            line = line.strip()
            volume = re.fullmatch(r"VOL(\d+)", line)
            mute = re.fullmatch(r"MUT([01])", line)
            power = re.fullmatch(r"PWR([012])", line)
            if volume:
                on_change(Status(volume=max(0, min(185, int(volume.group(1))))))
            elif mute:
                on_change(Status(muted=mute.group(1) == "0"))
            elif power:
                on_change(Status(power=power.group(1) == "0"))

        session.subscribe(receive)

        async def unsubscribe() -> None:
            await session.unsubscribe(receive)

        return unsubscribe

    async def mode_state(self, status: Status | None = None) -> ModeState:
        return ModeState(None, (), complete=False)

    @classmethod
    def mode_label(cls, code: str) -> str:
        return f"Mode {code}"

    async def set_volume(self, raw: int) -> int:
        value = max(self.scale.min, min(self.scale.max, int(raw)))
        match = await self._call(
            f"{value:03d}VL",
            r"VOL(\d+)",
            expected=lambda response: int(response.group(1)) == value,
        )
        return max(0, min(185, int(match.group(1))))

    async def set_mute(self, muted: bool) -> bool:
        match = await self._call(
            "MO" if muted else "MF",
            r"MUT([01])",
            expected=lambda response: (response.group(1) == "0") is muted,
        )
        return match.group(1) == "0"

    async def set_power(self, on: bool) -> bool:
        match = await self._call(
            "PO" if on else "PF",
            r"PWR([012])",
            expected=lambda response: (response.group(1) == "0") is on,
        )
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
