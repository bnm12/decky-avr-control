"""Yamaha MusicCast receiver driver using Yamaha Extended Control (YXC)."""

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

DEFAULT_PORT = 80
REQUEST_TIMEOUT = 2.0
API_ROOT = "/YamahaExtendedControl/v1"
_ERROR_MEANINGS = {
    1: "Device is initializing",
    2: "Internal error",
    3: "Invalid Request",
    4: "Invalid Parameter",
    5: "Guarded",
    6: "Timeout",
    99: "Firmware update is in progress",
}

_MODE_NAMES = {
    "straight": "Straight",
    "7ch_stereo": "7ch Stereo",
    "surr_decoder": "Surround Decoder",
    "all_ch_stereo": "All-Channel Stereo",
    "enhanced": "Enhanced",
    "sci-fi": "Sci-Fi",
    "standard": "Standard",
    "drama": "Drama",
    "mono_movie": "Mono Movie",
    "adventure": "Adventure",
    "spectacle": "Spectacle",
    "music_video": "Music Video",
    "hall_in_munich": "Hall in Munich",
    "hall_in_vienna": "Hall in Vienna",
    "chamber": "Chamber",
    "village_vanguard": "Village Vanguard",
    "warehouse_loft": "Warehouse Loft",
    "arena": "Arena",
    "disco": "Disco",
    "recital_opera": "Recital/Opera",
    "recital": "Recital",
    "opera": "Opera",
}


def _fallback_scale() -> VolumeScale:
    """Common Yamaha 0–161 raw mapping; model-specific features replace it."""
    zero = 161
    return VolumeScale(
        min=0,
        max=161,
        unit="dB",
        zero=zero,
        steps_per_unit=2,
        default_cap=zero - 30 * 2,
    )


def _int(value, default: int | None = None) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default


def _number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _range_entries(zone: dict) -> dict[str, dict]:
    raw = zone.get("range_step", [])
    entries: dict[str, dict] = {}
    if not isinstance(raw, list):
        return entries
    for value in raw:
        if not isinstance(value, dict):
            continue
        key = value.get("id")
        if isinstance(key, str) and key:
            entries[key.lower()] = value
    return entries


def _function_names(zone: dict) -> set[str]:
    raw = zone.get("func_list", [])
    if not isinstance(raw, list):
        return set()
    return {entry.lower() for entry in raw if isinstance(entry, str)}


def _programs(zone: dict) -> list[Mode]:
    raw = zone.get("sound_program_list", [])
    if not isinstance(raw, list):
        return []
    modes: list[Mode] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, str):
            continue
        if entry and entry not in seen:
            modes.append(Mode(entry, _pretty_mode(entry)))
            seen.add(entry)
    return modes


def _pretty_mode(code: str) -> str:
    if code in _MODE_NAMES:
        return _MODE_NAMES[code]
    normalized = code.replace("_", " ").replace("-", " ").strip()
    return normalized.title() if normalized else code


def _bool(value) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        value = value.strip().lower()
        if value in ("true", "on", "1"):
            return True
        if value in ("false", "off", "0"):
            return False
    return None


def _power(value) -> bool | None:
    if not isinstance(value, str):
        return _bool(value)
    value = value.strip().lower()
    if value == "on":
        return True
    if value in ("standby", "off"):
        return False
    return None


def _has_yxc_control_url(raw_xml: str) -> bool:
    """Only the YXC-specific URL claims a device; legacy X_controlURL is separate."""
    try:
        root = ET.fromstring(raw_xml)
    except (ET.ParseError, TypeError):
        return False
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "X_device":
            continue
        for child in element.iter():
            if (
                child.tag.rsplit("}", 1)[-1] == "X_yxcControlURL"
                and (child.text or "").strip()
            ):
                return True
    return False


