"""Onkyo-style eISCP driver, also used by recent Pioneer and Integra AVRs."""

from __future__ import annotations

import asyncio
import re
import socket
import struct
import time
from typing import Callable, ClassVar

from .base import (
    MODE,
    MUTE,
    POWER,
    VOLUME,
    ConfigField,
    Driver,
    DriverError,
    Found,
    Mode,
    Refused,
    Status,
    VolumeScale,
)

EISCP_PORT = 60128
DISCOVERY_TARGETS: list[tuple[str, int]] | None = None
DISCOVERY_BIND_PORT = EISCP_PORT
DISCOVERY_PORT = EISCP_PORT
_DISCOVERY_RE = re.compile(r"ECN([^/\x19\r\n]+)/(\d+)/([^/]*)/([0-9A-Fa-f]+)")
MODE_NAMES = [
    ("FF", "Auto Surround"),
    ("00", "Stereo"),
    ("01", "Direct"),
    ("11", "Pure Direct"),
    ("0F", "Mono"),
    ("13", "Mono Music"),
    ("0C", "Extended Stereo"),
    ("0D", "Front Stage Surround"),
    ("80", "Dolby Audio - Surround"),
    ("82", "DTS Neural:X"),
    ("17", "DTS Virtual:X"),
    ("03", "Advanced Game"),
    ("05", "Action"),
    ("06", "Rock/Pop"),
    ("08", "Classical"),
    ("09", "Unplugged"),
    ("0A", "Entertainment Show"),
    ("0B", "Drama"),
    ("0E", "Sports"),
]
REFUSED_MODE_MESSAGE = (
    "The receiver didn't accept that mode (it may not be available for the "
    "current input or signal)"
)
_NJAPUSH = re.compile(r"(?:^|!)\d*NJA")
_DEFAULT_VOLUME_SCALE = {"zero_db": 164, "volume_steps": 2, "max_volume": 185}
_MODEL_VOLUME_SCALES = {
    # The zero point and step size are measured; 185 is the historical clamp.
    "VSX-935": {"zero_db": 164, "volume_steps": 2, "max_volume": 185},
}


def packet(message: str) -> bytes:
    data = message.encode("ascii")
    return b"ISCP" + struct.pack(">IIB3x", 16, len(data), 1) + data


async def _read_packet(reader: asyncio.StreamReader) -> str:
    header = await reader.readexactly(16)
    if header[:4] != b"ISCP":
        raise DriverError("Unexpected reply (not eISCP)")
    header_size, data_size = struct.unpack(">II", header[4:12])
    if header_size < 16 or data_size > 65536:
        raise DriverError("Invalid eISCP packet")
    if header_size > 16:
        await reader.readexactly(header_size - 16)
    return (await reader.readexactly(data_size)).decode("ascii", errors="ignore")


async def _open(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    try:
        return await asyncio.wait_for(asyncio.open_connection(host, port), 2.0)
    except Exception as exc:
        raise DriverError(f"Can't reach AVR at {host}:{port}: {exc}") from exc


async def _query(host: str, port: int, command: str, pattern: str, timeout: float = 2.0):
    reader, writer = await _open(host, port)
    try:
        writer.write(packet(f"!1{command}\r"))
        await writer.drain()
        matcher = re.compile(pattern)
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError()
            payload = await asyncio.wait_for(_read_packet(reader), remaining)
            if _NJAPUSH.search(payload):
                continue
            match = matcher.search(payload)
            if match:
                return match
    except (asyncio.TimeoutError, asyncio.IncompleteReadError):
        raise
    except DriverError:
        raise
    except Exception as exc:
        raise DriverError(f"eISCP request failed: {exc}") from exc
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def _multi(
    host: str,
    port: int,
    queries: list[tuple[str, str, bool]],
    required_timeout: float = 2.0,
    grace: float = 0.6,
    complete_when: Callable[[list[re.Match[str] | None]], bool] | None = None,
    grace_indices: set[int] | None = None,
) -> list[re.Match[str] | None]:
    reader, writer = await _open(host, port)
    try:
        for command, _, _ in queries:
            writer.write(packet(f"!1{command}\r"))
        await writer.drain()
        patterns = [re.compile(pattern) for _, pattern, _ in queries]
        result: list[re.Match[str] | None] = [None] * len(queries)
        required = {i for i, query in enumerate(queries) if query[2]}
        optional = {i for i, query in enumerate(queries) if not query[2]}
        deadline = asyncio.get_running_loop().time() + required_timeout

        while (
            not complete_when(result)
            if complete_when is not None
            else any(result[i] is None for i in required)
        ):
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError()
            payload = await asyncio.wait_for(_read_packet(reader), remaining)
            if _NJAPUSH.search(payload):
                continue
            for i, pattern in enumerate(patterns):
                if result[i] is None:
                    result[i] = pattern.search(payload)

        if grace_indices is not None:
            optional = grace_indices
        if optional and any(result[i] is None for i in optional):
            deadline = asyncio.get_running_loop().time() + grace
            while any(result[i] is None for i in optional):
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    payload = await asyncio.wait_for(_read_packet(reader), remaining)
                except (asyncio.TimeoutError, asyncio.IncompleteReadError, OSError):
                    break
                if _NJAPUSH.search(payload):
                    continue
                for i in optional:
                    if result[i] is None:
                        result[i] = patterns[i].search(payload)
        return result
    except (asyncio.TimeoutError, asyncio.IncompleteReadError):
        raise
    except DriverError:
        raise
    except Exception as exc:
        raise DriverError(f"eISCP status request failed: {exc}") from exc
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


def _local_prefix() -> str | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0].rsplit(".", 1)[0]
    except OSError:
        return None
    finally:
        sock.close()


