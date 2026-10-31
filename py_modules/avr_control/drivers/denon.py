"""Denon / Marantz AVR driver for the port-23 ASCII control protocol."""

from __future__ import annotations

import asyncio
import re
import urllib.error
import urllib.parse
import urllib.request
import weakref
import xml.etree.ElementTree as ET
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
_HTTP_TIMEOUT = 0.8
_HTTP_STATUS_PATHS = (
    "/goform/formMainZone_MainZoneXmlStatus.xml",
    "/goform/formMainZone_MainZoneXml.xml",
)
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


class TelnetTransport:
    """Short-lived Telnet connections, serialized for single-client receivers."""

    def __init__(self, host: str, port: int, lock: asyncio.Lock):
        self.host = host
        self.port = port
        self.lock = lock

    async def query(self, commands: tuple[str, ...]) -> list[str]:
        async with self.lock:
            reader, writer = await _open(self.host, self.port)
            lines: list[str] = []
            try:
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
                    if line:
                        lines.append(line)
                        if self._query_complete(commands, lines):
                            break
            except DriverError:
                raise
            except Exception as exc:
                raise DriverError(
                    f"Denon/Marantz status request failed: {exc}"
                ) from exc
            finally:
                await _close(writer)
            return lines

    async def send(self, command: str) -> list[str]:
        async with self.lock:
            reader, writer = await _open(self.host, self.port)
            lines: list[str] = []
            matches = self._reply_matcher(command)
            try:
                writer.write(command.encode("ascii") + b"\r")
                await writer.drain()
                deadline = asyncio.get_running_loop().time() + _REPLY_TIMEOUT
                while True:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise asyncio.TimeoutError()
                    line = await _read_line(reader, remaining)
                    if not line:
                        continue
                    lines.append(line)
                    if matches(line):
                        grace_deadline = min(
                            deadline,
                            asyncio.get_running_loop().time() + 0.07,
                        )
                        while grace_deadline > asyncio.get_running_loop().time():
                            try:
                                line = await _read_line(
                                    reader,
                                    grace_deadline - asyncio.get_running_loop().time(),
                                )
                            except asyncio.TimeoutError:
                                break
                            if line:
                                lines.append(line)
                        return lines
            except asyncio.TimeoutError:
                raise
            except DriverError:
                raise
            except Exception as exc:
                raise DriverError(
                    f"Denon/Marantz command {command!r} failed: {exc}"
                ) from exc
            finally:
                await _close(writer)

    @staticmethod
    def _reply_matcher(command: str):
        if command.startswith("PW"):
            return _POWER_LINE.fullmatch
        if command.startswith("MV"):
            return _VOLUME_TOKEN.fullmatch
        if command.startswith("MU"):
            return _MUTE_LINE.fullmatch
        if command.startswith("MS"):
            return _MODE_LINE.fullmatch
        return lambda _line: False

    @staticmethod
    def _query_complete(commands: tuple[str, ...], lines: list[str]) -> bool:
        power = any(_POWER_LINE.fullmatch(line) for line in lines)
        if not power:
            return False
        if len(commands) == 1:
            return True
        if any(line == "PWSTANDBY" for line in lines):
            return True
        return (
            any(_VOLUME_TOKEN.fullmatch(line) for line in lines)
            and any(_MUTE_LINE.fullmatch(line) for line in lines)
            and any(_MODE_LINE.fullmatch(line) for line in lines)
        )


