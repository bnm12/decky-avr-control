"""Denon / Marantz AVR driver for the port-23 ASCII control protocol."""

from __future__ import annotations

import asyncio
import re
import weakref
from typing import ClassVar

from .. import ssdp
from .base import (
    MODE,
    MUTE,
    POWER,
    VOLUME,
    Driver,
    DriverError,
    Found,
    Mode,
    Refused,
    Status,
    VolumeScale,
)

DENON_PORT = 23
COMMAND_INTERVAL = 0.05
_REPLY_TIMEOUT = 1.2
_STATUS_TIMEOUT = 0.65
_VOLUME_TOKEN = re.compile(r"^MV(\d{2,3})$")
_MAX_VOLUME_LINE = re.compile(r"^MVMAX\s*(\d{2,3})$")
_POWER_LINE = re.compile(r"^PW(ON|STANDBY)$")
_MUTE_LINE = re.compile(r"^MU(ON|OFF)$")
_MODE_LINE = re.compile(r"^MS(.+)$")

MODE_NAMES = [
    ("MOVIE", "Movie"),
    ("MUSIC", "Music"),
    ("GAME", "Game"),
    ("DIRECT", "Direct"),
    ("PURE DIRECT", "Pure Direct"),
    ("STEREO", "Stereo"),
    ("AUTO", "Auto"),
    ("DOLBY DIGITAL", "Dolby Digital"),
    ("DOLBY SURROUND", "Dolby Surround"),
    ("DTS SURROUND", "DTS Surround"),
    ("DTS NEURAL:X", "DTS Neural:X"),
    ("MCH STEREO", "Multi-Channel Stereo"),
    ("VIRTUAL", "Virtual"),
]
_MODE_NAME_BY_CODE = dict(MODE_NAMES)

_DOLBY_REPORT_PREFIXES = (
    "DOLBY ",
    "DOLBY AUDIO",
    "STANDARD(DOLBY)",
    "AAC+DSUR",
    "AAC+DOLBY",
    "AAC+PL2Z",
    "M CH IN+DOLBY",
    "M CH IN+DSUR",
    "M CH IN+PL2Z",
    "MULTI IN + DOLBY",
    "MULTI IN + DSUR",
)
_DTS_REPORT_PREFIXES = (
    "DTS ",
    "DTS+",
    "DTS-",
    "DTS:",
    "AAC+VIRTUAL:X",
    "IMAX DTS",
    "STANDARD(DTS)",
    "NEURAL:X",
    "M CH IN+NEURAL:X",
    "M CH IN+VIRTUAL:X",
    "MULTI IN + NEURAL:X",
    "MULTI IN + VIRTUAL:X",
    "MULTI CH IN",
)


def _canonical_mode(code: str) -> str:
    """Translate decoder-specific status codes to settable mode commands."""
    original = code.strip().upper()
    normalized = re.sub(r"\s+", " ", original.replace("_", " "))
    if normalized in _MODE_NAME_BY_CODE:
        return normalized
    if normalized in {"AUTO", "NONE"}:
        return "AUTO"
    if normalized in {"MCH STEREO", "MULTI CH STEREO"}:
        return "MCH STEREO"
    if normalized.startswith(
        ("AAC+", "DOLBY ", "DTS ", "DTS+", "DTS-", "DTS:", "M CH IN", "MULTI IN")
    ):
        for suffix, canonical in (
            (" CINEMA", "MOVIE"),
            (" MOVIE", "MOVIE"),
            (" C", "MOVIE"),
            (" MUSIC", "MUSIC"),
            (" M", "MUSIC"),
            (" GAME", "GAME"),
            (" G", "GAME"),
        ):
            if normalized.endswith(suffix):
                return canonical
    if normalized.startswith(_DOLBY_REPORT_PREFIXES):
        return "DOLBY DIGITAL"
    if normalized == "NEURAL" or normalized.startswith(
        _DTS_REPORT_PREFIXES
    ) or normalized == "AAC+NEURAL:X":
        return "DTS SURROUND"
    return original