def _discover_blocking(timeout: float) -> list[Found]:
    targets = DISCOVERY_TARGETS
    if targets is None:
        targets = [("255.255.255.255", DISCOVERY_PORT)]
        prefix = _local_prefix()
        if prefix:
            targets.append((prefix + ".255", DISCOVERY_PORT))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("", DISCOVERY_BIND_PORT))
        except OSError:
            sock.bind(("", 0))
        sock.settimeout(0.2)
        probes = [packet("!xECNQSTN\r"), packet("!pECNQSTN\r")]
        by_id: dict[str, Found] = {}
        end = time.monotonic() + timeout
        last_send = 0.0
        while time.monotonic() < end:
            if time.monotonic() - last_send >= 1.0:
                for target in targets:
                    for probe in probes:
                        try:
                            sock.sendto(probe, target)
                        except OSError:
                            pass
                last_send = time.monotonic()
            try:
                data, address = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if data[:4] != b"ISCP":
                continue
            try:
                header_size, data_size = struct.unpack(">II", data[4:12])
                payload = data[header_size : header_size + data_size].decode(
                    "ascii", errors="ignore"
                )
            except (struct.error, IndexError):
                continue
            match = _DISCOVERY_RE.search(payload)
            if not match:
                continue
            model, port, _region, ident = match.groups()
            ident = ident.upper()
            by_id[ident] = Found(
                driver="eiscp",
                model=model,
                id=ident,
                host=address[0],
                config={"host": address[0], "port": int(port), "model": model},
            )
        return list(by_id.values())
    finally:
        sock.close()


def _volume_setting(value, key: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise DriverError(f"eISCP {key} must be an integer from {minimum} to {maximum}")
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError, OverflowError) as exc:
        raise DriverError(
            f"eISCP {key} must be an integer from {minimum} to {maximum}"
        ) from exc
    if not minimum <= parsed <= maximum:
        raise DriverError(f"eISCP {key} must be an integer from {minimum} to {maximum}")
    return parsed


def _volume_scale(config: dict) -> VolumeScale:
    model = str(config.get("model", "")).strip().upper()
    defaults = dict(_DEFAULT_VOLUME_SCALE)
    defaults.update(_MODEL_VOLUME_SCALES.get(model, {}))

    max_value = config.get("max_volume")
    maximum = (
        _volume_setting(max_value, "max_volume", 1, 255)
        if max_value is not None and str(max_value).strip()
        else defaults["max_volume"]
    )

    zero_value = config.get("zero_db")
    zero_text = str(zero_value).strip().lower() if zero_value is not None else ""
    absolute = (
        "zero_db" in config
        and (zero_value is None or not zero_text or zero_text == "absolute")
    ) or (
        "zero_db" not in config
        and max_value is not None
        and bool(str(max_value).strip())
        and maximum == 100
    )
    zero = (
        None
        if absolute
        else _volume_setting(zero_value, "zero_db", 0, 255)
        if "zero_db" in config
        else defaults["zero_db"]
    )

    steps_value = config.get("volume_steps")
    if steps_value is not None and str(steps_value).strip():
        steps = _volume_setting(steps_value, "volume_steps", 1, 2)
    else:
        steps = 1 if zero is None else defaults["volume_steps"]

    if zero is None:
        if maximum != 100:
            raise DriverError(
                "eISCP absolute volume needs max_volume=100 to display as %; "
                "otherwise provide zero_db for a dB scale"
            )
        if steps != 1:
            raise DriverError("eISCP absolute 0-100 volume requires volume_steps=1")
        return VolumeScale(
            min=0,
            max=maximum,
            unit="%",
            zero=0,
            steps_per_unit=1,
            default_cap=round(maximum * 0.4),
        )

    return VolumeScale(
        min=0,
        max=maximum,
        unit="dB",
        zero=zero,
        steps_per_unit=steps,
        default_cap=max(0, min(maximum, zero - 32 * steps)),
    )