class HttpTransport:
    """Denon web control via the main-zone XML pages and iPhone direct commands."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port

    async def query(self, _commands: tuple[str, ...]) -> list[str]:
        last_error: DriverError | None = None
        for path in _HTTP_STATUS_PATHS:
            try:
                return [await self._request(path)]
            except DriverError as exc:
                last_error = exc
        raise last_error or DriverError("Denon/Marantz HTTP status request failed")

    async def send(self, command: str) -> list[str]:
        path = "/goform/formiPhoneAppDirect.xml?" + urllib.parse.quote(
            command, safe=""
        )
        return [await self._request(path)]

    async def _request(self, path: str) -> str:
        host = self.host
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        url = f"http://{host}:{self.port}{path}"
        try:
            return await asyncio.to_thread(self._request_sync, url)
        except DriverError:
            raise
        except Exception as exc:
            raise DriverError(
                f"Can't reach Denon/Marantz HTTP interface at "
                f"{self.host}:{self.port}: {exc}"
            ) from exc

    @staticmethod
    def _request_sync(url: str) -> str:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "AVR-Control"},
        )
        try:
            with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT) as response:
                return response.read(2 * 1024 * 1024).decode("utf-8", errors="replace")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise DriverError(str(exc)) from exc


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
        self._transport_adapter: TelnetTransport | HttpTransport | None = None

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

    def _http_ports(self) -> list[int]:
        if "http_port" in self.config:
            try:
                port = int(self.config["http_port"])
            except (TypeError, ValueError) as exc:
                raise DriverError("Invalid Denon HTTP port") from exc
            if not 1 <= port <= 65535:
                raise DriverError("Invalid Denon HTTP port")
            return [port] + [
                candidate for candidate in (80, 8080) if candidate != port
            ]
        return [80, 8080]

    def _remember_transport(
        self, adapter: TelnetTransport | HttpTransport, kind: str, port: int
    ) -> None:
        self._transport_adapter = adapter
        self.config["transport"] = kind
        if kind == "http":
            self.config["http_port"] = port

    async def _get_transport(self) -> TelnetTransport | HttpTransport:
        if self._transport_adapter is not None:
            return self._transport_adapter

        configured = str(self.config.get("transport", "")).lower()
        if configured not in {"", "telnet", "http"}:
            raise DriverError("Invalid Denon transport")

        if configured != "http":
            telnet = TelnetTransport(self.host, self.port, self._operation_lock())
            try:
                lines = await telnet.query(("PW?",))
                if not any(_POWER_LINE.fullmatch(line) for line in lines):
                    raise DriverError("Denon/Marantz didn't report its power state")
                self._remember_transport(telnet, "telnet", self.port)
                return telnet
            except Exception:
                pass

        for port in self._http_ports():
            http = HttpTransport(self.host, port)
            try:
                body = (await http.query(("PW?",)))[0]
                status = self._parse_http_status(body)
                if status.power is None:
                    continue
                self._remember_transport(http, "http", port)
                return http
            except Exception:
                continue

        raise DriverError(
            f"Can't reach Denon/Marantz receiver at {self.host} via Telnet or HTTP"
        )

    async def _send_command(
        self, command: str
    ) -> tuple[TelnetTransport | HttpTransport, list[str]]:
        transport = await self._get_transport()
        try:
            return transport, await transport.send(command)
        except Exception as first_error:
            if not isinstance(transport, TelnetTransport):
                raise
            self._transport_adapter = None
            for port in self._http_ports():
                http = HttpTransport(self.host, port)
                try:
                    lines = await http.send(command)
                    self._remember_transport(http, "http", port)
                    return http, lines
                except Exception:
                    continue
            raise first_error

    def _parse_http_status(self, body: str) -> Status:
        try:
            root = ET.fromstring(body)
        except ET.ParseError as exc:
            raise DriverError("Denon/Marantz returned invalid status XML") from exc

        values: dict[str, str] = {}
        for element in root.iter():
            name = element.tag.rsplit("}", 1)[-1].lower()
            if not name:
                continue
            child_value = next(
                (
                    child.text
                    for child in element
                    if child.tag.rsplit("}", 1)[-1].lower() == "value"
                    and child.text
                ),
                None,
            )
            value = child_value or element.text
            if value and value.strip():
                values[name] = value.strip()

        power_value = values.get("power", "").upper()
        if power_value not in {"ON", "OFF", "STANDBY", "1", "0", "TRUE", "FALSE"}:
            raise DriverError("Denon/Marantz receiver didn't report its power state")
        power = power_value in {"ON", "1", "TRUE"}
        if not power:
            return Status(power=False)

        volume: int | None = None
        try:
            volume_value = float(values["mastervolume"])
            display = values.get("volumedisplay", "relative").lower()
            raw = (
                round(volume_value * 2)
                if display == "absolute"
                else round((volume_value + 80) * 2)
            )
            volume = max(0, min(self.scale.max, raw))
        except (KeyError, TypeError, ValueError):
            pass

        mute_value = values.get("mute", "").lower()
        muted = (
            True
            if mute_value in {"on", "true", "1"}
            else False
            if mute_value in {"off", "false", "0"}
            else None
        )
        mode_value = next(
            (
                values[name]
                for name in ("surrmode", "surroundmode", "soundmode")
                if values.get(name)
            ),
            None,
        )
        mode = _canonical_mode(mode_value) if mode_value else None
        return Status(volume=volume, muted=muted, power=True, mode=mode)

    async def status(self) -> Status:
        commands = ("PW?", "MV?", "MU?", "MS?")
        configured = str(self.config.get("transport", "")).lower()
        if self._transport_adapter is not None:
            if isinstance(self._transport_adapter, HttpTransport):
                try:
                    body = (await self._transport_adapter.query(commands))[0]
                    return self._parse_http_status(body)
                except Exception:
                    self._transport_adapter = None
                    configured = "http"
            else:
                try:
                    return self._parse_telnet_status(
                        await self._transport_adapter.query(commands)
                    )
                except Exception:
                    self._transport_adapter = None
                    configured = "http"

        if configured not in {"", "telnet", "http"}:
            raise DriverError("Invalid Denon transport")
        if configured != "http":
            telnet = TelnetTransport(self.host, self.port, self._operation_lock())
            try:
                status = self._parse_telnet_status(await telnet.query(commands))
                self._remember_transport(telnet, "telnet", self.port)
                return status
            except Exception:
                pass

        for port in self._http_ports():
            http = HttpTransport(self.host, port)
            try:
                body = (await http.query(commands))[0]
                status = self._parse_http_status(body)
                self._remember_transport(http, "http", port)
                return status
            except Exception:
                continue
        raise DriverError(
            f"Can't reach Denon/Marantz receiver at {self.host} via Telnet or HTTP"
        )

    def _parse_telnet_status(self, lines: list[str]) -> Status:
        power: bool | None = None
        volume: int | None = None
        muted: bool | None = None
        mode: str | None = None
        for line in lines:
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

        if power is None:
            raise DriverError("Denon/Marantz receiver didn't report its power state")
        return Status(volume=volume, muted=muted, power=power, mode=mode)

    async def set_volume(self, raw: int) -> int:
        try:
            value = max(self.scale.min, min(self.scale.max, int(raw)))
        except (TypeError, ValueError) as exc:
            raise DriverError("Invalid Denon volume value") from exc
        try:
            transport, lines = await self._send_command(f"MV{_encode_volume(value)}")
        except asyncio.TimeoutError as exc:
            raise DriverError("Denon/Marantz didn't confirm the volume") from exc
        for line in lines:
            self._record_max_volume(line)

        if isinstance(transport, HttpTransport):
            confirmed = (await self.status()).volume
            if confirmed is None:
                raise DriverError("Denon/Marantz didn't confirm the volume")
            if confirmed < value and value == self.scale.max:
                self._scale = VolumeScale(
                    min=self.scale.min,
                    max=confirmed,
                    unit="dB",
                    zero=self.scale.zero,
                    steps_per_unit=self.scale.steps_per_unit,
                    default_cap=min(100, confirmed),
                )
            return confirmed

        reply = next((line for line in lines if _VOLUME_TOKEN.fullmatch(line)), None)
        match = _VOLUME_TOKEN.fullmatch(reply or "")
        if match is None:
            raise DriverError("Denon/Marantz returned an invalid volume")
        try:
            return _decode_volume(match.group(1))
        except ValueError as exc:
            raise DriverError("Denon/Marantz returned an invalid volume") from exc

    async def set_mute(self, muted: bool) -> bool:
        try:
            transport, lines = await self._send_command(
                "MUON" if muted else "MUOFF"
            )
        except asyncio.TimeoutError as exc:
            raise DriverError("Denon/Marantz didn't confirm mute") from exc
        if isinstance(transport, HttpTransport):
            confirmed = (await self.status()).muted
            if confirmed is None or confirmed != muted:
                raise DriverError("Denon/Marantz didn't confirm mute")
            return confirmed
        reply = next((line for line in lines if _MUTE_LINE.fullmatch(line)), "")
        return reply == "MUON"

    async def set_power(self, on: bool) -> bool:
        command = "PWON" if on else "PWSTANDBY"
        try:
            transport, lines = await self._send_command(command)
        except asyncio.TimeoutError as exc:
            raise DriverError("Denon/Marantz didn't confirm power") from exc
        if isinstance(transport, HttpTransport):
            confirmed = (await self.status()).power
            if confirmed is None or confirmed != on:
                raise DriverError("Denon/Marantz didn't confirm power")
            return confirmed
        reply = next((line for line in lines if _POWER_LINE.fullmatch(line)), "")
        return reply == "PWON"

    async def set_mode(self, code: str) -> str:
        normalized = _canonical_mode(str(code))
        try:
            transport, lines = await self._send_command(f"MS{normalized}")
        except asyncio.TimeoutError as exc:
            raise Refused(
                "The receiver didn't accept that listening mode (it may be unavailable "
                "for the current input or signal)"
            ) from exc
        if isinstance(transport, HttpTransport):
            actual = (await self.status()).mode
            if actual is None or actual != normalized:
                raise Refused(
                    "The receiver didn't accept that listening mode (it may be unavailable "
                    "for the current input or signal)"
                )
            return actual
        reply = next((line for line in lines if _MODE_LINE.fullmatch(line)), "")
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
            api_config = {"host": item["ip"]}
            try:
                description_port = urllib.parse.urlsplit(item["location"]).port
            except ValueError:
                description_port = None
            if description_port:
                api_config["http_port"] = description_port
            api_config["model"] = model_name or friendly_name or "Denon/Marantz"
            api_config["id"] = udn or usn or item["ip"]
            reachable = await cls.probe(api_config)
            if reachable is None:
                return None
            return Found(
                driver=cls.key,
                model=model_name or friendly_name or "Denon/Marantz",
                id=udn or usn or item["ip"],
                host=item["ip"],
                config=reachable.config,
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
            driver = cls({**config, "host": host, "port": port})
            await driver._get_transport()
        except Exception:
            return None
        return Found(
            driver=cls.key,
            model=str(config.get("model") or "Denon/Marantz"),
            id=str(config.get("id") or f"denon-{host}"),
            host=host,
            config={**driver.config, "host": host, "port": port},
        )
