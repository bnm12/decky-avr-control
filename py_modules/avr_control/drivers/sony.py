"""Sony receiver driver using the ScalarWebAPI Audio Control API."""

from __future__ import annotations

import asyncio
import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
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
    Unsupported,
    VolumeScale,
)

DEFAULT_PORT = 10000
REQUEST_TIMEOUT = 2.0
SSDP_TARGET = "urn:schemas-sony-com:service:ScalarWebAPI:1"

_METHOD_VERSIONS = {
    "getPowerStatus": "1.1",
    "setPowerStatus": "1.1",
    "getSystemInformation": "1.0",
    "getInterfaceInformation": "1.0",
    "getVolumeInformation": "1.1",
    "setAudioVolume": "1.0",
    "setAudioMute": "1.0",
    "getSoundSettings": "1.1",
    "setSoundSettings": "1.1",
}


def _http_post(url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "AVR Control",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            body = response.read()
    except Exception as exc:
        raise DriverError(f"Sony receiver request failed: {exc}") from exc
    try:
        result = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DriverError("Sony receiver returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise DriverError("Sony receiver returned an invalid response")
    return result


def _int(value, default: int | None = None) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default


def _bool(value) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("true", "on", "1", "active"):
            return True
        if normalized in ("false", "off", "0", "standby"):
            return False
    return None


def _items(result) -> list[dict]:
    """Flatten the array-shaped ScalarWebAPI result into result objects."""
    if isinstance(result, dict):
        return [result]
    if not isinstance(result, list):
        return []
    items: list[dict] = []
    for item in result:
        if isinstance(item, dict):
            items.append(item)
        elif isinstance(item, list):
            items.extend(value for value in item if isinstance(value, dict))
    return items


def _first_result(result) -> dict:
    items = _items(result)
    return items[0] if items else {}


def _power_state(result) -> bool | None:
    state = _first_result(result).get("status")
    return _bool(state)


def _volume_info(result) -> dict:
    return _first_result(result)


def _sound_setting(result) -> dict:
    for item in _items(result):
        if str(item.get("target", "")).lower() == "soundfield":
            return item
    return {}


def _modes(setting: dict) -> list[Mode]:
    candidates = setting.get("candidate", [])
    if not isinstance(candidates, list):
        return []
    modes: list[Mode] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        code = candidate.get("value")
        if not isinstance(code, (str, int, float)):
            continue
        code = str(code)
        available = _bool(candidate.get("isAvailable"))
        if not code or available is False or code in seen:
            continue
        title = candidate.get("title")
        modes.append(Mode(code, str(title) if title else _pretty_mode(code)))
        seen.add(code)
    return modes


def _pretty_mode(code: str) -> str:
    return code.replace("_", " ").replace("-", " ").strip().title() or code


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _description_data(raw_xml: str) -> dict[str, str] | None:
    try:
        root = ET.fromstring(raw_xml)
    except (ET.ParseError, TypeError):
        return None
    values: dict[str, str] = {}
    service_types: list[str] = []
    in_service_list = False
    for element in root.iter():
        name = _local_name(element.tag)
        text = (element.text or "").strip()
        if name in ("modelName", "UDN", "manufacturer") and text:
            values.setdefault(name, text)
        if name == "X_ScalarWebAPI_ServiceList":
            in_service_list = True
        elif in_service_list and name == "X_ScalarWebAPI_ServiceType" and text:
            service_types.append(text)
        elif in_service_list and name == "X_ScalarWebAPI_BaseURL":
            in_service_list = False
        if name == "X_ScalarWebAPI_BaseURL" and text:
            values["base_url"] = text
    if not values.get("base_url"):
        return None
    values["has_video_service"] = str(
        any("video" in service.lower() for service in service_types)
    )
    return values


def _audio_category(value) -> bool:
    if not isinstance(value, str):
        return False
    category = "".join(char for char in value.lower() if char.isalnum())
    if any(token in category for token in ("video", "television", "tv")):
        return False
    return any(
        token in category
        for token in ("audio", "hometheater", "soundbar", "speaker", "receiver")
    )


class SonyDriver(Driver):
    key: ClassVar[str] = "sony"
    label: ClassVar[str] = "Sony (STR-DN / ES receivers, soundbars)"

    def __init__(self, config: dict):
        super().__init__(config)
        self._scale = VolumeScale(
            min=0, max=100, unit="%", zero=0, steps_per_unit=1, default_cap=40
        )
        self._capabilities: frozenset[str] = frozenset(
            {VOLUME, MUTE, POWER, MODE}
        )

    @property
    def host(self) -> str:
        return str(self.config.get("host", "")).strip()

    @property
    def port(self) -> int:
        try:
            return int(self.config.get("port", DEFAULT_PORT))
        except (TypeError, ValueError):
            return DEFAULT_PORT

    @property
    def capabilities(self) -> frozenset[str]:
        return self._capabilities

    @property
    def scale(self) -> VolumeScale:
        return self._scale

    def _url(self, service: str) -> str:
        if not self.host:
            raise DriverError("Sony receiver host is missing")
        if not 1 <= self.port <= 65535:
            raise DriverError("Sony receiver port must be between 1 and 65535")
        host = self.host
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"http://{host}:{self.port}/sony/{service}"

    async def _api(self, service: str, method: str, params: dict | None = None):
        payload = {
            "method": method,
            "id": 1,
            "params": [params] if params is not None else [],
            "version": _METHOD_VERSIONS[method],
        }
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None, partial(_http_post, self._url(service), payload)
        )
        if "error" in response:
            error = response["error"]
            if isinstance(error, list):
                code = _int(error[0]) if error else None
                message = str(error[1]) if len(error) > 1 else "Unknown error"
            elif isinstance(error, dict):
                code = _int(error.get("code"))
                message = str(error.get("message", "Unknown error"))
            else:
                code = None
                message = str(error)
            detail = f"Sony ScalarWebAPI error {code}: {message}"
            if code == 40000:
                raise Refused(f"Sony receiver is off or refused the request ({detail})")
            if code in (3, 12):
                raise Unsupported(f"Sony receiver doesn't support {method} ({detail})")
            raise DriverError(detail)
        if "result" not in response:
            raise DriverError("Sony receiver response omitted result")
        return response["result"]

    def _set_volume_scale(self, info: dict) -> None:
        raw_min = _int(info.get("minVolume"))
        raw_max = _int(info.get("maxVolume"))
        if raw_min is None or raw_max is None or raw_max <= raw_min:
            return
        default_cap = raw_min + round((raw_max - raw_min) * 0.4)
        self._scale = VolumeScale(
            min=raw_min,
            max=raw_max,
            unit="%",
            zero=0,
            steps_per_unit=1,
            default_cap=max(raw_min, min(raw_max, default_cap)),
        )

    async def _get_power(self) -> bool | None:
        return _power_state(await self._api("system", "getPowerStatus"))

    async def _get_volume_info(self) -> dict:
        info = _volume_info(
            await self._api(
                "audio", "getVolumeInformation", {"output": ""}
            )
        )
        self._set_volume_scale(info)
        return info

    async def _get_sound_setting(self) -> dict:
        result = await self._api(
            "audio", "getSoundSettings", {"target": "soundField"}
        )
        return _sound_setting(result)

    def _remove_capabilities(self, *capabilities: str) -> None:
        self._capabilities = frozenset(
            self._capabilities.difference(capabilities)
        )

    def _require_capability(self, capability: str) -> None:
        if capability not in self._capabilities:
            labels = {
                VOLUME: "volume",
                MUTE: "mute",
                POWER: "power control",
                MODE: "listening modes",
            }
            raise Unsupported(
                f"This Sony receiver doesn't support {labels[capability]}"
            )

    @staticmethod
    def _is_power_off_error(error: Refused) -> bool:
        return "40000" in str(error)

    async def status(self) -> Status:
        try:
            power = await self._get_power()
        except Unsupported:
            self._remove_capabilities(POWER)
            power = None
        except Refused as exc:
            if self._is_power_off_error(exc):
                return Status(power=False)
            raise

        if power is False:
            return Status(power=False)

        volume = muted = None
        mode = None
        try:
            info = await self._get_volume_info()
            volume = _int(info.get("volume"))
            muted = _bool(info.get("mute"))
            if volume is None:
                self._remove_capabilities(VOLUME)
            if muted is None:
                self._remove_capabilities(MUTE)
        except Unsupported:
            self._remove_capabilities(VOLUME, MUTE)
        except Refused as exc:
            if self._is_power_off_error(exc):
                return Status(power=False)
            raise

        try:
            setting = await self._get_sound_setting()
            mode = setting.get("currentValue")
            mode = str(mode) if mode is not None else None
            if not _modes(setting):
                self._remove_capabilities(MODE)
        except Unsupported:
            self._remove_capabilities(MODE)
        except Refused as exc:
            if self._is_power_off_error(exc):
                return Status(power=False)
            raise

        return Status(volume=volume, muted=muted, power=power, mode=mode)

    async def set_volume(self, raw: int) -> int:
        self._require_capability(VOLUME)
        await self._get_volume_info()
        value = max(self.scale.min, min(self.scale.max, int(raw)))
        await self._api(
            "audio",
            "setAudioVolume",
            {"volume": str(value), "output": ""},
        )
        confirmed = _int((await self._get_volume_info()).get("volume"))
        if confirmed is None:
            raise DriverError("Sony receiver didn't confirm the volume")
        return confirmed

    async def set_mute(self, muted: bool) -> bool:
        self._require_capability(MUTE)
        await self._api(
            "audio",
            "setAudioMute",
            {"mute": "on" if muted else "off"},
        )
        confirmed = _bool((await self._get_volume_info()).get("mute"))
        if confirmed is None:
            raise DriverError("Sony receiver didn't confirm mute")
        return confirmed

    async def set_power(self, on: bool) -> bool:
        self._require_capability(POWER)
        await self._api(
            "system",
            "setPowerStatus",
            {"status": "active" if on else "off"},
        )
        confirmed = await self._get_power()
        if confirmed is None:
            raise DriverError("Sony receiver didn't confirm power")
        return confirmed

    async def set_mode(self, code: str) -> str:
        self._require_capability(MODE)
        await self._api(
            "audio",
            "setSoundSettings",
            {"settings": [{"target": "soundField", "value": str(code)}]},
        )
        setting = await self._get_sound_setting()
        confirmed = setting.get("currentValue")
        if confirmed is None:
            raise DriverError("Sony receiver didn't confirm the sound field")
        return str(confirmed)

    async def list_modes(self) -> list[Mode]:
        return _modes(await self._get_sound_setting())

    def mode_name(self, code: str) -> str:
        return _pretty_mode(code)

    @classmethod
    async def probe(cls, config: dict) -> Found | None:
        host = str(config.get("host", "")).strip()
        if not host:
            return None
        driver = cls({**config, "host": host})
        try:
            interface, system = await asyncio.wait_for(
                asyncio.gather(
                    driver._api("system", "getInterfaceInformation"),
                    driver._api("system", "getSystemInformation"),
                    return_exceptions=True,
                ),
                timeout=REQUEST_TIMEOUT + 0.2,
            )
        except (asyncio.TimeoutError, DriverError, ValueError):
            return None
        if isinstance(interface, Exception):
            return None
        interface_info = _first_result(interface)
        if not _audio_category(interface_info.get("productCategory")):
            return None
        system_info = (
            _first_result(system) if not isinstance(system, Exception) else {}
        )
        model = str(
            interface_info.get("modelName")
            or system_info.get("model")
            or ""
        ).strip()
        if not model:
            return None
        device_id = str(
            system_info.get("macAddr")
            or system_info.get("serialNumber")
            or config.get("id")
            or host
        )
        return Found(
            driver=cls.key,
            model=model,
            id=device_id,
            host=host,
            config={**config, "host": host},
        )

    @classmethod
    async def discover(cls, timeout: float) -> list[Found]:
        try:
            responses = await ssdp.search(SSDP_TARGET, max(0.0, timeout))
        except Exception:
            return []

        candidates: dict[tuple[str, str], dict] = {}
        for item in responses:
            location = str(item.get("location", "")).strip()
            host = str(item.get("ip", "")).strip()
            if location and host:
                candidates[(location, host)] = item

        async def describe(item: dict) -> Found | None:
            try:
                description = await ssdp.fetch_description(item["location"])
            except Exception:
                return None
            data = _description_data(str(description.get("raw_xml", "")))
            if not data or data.get("has_video_service") == "True":
                return None
            try:
                base_url = urllib.parse.urlparse(data["base_url"])
            except ValueError:
                return None
            if base_url.scheme.lower() != "http" or not base_url.hostname:
                return None
            try:
                port = base_url.port or (443 if base_url.scheme == "https" else 80)
            except ValueError:
                return None
            host = str(item["ip"])
            driver = cls({"host": host, "port": port})
            device_id = data.get("UDN", "") or host
            try:
                system = _first_result(
                    await driver._api("system", "getSystemInformation")
                )
                device_id = str(
                    system.get("macAddr")
                    or system.get("serialNumber")
                    or device_id
                )
            except DriverError:
                pass
            return Found(
                driver=cls.key,
                model=str(
                    description.get("modelName")
                    or data.get("modelName")
                    or "Sony receiver"
                ),
                id=device_id,
                host=host,
                config={"host": host, "port": port},
            )

        results = await asyncio.gather(
            *(describe(item) for item in candidates.values())
        )
        by_id: dict[str, Found] = {}
        for found in results:
            if found is not None:
                by_id[found.id] = found
        return list(by_id.values())