class EiscpDriver(Driver):
    key: ClassVar[str] = "eiscp"
    label: ClassVar[str] = "Onkyo / Pioneer / Integra (eISCP)"
    capabilities = frozenset({VOLUME, MUTE, POWER, MODE})
    scale = _volume_scale({})
    config_fields: ClassVar[tuple[ConfigField, ...]] = Driver.config_fields + (
        ConfigField("volume_steps", "Volume steps per displayed unit (1 or 2)", required=False),
        ConfigField("zero_db", "Raw volume at 0 dB (blank for absolute volume)", required=False),
        ConfigField("max_volume", "Maximum raw volume", required=False),
    )

    def __init__(self, config: dict):
        super().__init__(config)
        self.scale = _volume_scale(self.config)

    @property
    def host(self) -> str:
        return str(self.config.get("host", ""))

    @property
    def port(self) -> int:
        return int(self.config.get("port", EISCP_PORT))

    async def status(self) -> Status:
        try:
            volume, mute, mode, power = await _multi(
                self.host,
                self.port,
                [
                    ("MVLQSTN", r"MVL([0-9A-F]{2})", False),
                    ("AMTQSTN", r"AMT([0-9A-F]{2})", False),
                    ("LMDQSTN", r"LMD([0-9A-F]{2,4})", False),
                    ("PWRQSTN", r"PWR(00|01)", False),
                ],
                complete_when=lambda result: (
                    result[3] is not None
                    and result[3].group(1) == "00"
                )
                or (result[0] is not None and result[1] is not None),
                grace_indices={2, 3},
            )
        except (asyncio.TimeoutError, asyncio.IncompleteReadError) as exc:
            raise DriverError("AVR didn't report its power state") from exc
        return Status(
            volume=int(volume.group(1), 16) if volume else None,
            muted=(mute.group(1) == "01") if mute else None,
            power=(power.group(1) == "01") if power else None,
            mode=mode.group(1).upper() if mode else None,
        )

    async def _set(self, command: str, pattern: str) -> re.Match[str]:
        try:
            return await _query(self.host, self.port, command, pattern)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError) as exc:
            raise DriverError("AVR didn't confirm the command") from exc

    async def set_volume(self, raw: int) -> int:
        value = max(self.scale.min, min(self.scale.max, int(raw)))
        match = await self._set(f"MVL{value:02X}", r"MVL([0-9A-F]{2})")
        return int(match.group(1), 16)

    async def set_mute(self, muted: bool) -> bool:
        match = await self._set(
            "AMT01" if muted else "AMT00", r"AMT([0-9A-F]{2})"
        )
        return match.group(1) == "01"

    async def set_power(self, on: bool) -> bool:
        match = await self._set("PWR01" if on else "PWR00", r"PWR(00|01)")
        return match.group(1) == "01"

    async def set_mode(self, code: str) -> str:
        code = str(code).strip().upper()
        if not re.fullmatch(r"[0-9A-F]{2,4}", code):
            raise Refused(f"Invalid mode code: {code}")
        try:
            match = await _query(
                self.host, self.port, f"LMD{code}", r"LMD([0-9A-F]{2,4})"
            )
        except (asyncio.TimeoutError, asyncio.IncompleteReadError) as exc:
            raise Refused(REFUSED_MODE_MESSAGE) from exc
        return match.group(1).upper()

    async def list_modes(self) -> list[Mode]:
        return [Mode(code, name) for code, name in MODE_NAMES]

    def mode_name(self, code: str) -> str:
        return dict(MODE_NAMES).get(code.upper(), f"Mode {code}")

    @classmethod
    async def discover(cls, timeout: float) -> list[Found]:
        return await asyncio.get_running_loop().run_in_executor(
            None, _discover_blocking, timeout
        )

    @classmethod
    async def probe(cls, config: dict) -> Found | None:
        host = str(config.get("host", ""))
        if not host:
            return None
        port = int(config.get("port", EISCP_PORT))
        try:
            volume, power = await _multi(
                host,
                port,
                [
                    ("MVLQSTN", r"MVL([0-9A-F]{2})", False),
                    ("PWRQSTN", r"PWR(00|01)", True),
                ],
                required_timeout=2.0,
                grace=0.1,
            )
        except Exception:
            return None
        if volume is None and power is None:
            return None
        model = str(config.get("model", "eISCP receiver"))
        return Found(
            driver=cls.key,
            model=model,
            id=str(config.get("id", host)),
            host=host,
            config={**config, "host": host, "port": port, "model": model}
            if config.get("model")
            else {**config, "host": host, "port": port},
        )
