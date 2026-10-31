"""Onkyo-style eISCP driver, also used by recent Pioneer and Integra AVRs."""

from __future__ import annotations

import asyncio
import re
import socket
import struct
import time
from typing import Awaitable, Callable, ClassVar

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
    ModeState,
    Refused,
    Status,
    VolumeScale,
)
from .receiver_session import ReceiverSessionError, shared_session

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


async def _query(
    host: str,
    port: int,
    command: str,
    pattern: str,
    timeout: float = 2.0,
    expected: Callable[[re.Match[str]], bool] | None = None,
):
    matcher = re.compile(pattern)
    last_observed: re.Match[str] | None = None

    def classify(payload: str) -> str | None:
        nonlocal last_observed
        if _NJAPUSH.search(payload):
            return None
        match = matcher.search(payload)
        if match is None:
            return None
        last_observed = match
        return "reply" if expected is None or expected(match) else None

    try:
        frames = await shared_session(host, port, _read_packet).transact(
            [packet(f"!1{command}\r")],
            classify,
            frozenset({"reply"}),
            timeout=timeout,
        )
    except ReceiverSessionError as exc:
        if expected is not None and exc.kind == "confirmation" and last_observed:
            return last_observed
        raise
    return matcher.search(frames["reply"])


async def _multi(
    host: str,
    port: int,
    queries: list[tuple[str, str, bool]],
    required_timeout: float = 2.0,
    grace: float = 0.6,
    complete_when: Callable[[list[re.Match[str] | None]], bool] | None = None,
    grace_indices: set[int] | None = None,
) -> list[re.Match[str] | None]:
    patterns = [re.compile(pattern) for _, pattern, _ in queries]
    result: list[re.Match[str] | None] = [None] * len(queries)
    if complete_when is None:
        required_indices = {i for i, query in enumerate(queries) if query[2]}
    else:
        required_indices = set()
    optional_indices = (
        set(grace_indices)
        if grace_indices is not None
        else {i for i, query in enumerate(queries) if not query[2]}
    )
    optional_keys = frozenset(f"reply-{index}" for index in optional_indices)

    def classify(payload: str) -> str | None:
        if _NJAPUSH.search(payload):
            return None
        for index, pattern in enumerate(patterns):
            if result[index] is not None:
                continue
            match = pattern.search(payload)
            if match is None:
                continue
            was_complete = (
                complete_when(result) if complete_when is not None else False
            )
            result[index] = match
            if (
                complete_when is not None
                and not was_complete
                and complete_when(result)
            ):
                return "complete"
            return f"reply-{index}"
        return None

    required_keys = (
        frozenset({"complete"})
        if complete_when is not None
        else frozenset(f"reply-{index}" for index in required_indices)
    )
    await shared_session(host, port, _read_packet).transact(
        [packet(f"!1{command}\r") for command, _, _ in queries],
        classify,
        required_keys,
        timeout=required_timeout,
        optional=optional_keys,
        optional_grace=grace,
    )
    return result


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
        if steps != 1:
            raise DriverError("eISCP absolute volume requires volume_steps=1")
        requested_unit = str(config.get("volume_unit", "")).strip().lower()
        if requested_unit in ("percent", "percentage"):
            requested_unit = "%"
        if requested_unit not in ("", "steps", "%"):
            raise DriverError(
                "eISCP volume_unit must be 'steps' or '%' for absolute volume"
            )
        if requested_unit == "%" and maximum != 100:
            raise DriverError(
                "eISCP volume_unit='%' requires max_volume=100"
            )
        return VolumeScale(
            min=0,
            max=maximum,
            unit="%" if requested_unit == "%" else "steps",
            zero=0,
            steps_per_unit=1,
            default_cap=round(maximum * 0.4),
        )

    requested_unit = str(config.get("volume_unit", "")).strip().lower()
    if requested_unit in ("percent", "percentage"):
        requested_unit = "%"
    if requested_unit == "%":
        raise DriverError("eISCP volume_unit='%' requires absolute volume")
    if requested_unit not in ("", "steps"):
        raise DriverError(
            "eISCP volume_unit must be 'steps' or '%' for absolute volume"
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
        ConfigField(
            "volume_unit",
            "Absolute volume unit (steps, or % if the receiver displays percent)",
            required=False,
        ),
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
        except ReceiverSessionError as exc:
            raise DriverError("AVR didn't report its power state") from exc
        return Status(
            volume=int(volume.group(1), 16) if volume else None,
            muted=(mute.group(1) == "01") if mute else None,
            power=(power.group(1) == "01") if power else None,
            mode=mode.group(1).upper() if mode else None,
        )

    async def watch(
        self, on_change: Callable[[Status], None]
    ) -> Callable[[], Awaitable[None]]:
        session = shared_session(self.host, self.port, _read_packet)

        def receive(payload: str) -> None:
            if _NJAPUSH.search(payload):
                return
            payload = payload.rstrip("\r\n\x1a")
            match = re.fullmatch(
                r"!1(?:MVL([0-9A-Fa-f]{2})|AMT(00|01)|PWR(00|01)|LMD([0-9A-Fa-f]{2,4}))",
                payload,
            )
            if match is None:
                return
            volume, mute, power, mode = match.groups()
            on_change(
                Status(
                    volume=int(volume, 16) if volume is not None else None,
                    muted=(mute == "01") if mute is not None else None,
                    power=(power == "01") if power is not None else None,
                    mode=mode.upper() if mode is not None else None,
                )
            )

        session.subscribe(receive)

        async def unsubscribe() -> None:
            await session.unsubscribe(receive)

        return unsubscribe

    async def set_volume(self, raw: int) -> int:
        value = max(self.scale.min, min(self.scale.max, int(raw)))
        match = await _query(
            self.host,
            self.port,
            f"MVL{value:02X}",
            r"MVL([0-9A-F]{2})",
            expected=lambda response: int(response.group(1), 16) == value,
        )
        if match is None:
            raise DriverError("AVR didn't confirm the volume")
        return int(match.group(1), 16)

    async def set_mute(self, muted: bool) -> bool:
        match = await _query(
            self.host,
            self.port,
            "AMT01" if muted else "AMT00",
            r"AMT([0-9A-F]{2})",
            expected=lambda response: (response.group(1) == "01") is muted,
        )
        if match is None:
            raise DriverError("AVR didn't confirm mute")
        return match.group(1) == "01"

    async def set_power(self, on: bool) -> bool:
        match = await _query(
            self.host,
            self.port,
            "PWR01" if on else "PWR00",
            r"PWR(00|01)",
            expected=lambda response: (response.group(1) == "01") is on,
        )
        if match is None:
            raise DriverError("AVR didn't confirm power")
        return match.group(1) == "01"

    async def set_mode(self, code: str) -> str:
        code = str(code).strip().upper()
        if not re.fullmatch(r"[0-9A-F]{2,4}", code):
            raise Refused(f"Invalid mode code: {code}")
        try:
            match = await _query(
                self.host,
                self.port,
                f"LMD{code}",
                r"LMD([0-9A-F]{2,4})",
                expected=lambda response: response.group(1).upper() == code,
            )
        except ReceiverSessionError as exc:
            if exc.kind == "confirmation":
                raise Refused(REFUSED_MODE_MESSAGE) from exc
            raise DriverError("AVR couldn't confirm the mode") from exc
        return match.group(1).upper()

    async def list_modes(self) -> list[Mode]:
        return [Mode(code, name) for code, name in MODE_NAMES]

    async def mode_state(self, status: Status | None = None) -> ModeState:
        current_status = status if status is not None else await self.status()
        if current_status.power is False:
            return ModeState(None, (), complete=False)
        return ModeState(
            current_status.mode,
            tuple(await self.list_modes()),
            complete=False,
        )

    @classmethod
    def mode_label(cls, code: str) -> str:
        return dict(MODE_NAMES).get(str(code).strip().upper(), f"Mode {code}")

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
