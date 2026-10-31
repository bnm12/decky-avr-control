"""Home Assistant REST API driver for media-player entities."""

from __future__ import annotations

import asyncio
import json
import math
import urllib.error
import urllib.parse
import urllib.request
from typing import ClassVar

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
    Unsupported,
    VolumeScale,
)

REQUEST_TIMEOUT = 3.0
CONFIRM_TIMEOUT = 1.5
CONFIRM_INTERVAL = 0.1

VOLUME_SET = 4
VOLUME_MUTE = 8
TURN_ON = 128
TURN_OFF = 256
SELECT_SOUND_MODE = 65536


def _url_origin(url: str) -> tuple[str, str, int] | None:
    try:
        parsed = urllib.parse.urlsplit(url)
        scheme = parsed.scheme.lower()
        host = parsed.hostname
        if scheme not in {"http", "https"} or not host:
            return None
        port = parsed.port
    except ValueError:
        return None
    if port is None:
        port = 443 if scheme == "https" else 80
    return scheme, host.lower(), port


class _SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, origin: tuple[str, str, int]):
        super().__init__()
        self._origin = origin

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target_origin = _url_origin(newurl)
        is_https_downgrade = (
            self._origin[0] == "https"
            and target_origin is not None
            and target_origin[0] == "http"
        )
        if target_origin != self._origin or is_https_downgrade:
            raise DriverError(
                "Home Assistant redirected to another address; enter the final URL"
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _read_response(
    request: urllib.request.Request, timeout: float, origin: tuple[str, str, int]
) -> bytes:
    opener = urllib.request.build_opener(_SameOriginRedirectHandler(origin))
    with opener.open(request, timeout=timeout) as response:
        return response.read()


def _redact(value: str, token: str) -> str:
    return value.replace(token, "[redacted]") if token else value


class HomeAssistantDriver(Driver):
    key: ClassVar[str] = "homeassistant"
    label: ClassVar[str] = "Home Assistant"
    config_fields: ClassVar[tuple[ConfigField, ...]] = (
        ConfigField("url", "Home Assistant URL (e.g. http://homeassistant.local:8123)"),
        ConfigField("token", "Long-lived access token", secret=True),
        ConfigField(
            "entity_id",
            "Media player entity (e.g. media_player.living_room_receiver)",
        ),
    )
    scale = VolumeScale(min=0, max=100, unit="%", zero=0, steps_per_unit=1, default_cap=60)

    def __init__(self, config: dict):
        super().__init__(config)
        self._supported_features: int | None = None
        self._sound_mode_list: list[str] | None = None

    @property
    def url(self) -> str:
        return str(self.config.get("url", "")).strip().rstrip("/")

    @property
    def token(self) -> str:
        return str(self.config.get("token", ""))

    @property
    def entity_id(self) -> str:
        return str(self.config.get("entity_id", "")).strip()

    @property
    def capabilities(self) -> frozenset[str]:
        features = self._supported_features
        if features is None:
            return frozenset()
        result: set[str] = set()
        if features & VOLUME_SET:
            result.add(VOLUME)
        if features & VOLUME_MUTE:
            result.add(MUTE)
        if features & (TURN_ON | TURN_OFF):
            result.add(POWER)
        if features & SELECT_SOUND_MODE:
            result.add(MODE)
        return frozenset(result)

    async def _request_json(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict | None = None,
        state_request: bool = False,
    ):
        base_url = self.url
        token = self.token
        try:
            parsed = urllib.parse.urlsplit(base_url)
            origin = _url_origin(base_url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or origin is None
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError
            request_url = f"{base_url}{path}"
        except (ValueError, TypeError):
            raise DriverError("Invalid Home Assistant URL") from None

        try:
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
            data = json.dumps(payload).encode("utf-8") if payload is not None else None
            request = urllib.request.Request(
                request_url, data=data, headers=headers, method=method
            )
        except Exception:
            raise DriverError("Invalid Home Assistant request") from None
        try:
            body = await asyncio.get_running_loop().run_in_executor(
                None, _read_response, request, REQUEST_TIMEOUT, origin
            )
        except DriverError:
            raise
        except urllib.error.HTTPError as exc:
            exc.close()
            if exc.code in (401, 403):
                raise DriverError("Home Assistant rejected the token") from None
            if exc.code == 404 and state_request:
                entity_id = _redact(self.entity_id, token)
                raise DriverError(f"Entity {entity_id} not found") from None
            raise DriverError(
                f"Home Assistant request failed (HTTP {exc.code})"
            ) from None
        except (urllib.error.URLError, OSError, TimeoutError):
            safe_url = _redact(base_url, token)
            raise DriverError(
                f"Can't reach Home Assistant at {safe_url}"
            ) from None
        except Exception:
            raise DriverError("Home Assistant request failed") from None

        if not body:
            return None
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise DriverError("Home Assistant returned invalid JSON") from None

    async def _read_state(self) -> dict:
        quoted_entity_id = urllib.parse.quote(self.entity_id, safe="")
        response = await self._request_json(
            f"/api/states/{quoted_entity_id}", state_request=True
        )
        if not isinstance(response, dict) or not isinstance(response.get("state"), str):
            raise DriverError("Home Assistant returned an invalid entity state")
        return response

    async def status(self) -> Status:
        response = await self._read_state()
        state = response["state"].strip().lower()
        safe_entity_id = _redact(self.entity_id, self.token)
        if state in {"unavailable", "unknown"}:
            raise DriverError(
                f"Home Assistant entity {safe_entity_id} is {state}"
            )

        attributes = response.get("attributes")
        if not isinstance(attributes, dict):
            attributes = {}

        try:
            features = int(attributes.get("supported_features", 0))
        except (TypeError, ValueError, OverflowError):
            features = 0
        self._supported_features = features

        raw_volume = None
        level = attributes.get("volume_level")
        if level is not None:
            try:
                numeric_level = float(level)
                if math.isfinite(numeric_level):
                    raw_volume = max(0, min(100, round(numeric_level * 100)))
            except (TypeError, ValueError, OverflowError):
                pass

        muted = attributes.get("is_volume_muted")
        if not isinstance(muted, bool):
            muted = None
        current_mode = attributes.get("sound_mode")
        if not isinstance(current_mode, str):
            current_mode = None

        modes = attributes.get("sound_mode_list")
        self._sound_mode_list = (
            [mode for mode in modes if isinstance(mode, str)]
            if isinstance(modes, list)
            else None
        )

        return Status(
            volume=raw_volume,
            muted=muted,
            power=state not in {"off", "standby"},
            mode=current_mode,
        )

    async def _call_service(self, service: str, data: dict) -> None:
        await self._request_json(
            f"/api/services/media_player/{service}",
            method="POST",
            payload={"entity_id": self.entity_id, **data},
        )

    async def _require_feature(self, feature: int, message: str) -> None:
        if self._supported_features is None:
            await self.status()
        if not self._supported_features & feature:
            raise Unsupported(message)

    async def _confirm(self, field: str, requested):
        deadline = asyncio.get_running_loop().time() + CONFIRM_TIMEOUT
        last_observed = None
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return last_observed
            try:
                snapshot = await asyncio.wait_for(self.status(), remaining)
            except asyncio.TimeoutError:
                return last_observed
            confirmed = getattr(snapshot, field)
            last_observed = confirmed
            if confirmed == requested:
                return confirmed
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return last_observed
            await asyncio.sleep(min(CONFIRM_INTERVAL, remaining))

    async def set_volume(self, raw: int) -> int:
        await self._require_feature(
            VOLUME_SET, "Home Assistant entity doesn't support volume"
        )
        try:
            requested = max(self.scale.min, min(self.scale.max, int(raw)))
        except (TypeError, ValueError, OverflowError):
            raise DriverError("Invalid volume value") from None
        await self._call_service(
            "volume_set", {"volume_level": requested / 100}
        )
        return await self._confirm("volume", requested)

    async def set_mute(self, muted: bool) -> bool:
        await self._require_feature(
            VOLUME_MUTE, "Home Assistant entity doesn't support mute"
        )
        requested = bool(muted)
        await self._call_service(
            "volume_mute", {"is_volume_muted": requested}
        )
        return await self._confirm("muted", requested)

    async def set_power(self, on: bool) -> bool:
        requested = bool(on)
        feature = TURN_ON if requested else TURN_OFF
        message = (
            "Home Assistant entity doesn't support turning on"
            if requested
            else "Home Assistant entity doesn't support turning off"
        )
        await self._require_feature(feature, message)
        await self._call_service("turn_on" if requested else "turn_off", {})
        return await self._confirm("power", requested)

    async def set_mode(self, code: str) -> str:
        await self._require_feature(
            SELECT_SOUND_MODE,
            "Home Assistant entity doesn't support sound modes",
        )
        if self._sound_mode_list is not None and code not in self._sound_mode_list:
            raise Refused("Home Assistant doesn't offer that sound mode")
        await self._call_service("select_sound_mode", {"sound_mode": code})
        return await self._confirm("mode", code)

    async def list_modes(self) -> list[Mode]:
        await self.status()
        return [Mode(mode, mode) for mode in (self._sound_mode_list or [])]

    def mode_name(self, code: str) -> str:
        return code

    @classmethod
    async def discover(cls, timeout: float) -> list[Found]:
        return []

    @classmethod
    async def probe(cls, config: dict) -> Found | None:
        url = str(config.get("url", "")).strip()
        token = str(config.get("token", ""))
        entity_id = str(config.get("entity_id", "")).strip()
        if not url or not token or not entity_id:
            return None
        driver = cls(config)
        try:
            api_status = await driver._request_json("/api/")
            if not isinstance(api_status, dict) or api_status.get("message") != "API running.":
                return None
            response = await driver._read_state()
        except DriverError:
            return None

        attributes = response.get("attributes")
        if not isinstance(attributes, dict):
            attributes = {}
        friendly_name = attributes.get("friendly_name")
        model = friendly_name if isinstance(friendly_name, str) and friendly_name else entity_id
        model = _redact(model, token)
        try:
            parsed = urllib.parse.urlsplit(driver.url)
            host = parsed.hostname or ""
        except ValueError:
            return None
        return Found(
            driver=cls.key,
            model=model,
            id=f"ha:{entity_id}",
            host=_redact(host, token),
            config=config,
        )
