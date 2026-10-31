"""Legacy Yamaha receiver driver using Yamaha Remote Control XML (YNC)."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation
from functools import partial
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

DEFAULT_PORT = 80
REQUEST_TIMEOUT = 2.0
PROBE_TIMEOUT = 2.0
CONTROL_PATH = "/YamahaRemoteControl/ctrl"
YXC_INFO_PATH = "/YamahaExtendedControl/v1/system/getDeviceInfo"

_COMMON_PROGRAMS = (
    ("Straight", "Straight"),
    ("2ch Stereo", "2ch Stereo"),
    ("5ch Stereo", "5ch Stereo"),
    ("7ch Stereo", "7ch Stereo"),
    ("Surround Decoder", "Surround Decoder"),
    ("Hall in Munich", "Hall in Munich"),
    ("Hall in Vienna", "Hall in Vienna"),
    ("Hall in Amsterdam", "Hall in Amsterdam"),
    ("Hall in USA", "Hall in USA"),
    ("Hall in Toronto", "Hall in Toronto"),
    ("Hall in Boston", "Hall in Boston"),
    ("Hall in Chicago", "Hall in Chicago"),
    ("Hall in Freiburg", "Hall in Freiburg"),
    ("Hall in Royaumont", "Hall in Royaumont"),
    ("Village Vanguard", "Village Vanguard"),
    ("Warehouse Loft", "Warehouse Loft"),
    ("Cellar Club", "Cellar Club"),
    ("The Roxy Theatre", "The Roxy Theatre"),
    ("The Bottom Line", "The Bottom Line"),
    ("Sports", "Sports"),
    ("Action Game", "Action Game"),
    ("Roleplaying Game", "Roleplaying Game"),
    ("Music Video", "Music Video"),
    ("Recital/Opera", "Recital/Opera"),
    ("Recital", "Recital"),
    ("Opera", "Opera"),
    ("Standard", "Standard"),
    ("Spectacle", "Spectacle"),
    ("Sci-Fi", "Sci-Fi"),
    ("Adventure", "Adventure"),
    ("Drama", "Drama"),
    ("Mono Movie", "Mono Movie"),
    ("Enhanced", "Enhanced"),
)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find_path(root: ET.Element, *path: str) -> ET.Element | None:
    matches = [root]
    for wanted in path:
        matches = [
            child
            for parent in matches
            for child in list(parent)
            if _local_name(child.tag).casefold() == wanted.casefold()
        ]
        if not matches:
            return None
    return matches[0]


def _first_text(root: ET.Element, name: str) -> str | None:
    for element in root.iter():
        if _local_name(element.tag).casefold() == name.casefold():
            value = (element.text or "").strip()
            if value:
                return value
    return None


def _decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value.strip())
    except (InvalidOperation, ValueError):
        return None


def _volume_db(level: ET.Element) -> Decimal | None:
    value = _decimal(_first_text(level, "Val"))
    exponent = _decimal(_first_text(level, "Exp"))
    unit = _first_text(level, "Unit")
    if value is None or exponent is None:
        return None
    if unit and unit.casefold() != "db":
        return None
    try:
        return value / (Decimal(10) ** int(exponent))
    except (ValueError, OverflowError, InvalidOperation):
        return None


def _raw_from_db(value: Decimal) -> int:
    return int((value * 2 + 161).to_integral_value())


def _bool(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().casefold()
    if normalized in ("on", "true", "1"):
        return True
    if normalized in ("off", "false", "0"):
        return False
    return None


def _power(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().casefold()
    if normalized == "on":
        return True
    if normalized in ("standby", "off"):
        return False
    return None


def _build_request(command: str, *path: str, value: str | None = None) -> bytes:
    root = ET.Element("YAMAHA_AV", {"cmd": command})
    current = root
    for name in path:
        current = ET.SubElement(current, name)
    if value is not None:
        current.text = value
    elif command == "GET":
        current.text = "GetParam"
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _post_xml(host: str, port: int, payload: bytes, timeout: float) -> ET.Element:
    if not host:
        raise DriverError("Yamaha receiver host is missing")
    if not 1 <= port <= 65535:
        raise DriverError("Yamaha receiver port must be between 1 and 65535")
    parsed_host = host
    if ":" in parsed_host and not parsed_host.startswith("["):
        parsed_host = f"[{parsed_host}]"
    url = f"http://{parsed_host}:{port}{CONTROL_PATH}"
    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "User-Agent": "AVR Control",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except Exception as exc:
        raise DriverError(f"Yamaha receiver request failed: {exc}") from exc
    try:
        root = ET.fromstring(body)
    except (ET.ParseError, TypeError) as exc:
        raise DriverError("Yamaha receiver returned invalid XML") from exc
    rc = next(
        (value for key, value in root.attrib.items() if key.casefold() == "rc"),
        None,
    )
    if rc is None:
        raise DriverError("Yamaha receiver response omitted RC")
    if rc.strip() != "0":
        raise Refused(f"Yamaha receiver refused the request (RC={rc.strip()})")
    return root


def _yxc_answers(host: str, port: int, timeout: float) -> bool:
    parsed_host = host
    if ":" in parsed_host and not parsed_host.startswith("["):
        parsed_host = f"[{parsed_host}]"
    request = urllib.request.Request(
        f"http://{parsed_host}:{port}{YXC_INFO_PATH}",
        headers={"Accept": "application/json", "User-Agent": "AVR Control"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        exc.close()
        return False
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    response_code = data.get("response_code")
    if isinstance(response_code, bool):
        return False
    if isinstance(response_code, int):
        return response_code == 0
    return isinstance(response_code, str) and response_code.strip() == "0"


def _probe_device(host: str, port: int) -> tuple[str | None, str | None] | None:
    if _yxc_answers(host, port, 0.8):
        return None
    root = _post_xml(
        host,
        port,
        _build_request("GET", "System", "Config"),
        timeout=1.2,
    )
    model = _first_text(root, "Model_Name")
    if not model:
        return None
    return model, _first_text(root, "System_ID")


def _description_urls(raw_xml: str) -> tuple[bool, bool, int]:
    try:
        root = ET.fromstring(raw_xml)
    except (ET.ParseError, TypeError):
        return False, False, DEFAULT_PORT
    has_control = False
    has_yxc = False
    port = DEFAULT_PORT
    for device in root.iter():
        if _local_name(device.tag).casefold() != "x_device":
            continue
        for element in device.iter():
            name = _local_name(element.tag).casefold()
            text = (element.text or "").strip()
            if not text:
                continue
            if name == "x_controlurl":
                has_control = True
            elif name == "x_yxccontrolurl":
                has_yxc = True
            elif name == "x_urlbase":
                try:
                    candidate = urllib.parse.urlparse(text).port
                except ValueError:
                    candidate = None
                if candidate is not None:
                    port = candidate
    return has_control, has_yxc, port


def _configured_modes(root: ET.Element) -> list[Mode]:
    candidates: list[ET.Element] = []
    for element in root.iter():
        name = _local_name(element.tag).casefold().replace("_", "")
        if name in ("soundprogramlist", "soundprograms"):
            candidates.append(element)
        elif name == "soundprogram" and list(element):
            candidates.append(element)

    for candidate in candidates:
        values: list[str] = []
        if list(candidate):
            for element in candidate.iter():
                if element is candidate or list(element):
                    continue
                value = (element.text or "").strip()
                if value and value.casefold() not in ("getparam", "invalid"):
                    values.append(value)
        else:
            raw = (candidate.text or "").strip()
            values.extend(
                value.strip()
                for value in raw.replace("\n", ",").replace("|", ",").split(",")
                if value.strip()
            )
        unique = list(dict.fromkeys(values))
        if unique:
            modes = [Mode(value, value.replace("_", " ")) for value in unique]
            if not any(mode.code.casefold() == "straight" for mode in modes):
                modes.insert(0, Mode("Straight", "Straight"))
            return modes
    return []


def _configured_volume_max(root: ET.Element) -> int | None:
    def visit(element: ET.Element, ancestors: tuple[str, ...]):
        name = _local_name(element.tag).casefold().replace("_", "")
        path = ancestors + (name,)
        is_volume_max = (
            name in ("maxvolume", "volumemax", "maxvol")
            or (name == "max" and any("volume" in ancestor for ancestor in ancestors))
        )
        if is_volume_max:
            db = _volume_db(element)
            if db is None:
                value = _decimal(_first_text(element, "Val")) or _decimal(
                    (element.text or "").strip()
                )
                if value is not None:
                    unit = _first_text(element, "Unit")
                    if (unit and unit.casefold() == "db") or -80 <= value <= 30:
                        db = value
                    elif value >= 1:
                        return int(value)
            if db is not None:
                return max(0, _raw_from_db(db))
        for child in list(element):
            result = visit(child, path)
            if result is not None:
                return result
        return None

    return visit(root, ())


def _read_model_config(host: str, port: int) -> ET.Element:
    return _post_xml(
        host,
        port,
        _build_request("GET", "System", "Config"),
        timeout=REQUEST_TIMEOUT,
    )


class YamahaYNCDriver(Driver):
    key: ClassVar[str] = "yamaha_ync"
    label: ClassVar[str] = "Yamaha (pre-MusicCast, 2010-2016)"

    def __init__(self, config: dict):
        super().__init__(config)
        self._scale = self._make_scale(194)
        self._modes: list[Mode] | None = None
        self._config_loaded = False
        self._config_lock = asyncio.Lock()

    @property
    def host(self) -> str:
        return str(self.config.get("host", "")).strip()

    @property
    def port(self) -> int:
        try:
            return int(self.config.get("port", DEFAULT_PORT))
        except (TypeError, ValueError):
            return DEFAULT_PORT

    @staticmethod
    def _make_scale(maximum: int) -> VolumeScale:
        return VolumeScale(
            min=0,
            max=max(0, maximum),
            unit="dB",
            zero=161,
            steps_per_unit=2,
            default_cap=min(101, max(0, maximum)),
        )

    @property
    def capabilities(self) -> frozenset[str]:
        return frozenset({VOLUME, MUTE, POWER, MODE})

    @property
    def scale(self) -> VolumeScale:
        return self._scale

    async def _request(self, payload: bytes, timeout: float = REQUEST_TIMEOUT):
        if not self.host:
            raise DriverError("Yamaha receiver host is missing")
        if not 1 <= self.port <= 65535:
            raise DriverError("Yamaha receiver port must be between 1 and 65535")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, partial(_post_xml, self.host, self.port, payload, timeout)
        )

    async def _ensure_config(self) -> None:
        if self._config_loaded:
            return
        async with self._config_lock:
            if self._config_loaded:
                return
            modes: list[Mode] = []
            try:
                root = await self._request(
                    _build_request("GET", "System", "Config")
                )
            except DriverError:
                root = None
            else:
                modes = _configured_modes(root)
                maximum = _configured_volume_max(root)
                if maximum is not None:
                    self._scale = self._make_scale(maximum)
            if not modes:
                try:
                    zone_config = await self._request(
                        _build_request("GET", "Main_Zone", "Config")
                    )
                except DriverError:
                    pass
                else:
                    modes = _configured_modes(zone_config)
                    maximum = _configured_volume_max(zone_config)
                    if maximum is not None:
                        self._scale = self._make_scale(maximum)
            self._modes = modes or [Mode(*entry) for entry in _COMMON_PROGRAMS]
            self._config_loaded = True

    async def status(self) -> Status:
        await self._ensure_config()
        root = await self._request(
            _build_request("GET", "Main_Zone", "Basic_Status")
        )
        basic = _find_path(root, "Main_Zone", "Basic_Status")
        if basic is None:
            basic = root
        power = _power(_first_text(basic, "Power"))
        level = _find_path(basic, "Volume", "Lvl")
        db = _volume_db(level) if level is not None else None
        raw_volume = _raw_from_db(db) if db is not None else None
        if raw_volume is None:
            raise DriverError("Yamaha receiver returned an invalid volume level")

        volume = _find_path(basic, "Volume")
        mute = _bool(_first_text(volume if volume is not None else basic, "Mute"))
        current = _find_path(basic, "Surround", "Program_Sel", "Current")
        mode_tree = current if current is not None else basic
        straight = _bool(_first_text(mode_tree, "Straight"))
        sound_program = _first_text(mode_tree, "Sound_Program")
        mode = "Straight" if straight else sound_program
        return Status(
            volume=raw_volume,
            muted=mute,
            power=power,
            mode=mode,
        )

    async def set_volume(self, raw: int) -> int:
        await self._ensure_config()
        value = max(self.scale.min, min(self.scale.max, int(raw)))
        wire_value = (value - self.scale.zero) * 5
        root = ET.Element("YAMAHA_AV", {"cmd": "PUT"})
        main_zone = ET.SubElement(root, "Main_Zone")
        volume = ET.SubElement(main_zone, "Volume")
        level = ET.SubElement(volume, "Lvl")
        ET.SubElement(level, "Val").text = str(wire_value)
        ET.SubElement(level, "Exp").text = "1"
        ET.SubElement(level, "Unit").text = "dB"
        await self._request(ET.tostring(root, encoding="utf-8", xml_declaration=True))
        confirmed = await self.status()
        if confirmed.volume is None:
            raise DriverError("Yamaha receiver didn't confirm the volume")
        return confirmed.volume

    async def set_mute(self, muted: bool) -> bool:
        await self._request(
            _build_request(
                "PUT",
                "Main_Zone",
                "Volume",
                "Mute",
                value="On" if muted else "Off",
            )
        )
        confirmed = (await self.status()).muted
        if confirmed is None:
            raise DriverError("Yamaha receiver didn't confirm mute")
        return confirmed

    async def set_power(self, on: bool) -> bool:
        await self._request(
            _build_request(
                "PUT",
                "Main_Zone",
                "Power_Control",
                "Power",
                value="On" if on else "Standby",
            )
        )
        confirmed = (await self.status()).power
        if confirmed is None:
            raise DriverError("Yamaha receiver didn't confirm power")
        return confirmed

    async def set_mode(self, code: str) -> str:
        is_straight = code.casefold() == "straight"
        root = ET.Element("YAMAHA_AV", {"cmd": "PUT"})
        main_zone = ET.SubElement(root, "Main_Zone")
        surround = ET.SubElement(main_zone, "Surround")
        program_selection = ET.SubElement(surround, "Program_Sel")
        current = ET.SubElement(program_selection, "Current")
        straight = ET.SubElement(current, "Straight")
        straight.text = "On" if is_straight else "Off"
        if not is_straight:
            program = ET.SubElement(current, "Sound_Program")
            program.text = code
        await self._request(ET.tostring(root, encoding="utf-8", xml_declaration=True))
        confirmed = (await self.status()).mode
        if confirmed is None:
            raise DriverError("Yamaha receiver didn't confirm the sound program")
        return confirmed

    async def list_modes(self) -> list[Mode]:
        await self._ensure_config()
        return list(self._modes or [])

    def mode_name(self, code: str) -> str:
        if code.casefold() == "straight":
            return "Straight"
        return code.replace("_", " ").replace("-", " ").title()

    @classmethod
    async def discover(cls, timeout: float) -> list[Found]:
        targets = (
            "urn:schemas-upnp-org:device:MediaRenderer:1",
            "upnp:rootdevice",
        )
        try:
            responses = await asyncio.gather(
                *(ssdp.search(st, max(0.0, timeout)) for st in targets),
                return_exceptions=True,
            )
        except Exception:
            return []

        candidates: dict[tuple[str, str], dict] = {}
        for result in responses:
            if isinstance(result, Exception):
                continue
            for item in result:
                location = str(item.get("location", ""))
                ip = str(item.get("ip", ""))
                if location and ip:
                    candidates[(location, ip)] = item

        async def describe(item: dict) -> Found | None:
            try:
                description = await ssdp.fetch_description(item["location"])
            except Exception:
                return None
            if "yamaha" not in str(description.get("manufacturer", "")).casefold():
                return None
            has_control, has_yxc, port = _description_urls(
                str(description.get("raw_xml", ""))
            )
            if not has_control or has_yxc:
                return None
            host = str(item["ip"])
            return Found(
                driver=cls.key,
                model=str(description.get("modelName", "") or "Yamaha receiver"),
                id=str(description.get("UDN", "") or host),
                host=host,
                config={"host": host, **({"port": port} if port != DEFAULT_PORT else {})},
            )

        found = await asyncio.gather(*(describe(item) for item in candidates.values()))
        by_id: dict[str, Found] = {}
        for item in found:
            if item is not None:
                by_id[item.id] = item
        return list(by_id.values())

    @classmethod
    async def probe(cls, config: dict) -> Found | None:
        host = str(config.get("host", "")).strip()
        try:
            port = int(config.get("port", DEFAULT_PORT))
        except (TypeError, ValueError):
            return None
        if not host or not 1 <= port <= 65535:
            return None
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(None, _probe_device, host, port)
        try:
            result = await asyncio.wait_for(future, timeout=PROBE_TIMEOUT)
        except Exception:
            return None
        if result is None:
            return None
        model, system_id = result
        if not model:
            return None
        return Found(
            driver=cls.key,
            model=model,
            id=str(system_id or config.get("id") or host),
            host=host,
            config={**config, "host": host},
        )