def _decode_volume(token: str) -> int:
    """Convert Denon's whole/half volume notation into half-step raw units."""
    if len(token) == 2:
        return int(token) * 2
    if len(token) == 3 and token[-1] in "05":
        return int(token[:2]) * 2 + (token[-1] == "5")
    raise ValueError(f"Invalid Denon volume value: {token}")


def _encode_volume(raw: int) -> str:
    whole, half = divmod(raw, 2)
    return f"{whole:02d}{'5' if half else ''}"


async def _open(
    host: str, port: int
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    try:
        return await asyncio.wait_for(asyncio.open_connection(host, port), 2.0)
    except Exception as exc:
        raise DriverError(f"Can't reach Denon/Marantz receiver at {host}:{port}: {exc}") from exc


async def _read_line(reader: asyncio.StreamReader, timeout: float) -> str:
    data = await asyncio.wait_for(reader.readuntil(b"\r"), timeout)
    return data[:-1].decode("ascii", errors="ignore").strip()


async def _close(writer: asyncio.StreamWriter) -> None:
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass


class DenonDriver(Driver):
    key: ClassVar[str] = "denon"
    label: ClassVar[str] = "Denon / Marantz"
    capabilities = frozenset({VOLUME, MUTE, POWER, MODE})
    _operation_locks: ClassVar[weakref.WeakKeyDictionary] = weakref.WeakKeyDictionary()

    def __init__(self, config: dict):
        super().__init__(config)
        self._scale = VolumeScale(
            min=0, max=196, unit="dB", zero=160, steps_per_unit=2, default_cap=100
        )

    @property
    def host(self) -> str:
        return str(self.config.get("host", ""))

    @property
    def port(self) -> int:
        try:
            port = int(self.config.get("port", DENON_PORT))
        except (TypeError, ValueError) as exc:
            raise DriverError("Invalid Denon receiver port") from exc
        if not 1 <= port <= 65535:
            raise DriverError("Invalid Denon receiver port")
        return port

    @property
    def scale(self) -> VolumeScale:
        return self._scale

    def _operation_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        locks = self._operation_locks.get(loop)
        if locks is None:
            locks = weakref.WeakValueDictionary()
            self._operation_locks[loop] = locks
        receiver = (self.host, self.port)
        lock = locks.get(receiver)
        if lock is None:
            lock = asyncio.Lock()
            locks[receiver] = lock
        return lock

    def _record_max_volume(self, line: str) -> None:
        match = _MAX_VOLUME_LINE.fullmatch(line)
        if not match:
            return
        try:
            maximum = _decode_volume(match.group(1))
        except ValueError:
            return
        self._scale = VolumeScale(
            min=0,
            max=maximum,
            unit="dB",
            zero=160,
            steps_per_unit=2,
            default_cap=min(100, maximum),
        )

    async def _single(
        self,
        command: str,
        matches,
        *,
        timeout: float = _REPLY_TIMEOUT,
        trailing_grace: float = 0.07,
    ) -> str:
        async with self._operation_lock():
            return await self._single_serialized(
                command,
                matches,
                timeout=timeout,
                trailing_grace=trailing_grace,
            )

    async def _single_serialized(
        self,
        command: str,
        matches,
        *,
        timeout: float = _REPLY_TIMEOUT,
        trailing_grace: float = 0.07,
    ) -> str:
        reader, writer = await _open(self.host, self.port)
        try:
            writer.write(command.encode("ascii") + b"\r")
            await writer.drain()
            deadline = asyncio.get_running_loop().time() + timeout
            result = None
            while result is None:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise asyncio.TimeoutError()
                line = await _read_line(reader, remaining)
                if not line:
                    continue
                self._record_max_volume(line)
                if matches(line):
                    result = line

            if trailing_grace:
                grace_deadline = min(
                    deadline,
                    asyncio.get_running_loop().time() + trailing_grace,
                )
                while grace_deadline > asyncio.get_running_loop().time():
                    try:
                        line = await _read_line(
                            reader, grace_deadline - asyncio.get_running_loop().time()
                        )
                    except asyncio.TimeoutError:
                        break
                    if line:
                        self._record_max_volume(line)
            return result
        except asyncio.TimeoutError:
            raise
        except DriverError:
            raise
        except Exception as exc:
            raise DriverError(f"Denon/Marantz command {command!r} failed: {exc}") from exc
        finally:
            await _close(writer)

    async def status(self) -> Status:
        async with self._operation_lock():
            return await self._status_serialized()

    async def _status_serialized(self) -> Status:
        reader, writer = await _open(self.host, self.port)
        power: bool | None = None
        volume: int | None = None
        muted: bool | None = None
        mode: str | None = None
        try:
            commands = ("PW?", "MV?", "MU?", "MS?")
            for index, command in enumerate(commands):
                if index:
                    await asyncio.sleep(COMMAND_INTERVAL)
                writer.write(command.encode("ascii") + b"\r")
                await writer.drain()

            deadline = asyncio.get_running_loop().time() + _STATUS_TIMEOUT
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    line = await _read_line(reader, remaining)
                except asyncio.TimeoutError:
                    break
                if not line:
                    continue

                self._record_max_volume(line)
                power_match = _POWER_LINE.fullmatch(line)
                if power_match:
                    power = power_match.group(1) == "ON"
                    if not power:
                        return Status(power=False)
                    continue

                volume_match = _VOLUME_TOKEN.fullmatch(line)
                if volume_match:
                    try:
                        volume = _decode_volume(volume_match.group(1))
                    except ValueError:
                        pass
                    continue

                mute_match = _MUTE_LINE.fullmatch(line)
                if mute_match:
                    muted = mute_match.group(1) == "ON"
                    continue

                mode_match = _MODE_LINE.fullmatch(line)
                if mode_match and mode_match.group(1) != "?":
                    mode = _canonical_mode(mode_match.group(1))

                if power is True and all(
                    value is not None for value in (volume, muted, mode)
                ):
                    break
        except DriverError:
            raise
        except Exception as exc:
            raise DriverError(f"Denon/Marantz status request failed: {exc}") from exc
        finally:
            await _close(writer)

        if power is None:
            raise DriverError("Denon/Marantz receiver didn't report its power state")
        return Status(volume=volume, muted=muted, power=power, mode=mode)

    async def set_volume(self, raw: int) -> int:
        try:
            value = max(self.scale.min, min(self.scale.max, int(raw)))
        except (TypeError, ValueError) as exc:
            raise DriverError("Invalid Denon volume value") from exc
        pattern = re.compile(r"^MV(\d{2,3})$")
        try:
            reply = await self._single(
                f"MV{_encode_volume(value)}", pattern.fullmatch
            )
            match = pattern.fullmatch(reply)
            if match is None:
                raise DriverError("Denon/Marantz returned an invalid volume")
            try:
                return _decode_volume(match.group(1))
            except ValueError as exc:
                raise DriverError("Denon/Marantz returned an invalid volume") from exc
        except asyncio.TimeoutError as exc:
            raise DriverError("Denon/Marantz didn't confirm the volume") from exc

    async def set_mute(self, muted: bool) -> bool:
        try:
            reply = await self._single(
                "MUON" if muted else "MUOFF", _MUTE_LINE.fullmatch
            )
        except asyncio.TimeoutError as exc:
            raise DriverError("Denon/Marantz didn't confirm mute") from exc
        return reply == "MUON"

    async def set_power(self, on: bool) -> bool:
        command = "PWON" if on else "PWSTANDBY"
        try:
            reply = await self._single(command, _POWER_LINE.fullmatch)
        except asyncio.TimeoutError as exc:
            raise DriverError("Denon/Marantz didn't confirm power") from exc
        return reply == "PWON"

    async def set_mode(self, code: str) -> str:
        normalized = _canonical_mode(str(code))
        try:
            reply = await self._single(f"MS{normalized}", _MODE_LINE.fullmatch)
        except asyncio.TimeoutError as exc:
            raise Refused(
                "The receiver didn't accept that listening mode (it may be unavailable "
                "for the current input or signal)"
            ) from exc
        actual = _canonical_mode(reply[2:])
        if actual in {"", "?", "ERROR"}:
            raise Refused(
                "The receiver didn't accept that listening mode (it may be unavailable "
                "for the current input or signal)"
            )
        return actual

    async def list_modes(self) -> list[Mode]:
        return [Mode(code, name) for code, name in MODE_NAMES]

    def mode_name(self, code: str) -> str:
        normalized = str(code).strip().upper()
        if normalized in _MODE_NAME_BY_CODE:
            return _MODE_NAME_BY_CODE[normalized]
        pretty = re.sub(r"\s+", " ", normalized.replace("_", " ")).strip()
        if not pretty:
            return "Unknown mode"
        special = {"DTS", "MCH", "PCM", "NEURAL:X", "D+"}
        return " ".join(
            word if word in special else word.title() for word in pretty.split()
        )

    @classmethod
    async def discover(cls, timeout: float) -> list[Found]:
        if timeout <= 0:
            return []
        targets = (
            "urn:schemas-upnp-org:device:MediaRenderer:1",
            "upnp:rootdevice",
        )
        try:
            responses = await asyncio.gather(
                *(ssdp.search(target, timeout) for target in targets)
            )
        except Exception:
            return []

        candidates: dict[tuple[str, str], dict[str, str]] = {}
        for response_group in responses:
            for response in response_group:
                location = str(response.get("location", ""))
                host = str(response.get("ip", ""))
                if location and host:
                    candidates[(location, host)] = response

        async def fetch_if_denon(
            item: dict[str, str]
        ) -> Found | None:
            try:
                description = await ssdp.fetch_description(item["location"])
            except Exception:
                return None
            manufacturer = str(description.get("manufacturer", ""))
            model_name = str(description.get("modelName", ""))
            friendly_name = str(description.get("friendlyName", ""))
            identity = " ".join((manufacturer, model_name, friendly_name)).lower()
            if not any(
                marker in identity
                for marker in ("denon", "marantz", "heos", "d&m", "d and m", "dm holdings")
            ):
                return None
            udn = str(description.get("UDN", "")).strip()
            usn = str(item.get("usn", "")).split("::", 1)[0].strip()
            return Found(
                driver=cls.key,
                model=model_name or friendly_name or "Denon/Marantz",
                id=udn or usn or item["ip"],
                host=item["ip"],
                config={"host": item["ip"]},
            )

        found = await asyncio.gather(
            *(fetch_if_denon(item) for item in candidates.values())
        )
        unique: dict[str, Found] = {}
        for receiver in found:
            if receiver is not None:
                unique[receiver.id] = receiver
        return list(unique.values())

    @classmethod
    async def probe(cls, config: dict) -> Found | None:
        host = str(config.get("host", "")).strip()
        if not host:
            return None
        try:
            port = int(config.get("port", DENON_PORT))
            if not 1 <= port <= 65535:
                return None
            reply = await cls(config)._single(
                "PW?",
                _POWER_LINE.fullmatch,
                timeout=2.0,
                trailing_grace=0.0,
            )
        except Exception:
            return None
        return Found(
            driver=cls.key,
            model=str(config.get("model") or "Denon/Marantz"),
            id=str(config.get("id") or f"denon-{host}"),
            host=host,
            config={**config, "host": host, "port": port},
        )