def _http_get(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "AVR Control"},
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            payload = response.read()
    except Exception as exc:
        raise DriverError(f"Yamaha receiver request failed: {exc}") from exc
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DriverError("Yamaha receiver returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise DriverError("Yamaha receiver returned an invalid response")
    return data


class YamahaDriver(Driver):
    key: ClassVar[str] = "yamaha"
    label: ClassVar[str] = "Yamaha (MusicCast)"

    def __init__(self, config: dict):
        super().__init__(config)
        self._scale = _fallback_scale()
        self._features_loaded = False
        self._feature_lock = asyncio.Lock()
        self._capabilities: frozenset[str] = frozenset(
            {VOLUME, MUTE, POWER, MODE}
        )
        self._zone_features: dict = {}
        self._modes: list[Mode] = []
        self._volume_range: tuple[int, int, int] | None = None

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

    def _url(self, path: str, params: dict | None = None) -> str:
        if not self.host:
            raise DriverError("Yamaha receiver host is missing")
        if not 1 <= self.port <= 65535:
            raise DriverError("Yamaha receiver port must be between 1 and 65535")
        host = self.host
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        query = urllib.parse.urlencode(params or {})
        return f"http://{host}:{self.port}{API_ROOT}/{path}" + (
            f"?{query}" if query else ""
        )

    async def _api(self, path: str, params: dict | None = None) -> dict:
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None, partial(_http_get, self._url(path, params))
        )
        response_code = _int(response.get("response_code"))
        if response_code is None:
            raise DriverError("Yamaha response omitted response_code")
        if response_code != 0:
            meaning = _ERROR_MEANINGS.get(response_code, "Unknown error")
            message = f"Yamaha response_code {response_code}: {meaning}"
            if response_code == 5:
                raise Refused(message)
            raise DriverError(message)
        return response

    def _derive_scale(self, zone: dict) -> None:
        ranges = _range_entries(zone)
        volume = ranges.get("volume")
        raw_min = _int(volume.get("min"), 0) if volume else 0
        raw_max = _int(volume.get("max"), 161) if volume else 161
        raw_step = _int(volume.get("step"), 1) if volume else 1
        if raw_min is None or raw_max is None or raw_max <= raw_min:
            raw_min, raw_max, raw_step = 0, 161, 1
        raw_step = max(1, raw_step or 1)
        self._volume_range = (raw_min, raw_max, raw_step)

        db_range = ranges.get("actual_volume_db")

        db_min = _number(db_range.get("min")) if db_range else None
        db_max = _number(db_range.get("max")) if db_range else None
        db_step = _number(db_range.get("step")) if db_range else None
        if (
            db_min is not None
            and db_max is not None
            and db_step is not None
            and db_step > 0
            and db_max > db_min
        ):
            steps_per_unit = max(1, round(1 / db_step))
            zero = round(
                raw_min
                + ((0 - db_min) / (db_max - db_min)) * (raw_max - raw_min)
            )
            cap = max(raw_min, min(raw_max, zero - 30 * steps_per_unit))
            self._scale = VolumeScale(
                min=raw_min,
                max=raw_max,
                unit="dB",
                zero=zero,
                steps_per_unit=steps_per_unit,
                default_cap=cap,
            )
        elif raw_max == 100:
            self._scale = VolumeScale(
                min=raw_min,
                max=raw_max,
                unit="%",
                zero=0,
                steps_per_unit=1,
                default_cap=min(raw_max, 70),
            )
        else:
            self._scale = _fallback_scale()

    async def _ensure_features(self) -> None:
        if self._features_loaded:
            return
        async with self._feature_lock:
            if self._features_loaded:
                return
            response = await self._api("system/getFeatures")
            zones = response.get("zone", [])
            zone = (
                next(
                    (
                        item
                        for item in zones
                        if isinstance(item, dict) and item.get("id") == "main"
                    ),
                    {},
                )
                if isinstance(zones, list)
                else {}
            )
            self._zone_features = zone
            functions = _function_names(zone)
            ranges = _range_entries(zone)
            capabilities: set[str] = set()
            if "volume" in functions or "volume" in ranges:
                capabilities.add(VOLUME)
            if "mute" in functions:
                capabilities.add(MUTE)
            if "power" in functions:
                capabilities.add(POWER)
            self._modes = _programs(zone)
            if self._modes or "sound_program" in functions:
                capabilities.add(MODE)
            self._capabilities = frozenset(capabilities)
            self._derive_scale(zone)
            self._features_loaded = True

    async def _require_capability(self, capability: str) -> None:
        await self._ensure_features()
        if capability not in self._capabilities:
            labels = {
                VOLUME: "volume",
                MUTE: "mute",
                POWER: "power control",
                MODE: "listening modes",
            }
            raise Unsupported(f"This Yamaha receiver doesn't support {labels[capability]}")

    async def status(self) -> Status:
        await self._ensure_features()
        response = await self._api("main/getStatus")
        raw_volume = _int(response.get("volume"))
        actual = response.get("actual_volume")
        if (
            raw_volume is not None
            and self._scale.unit == "dB"
            and isinstance(actual, dict)
            and str(actual.get("mode", "")).lower() == "db"
        ):
            actual_db = _number(actual.get("value"))
            if actual_db is not None:
                zero = round(raw_volume - actual_db * self._scale.steps_per_unit)
                cap = max(
                    self._scale.min,
                    min(
                        self._scale.max,
                        zero - 30 * self._scale.steps_per_unit,
                    ),
                )
                self._scale = VolumeScale(
                    min=self._scale.min,
                    max=self._scale.max,
                    unit="dB",
                    zero=zero,
                    steps_per_unit=self._scale.steps_per_unit,
                    default_cap=cap,
                )
        return Status(
            volume=raw_volume,
            muted=_bool(response.get("mute")),
            power=_power(response.get("power")),
            mode=(
                str(response["sound_program"])
                if response.get("sound_program") is not None
                else None
            ),
        )

    async def set_volume(self, raw: int) -> int:
        await self._require_capability(VOLUME)
        value = max(self.scale.min, min(self.scale.max, int(raw)))
        response = await self._api("main/setVolume", {"volume": value})
        confirmed = _int(response.get("volume"))
        if confirmed is None:
            confirmed = (await self.status()).volume
        if confirmed is None:
            raise DriverError("Yamaha receiver didn't confirm the volume")
        return confirmed

    async def set_mute(self, muted: bool) -> bool:
        await self._require_capability(MUTE)
        response = await self._api(
            "main/setMute", {"enable": "true" if muted else "false"}
        )
        confirmed = _bool(response.get("mute"))
        if confirmed is None:
            confirmed = (await self.status()).muted
        if confirmed is None:
            raise DriverError("Yamaha receiver didn't confirm mute")
        return confirmed

    async def set_power(self, on: bool) -> bool:
        await self._require_capability(POWER)
        response = await self._api(
            "main/setPower", {"power": "on" if on else "standby"}
        )
        confirmed = _power(response.get("power"))
        if confirmed is None:
            confirmed = (await self.status()).power
        if confirmed is None:
            raise DriverError("Yamaha receiver didn't confirm power")
        return confirmed

    async def set_mode(self, code: str) -> str:
        await self._require_capability(MODE)
        try:
            response = await self._api("main/setSoundProgram", {"program": code})
        except DriverError as exc:
            if isinstance(exc, Refused) or "response_code 4:" in str(exc):
                raise Refused(f"Yamaha refused sound program {code!r}: {exc}") from exc
            raise
        confirmed = response.get("sound_program")
        if confirmed is None:
            confirmed = (await self.status()).mode
        if confirmed is None:
            raise DriverError("Yamaha receiver didn't confirm the sound program")
        return str(confirmed)

    async def list_modes(self) -> list[Mode]:
        await self._ensure_features()
        return list(self._modes)

    def mode_name(self, code: str) -> str:
        return _pretty_mode(code)

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
            manufacturer = str(description.get("manufacturer", ""))
            if "yamaha" not in manufacturer.lower():
                return None
            if not _has_yxc_control_url(str(description.get("raw_xml", ""))):
                return None
            host = str(item["ip"])
            return Found(
                driver=cls.key,
                model=str(description.get("modelName", "") or "Yamaha receiver"),
                id=str(description.get("UDN", "") or host),
                host=host,
                config={"host": host},
            )

        found = await asyncio.gather(
            *(describe(item) for item in candidates.values())
        )
        by_id: dict[str, Found] = {}
        for item in found:
            if item is not None:
                by_id[item.id] = item
        return list(by_id.values())

    @classmethod
    async def probe(cls, config: dict) -> Found | None:
        host = str(config.get("host", "")).strip()
        if not host:
            return None
        driver = cls({**config, "host": host})
        try:
            response = await driver._api("system/getDeviceInfo")
        except Exception:
            return None
        model = str(response.get("model_name", "")).strip()
        if not model:
            return None
        device_id = str(response.get("device_id", "") or config.get("id", "") or host)
        return Found(
            driver=cls.key,
            model=model,
            id=device_id,
            host=host,
            config={**config, "host": host},
        )
