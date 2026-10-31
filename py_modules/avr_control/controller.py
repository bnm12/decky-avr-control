"""Receiver-independent application behaviour for the Decky adapter."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Callable

from .drivers import REGISTRY, discover_all, get_driver_class, probe_all
from .drivers.base import (
    MODE,
    MUTE,
    POWER,
    VOLUME,
    Driver,
    DriverError,
    Found,
    Refused,
    Status,
    Unsupported,
)
from .settings import SettingsStore, migrate

_CAPABILITY_ORDER = (VOLUME, MUTE, POWER, MODE)


class Controller:
    def __init__(
        self,
        store: SettingsStore,
        registry=None,
        logger=None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], object] = asyncio.sleep,
        reapply_delay: float = 4.0,
        rediscover_interval: float = 30.0,
    ):
        self.store = store
        self.registry = registry if registry is not None else REGISTRY
        self.logger = logger
        self.clock = clock
        self.sleep = sleep
        self.reapply_delay = reapply_delay
        self.rediscover_interval = rediscover_interval
        self._active_driver: Driver | None = None
        self._receiver_generation = 0
        self._initialized_driver: Driver | None = None
        self._last_rediscover: float | None = None
        self._last_found: list[Found] = []
        self._current_game: dict | None = None
        self._suppress_save_until: tuple[int, int, float] | None = None

    def _log(self, level: str, message: str) -> None:
        log = getattr(self.logger, level, None)
        if log:
            log(message)

    def _settings(self) -> dict:
        data = self.store.load()
        if data.get("version") != 2:
            data = migrate(data)
        data.setdefault("receiver", {"driver": None, "config": {}})
        data["receiver"].setdefault("driver", None)
        data["receiver"].setdefault("config", {})
        data.setdefault("cap", None)
        data.setdefault("late_cap", None)
        data.setdefault("late", {"active": False})
        data.setdefault("games", {})
        return data

    def _save(self, data: dict) -> None:
        data["version"] = 2
        self.store.save(data)

    @staticmethod
    def _host(data: dict) -> str:
        return str(data.get("receiver", {}).get("config", {}).get("host", "")).strip()

    @staticmethod
    def _missing_config_field(driver_class, config: dict):
        for field in driver_class.config_fields:
            value = config.get(field.key)
            if field.required and (value is None or not str(value).strip()):
                return field
        return None

    @staticmethod
    def _config_error(field) -> str:
        if field.key == "host":
            return "Enter the receiver IP address"
        return f"Enter {field.label}"

    def _normalized_driver_config(self, driver_class, config: dict):
        driver = driver_class(dict(config))
        normalized = dict(driver.config)
        if hasattr(type(driver), "port"):
            normalized["port"] = int(driver.port)
        return driver, normalized

    def _binding_config(self, driver_class, config: dict) -> dict:
        _instance, normalized = self._normalized_driver_config(
            driver_class, config
        )
        if "port" in normalized:
            without_port = dict(normalized)
            without_port.pop("port")
            _default_instance, defaults = self._normalized_driver_config(
                driver_class, without_port
            )
            if normalized["port"] == defaults.get("port"):
                normalized.pop("port")
        return normalized

    def _identity_config(self, driver_class, config: dict) -> dict:
        driver, normalized = self._normalized_driver_config(
            driver_class, config
        )
        identity = {}
        for key in getattr(driver_class, "identity_keys", ("host",)):
            if key not in normalized:
                continue
            value = normalized[key]
            try:
                value = getattr(driver, key)
            except Exception:
                pass
            if isinstance(value, str):
                value = value.strip()
            identity[key] = value
        return identity

    def _receiver_binding(self, data: dict) -> dict:
        receiver = data.get("receiver", {})
        driver = receiver.get("driver")
        ident = receiver.get("id")
        if ident:
            return {"driver": driver, "id": str(ident).upper()}
        config = receiver.get("config") or {}
        driver_class = self._class_for(driver)
        if driver_class is not None:
            try:
                config = self._identity_config(driver_class, config)
            except Exception:
                keys = getattr(driver_class, "identity_keys", ("host",))
                config = {key: config[key] for key in keys if key in config}
        else:
            config = {"host": config["host"]} if "host" in config else {}
        encoded = json.dumps(
            config,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return {
            "driver": driver,
            "config_hash": hashlib.sha256(encoded).hexdigest(),
        }

    def _legacy_receiver_binding(self, data: dict) -> dict:
        receiver = data.get("receiver", {})
        driver = receiver.get("driver")
        ident = receiver.get("id")
        if ident:
            return {"driver": driver, "id": str(ident).upper()}
        config = receiver.get("config") or {}
        driver_class = self._class_for(driver)
        if driver_class is not None:
            try:
                config = self._binding_config(driver_class, config)
            except Exception:
                config = receiver.get("config") or {}
        encoded = json.dumps(
            config,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return {
            "driver": driver,
            "config_hash": hashlib.sha256(encoded).hexdigest(),
        }

    def _bind_receiver_state(self, data: dict) -> bool:
        binding = self._receiver_binding(data)
        legacy_binding = self._legacy_receiver_binding(data)
        changed = False
        for value_key, binding_key in (
            ("cap", "cap_receiver"),
            ("late_cap", "late_cap_receiver"),
        ):
            previous = data.get(binding_key)
            if previous is None:
                if data.get(value_key) is not None:
                    data[binding_key] = binding
                    changed = True
            elif previous != binding:
                data[binding_key] = binding
                if previous != legacy_binding:
                    data[value_key] = None
                changed = True

        late = data.get("late", {})
        previous = data.get("late_receiver")
        if previous is None:
            if late.get("active") or late.get("prev_volume") is not None:
                data["late_receiver"] = binding
                changed = True
        elif previous != binding:
            data["late_receiver"] = binding
            if previous != legacy_binding:
                data["late"] = {"active": False}
            changed = True
        return changed

    def _scale_for(self, data: dict):
        if self._active_driver is not None:
            return self._active_driver.scale
        key = data.get("receiver", {}).get("driver")
        driver_class = self._class_for(key) or self._class_for("eiscp")
        return driver_class.scale

    def _class_for(self, key: str | None):
        if key is None:
            return None
        if self.registry is REGISTRY:
            return get_driver_class(key)
        return next((driver for driver in self.registry if driver.key == key), None)

    def _base_cap(self, data: dict, scale=None) -> int:
        scale = scale or self._scale_for(data)
        if data.get("cap"):
            return int(data["cap"])
        return int(scale.as_dict()["default_cap"])

    def _late_cap(self, data: dict, scale=None) -> int:
        scale = scale or self._scale_for(data)
        if data.get("late_cap"):
            return int(data["late_cap"])
        if scale.unit == "dB":
            return max(scale.min, scale.zero - 50 * scale.steps_per_unit)
        return min(
            scale.max,
            scale.min + round((scale.max - scale.min) * 0.2),
        )

    def _effective_cap(self, data: dict, scale=None) -> int:
        scale = scale or self._scale_for(data)
        cap = self._base_cap(data, scale)
        if data.get("late", {}).get("active"):
            cap = min(cap, self._late_cap(data, scale))
        return max(scale.min, min(scale.max, cap))

    def _is_current(self, generation: int, driver: Driver | None = None) -> bool:
        return generation == self._receiver_generation and (
            driver is None or self._active_driver is driver
        )

    def _is_current_game(self, generation: int, appid: int) -> bool:
        return (
            self._is_current(generation)
            and self._current_game is not None
            and self._current_game["appid"] == int(appid)
        )

    def _suppress_game_saves(self, generation: int, appid: int) -> None:
        if self._is_current_game(generation, appid):
            self._suppress_save_until = (
                generation,
                int(appid),
                self.clock() + 6.0,
            )

    @staticmethod
    def _stale_receiver_error() -> DriverError:
        return DriverError("Receiver changed while the operation was in progress")

    async def _get_driver(self, generation: int | None = None) -> Driver:
        if generation is None:
            generation = self._receiver_generation
        if not self._is_current(generation):
            raise self._stale_receiver_error()
        starting_driver = self._active_driver
        data = self._settings()
        receiver = data["receiver"]
        key = receiver.get("driver")
        config = dict(receiver.get("config") or {})
        driver_class = self._class_for(key)
        if driver_class is None:
            if key is not None:
                raise DriverError(f"Unknown receiver driver: {key}")
            if not config.get("host"):
                raise DriverError("Set the AVR IP address first, or tap Search network")
            found = await self._probe(config)
            if not self._is_current(generation, starting_driver):
                raise self._stale_receiver_error()
            if found is None:
                replacement = await self._rediscover(
                    data, generation, starting_driver
                )
                if replacement is not None:
                    return replacement
                if not self._is_current(generation, starting_driver):
                    raise self._stale_receiver_error()
                raise DriverError("No supported receiver answered at that address")
            receiver["driver"] = found.driver
            receiver["config"] = dict(found.config)
            self._bind_receiver_state(data)
            self._save(data)
            driver_class = self._class_for(found.driver)
            config = dict(found.config)
        if driver_class is None:
            raise DriverError(f"Unknown receiver driver: {key}")
        missing_field = self._missing_config_field(driver_class, config)
        if missing_field is not None:
            raise DriverError(self._config_error(missing_field))
        candidate, normalized_config = self._normalized_driver_config(
            driver_class, config
        )
        config_changed = receiver.get("config") != normalized_config
        receiver["config"] = normalized_config
        binding_changed = self._bind_receiver_state(data)
        active_config = None
        if self._active_driver is not None:
            active_class = self._class_for(self._active_driver.key)
            if active_class is not None:
                _active_instance, active_config = self._normalized_driver_config(
                    active_class, self._active_driver.config
                )
        if (
            self._active_driver is None
            or self._active_driver.key != driver_class.key
            or active_config != normalized_config
        ):
            self._active_driver = candidate
            self._initialized_driver = None
        if config_changed or binding_changed:
            self._save(data)
        self._bind_unbound_profiles(data, self._active_driver.key)
        return self._active_driver

    async def _probe(self, config: dict) -> Found | None:
        if self.registry is REGISTRY:
            return await probe_all(config)
        for driver_class in self.registry:
            try:
                found = await driver_class.probe(dict(config))
            except Exception:
                continue
            if found is not None:
                return found
        return None

    def _persist_driver_config(self, driver: Driver, generation: int) -> None:
        if (
            generation != self._receiver_generation
            or self._active_driver is not driver
        ):
            return
        data = self._settings()
        receiver = data["receiver"]
        _instance, config = self._normalized_driver_config(
            type(driver), driver.config
        )
        if receiver.get("driver") == driver.key and receiver.get("config") == config:
            return
        receiver["driver"] = driver.key
        receiver["config"] = config
        self._bind_receiver_state(data)
        self._save(data)

    def _bind_unbound_profiles(self, data: dict, driver_key: str) -> None:
        changed = False
        for profile in data.get("games", {}).values():
            if isinstance(profile, dict) and profile.get("driver") is None:
                profile["driver"] = driver_key
                changed = True
        if changed:
            self._save(data)

    async def _rediscover(
        self,
        data: dict,
        generation: int | None = None,
        starting_driver: Driver | None = None,
    ) -> Driver | None:
        if generation is None:
            generation = self._receiver_generation
        if starting_driver is None:
            starting_driver = self._active_driver
        ident = data.get("receiver", {}).get("id")
        now = self.clock()
        if not ident:
            return None
        if (
            self._last_rediscover is not None
            and now - self._last_rediscover < self.rediscover_interval
        ):
            return None
        self._last_rediscover = now
        try:
            found_list = await self._discover_found(2.5)
        except Exception as exc:
            self._log("error", f"Rediscovery failed: {exc}")
            return None
        if not self._is_current(generation, starting_driver):
            return None
        found = next((item for item in found_list if item.id.upper() == str(ident).upper()), None)
        if found is None:
            return None
        current_host = self._host(data)
        if found.host == current_host:
            return None
        self._log("info", f"AVR moved {current_host} -> {found.host}")
        data = self._settings()
        data["receiver"]["driver"] = found.driver
        data["receiver"]["config"] = dict(found.config)
        data["receiver"]["id"] = found.id
        self._bind_receiver_state(data)
        self._save(data)
        driver_class = self._class_for(found.driver)
        if driver_class is None:
            return None
        self._active_driver = driver_class(dict(found.config))
        self._initialized_driver = None
        return self._active_driver

    async def _run(
        self,
        method: str,
        *args,
        expected_driver: str | None = None,
        generation: int | None = None,
        driver: Driver | None = None,
    ):
        if generation is None:
            generation = self._receiver_generation
        if not self._is_current(generation, driver):
            raise self._stale_receiver_error()
        active_driver = await self._get_driver(generation)
        if not self._is_current(generation):
            raise self._stale_receiver_error()
        if driver is not None and active_driver is not driver:
            raise self._stale_receiver_error()
        driver = active_driver
        try:
            result = await getattr(driver, method)(*args)
        except (Refused, Unsupported):
            if not self._is_current(generation, driver):
                raise self._stale_receiver_error()
            raise
        except DriverError:
            if not self._is_current(generation, driver):
                raise self._stale_receiver_error()
            data = self._settings()
            replacement = await self._rediscover(data, generation, driver)
            if replacement is None:
                if not self._is_current(generation, driver):
                    raise self._stale_receiver_error()
                raise
            if not self._is_current(generation, replacement):
                raise self._stale_receiver_error()
            if expected_driver is not None and replacement.key != expected_driver:
                raise DriverError("The saved game profile belongs to another receiver driver")
            result = await getattr(replacement, method)(*args)
            driver = replacement
        except Exception:
            if not self._is_current(generation, driver):
                raise self._stale_receiver_error()
            raise
        if not self._is_current(generation, driver):
            raise self._stale_receiver_error()
        if method == "status":
            self._initialized_driver = driver
        self._persist_driver_config(driver, generation)
        return result

    async def get_host(self) -> str:
        return self._host(self._settings())

    async def get_connection(self) -> dict:
        receiver = self._settings()["receiver"]
        driver_key = receiver.get("driver")
        config = dict(receiver.get("config") or {})
        driver_class = self._class_for(driver_key)
        if driver_class is None:
            fields = {"host": config["host"]} if config.get("host") else {}
            return {
                "configured": bool(config.get("host")) if driver_key is None else False,
                "driver": driver_key,
                "label": None,
                "summary": str(config.get("host") or ""),
                "fields": fields,
            }

        fields = {
            field.key: config[field.key]
            for field in driver_class.config_fields
            if not field.secret and config.get(field.key) is not None
        }
        missing_field = self._missing_config_field(driver_class, config)
        if driver_class.key == "homeassistant":
            summary = config.get("entity_id") or config.get("model") or ""
        else:
            summary = config.get("host") or config.get("model") or ""
        return {
            "configured": missing_field is None,
            "driver": driver_class.key,
            "label": driver_class.label,
            "summary": str(summary),
            "fields": fields,
        }

    async def configure(self, driver_key: str, config: dict) -> dict:
        starting_generation = self._receiver_generation
        starting_driver = self._active_driver
        data = self._settings()
        config = dict(config)
        receiver = data["receiver"]
        driver_class = None
        if driver_key != "auto":
            driver_class = self._class_for(driver_key)
            if driver_class is None:
                return {"ok": False, "error": f"Unknown receiver driver: {driver_key}"}
        if driver_key != "auto" and receiver.get("driver") == driver_key:
            saved_config = dict(receiver.get("config") or {})
            config = {**saved_config, **config}
            for field in driver_class.config_fields:
                if field.secret and not str(config.get(field.key, "")).strip():
                    if saved_config.get(field.key):
                        config[field.key] = saved_config[field.key]
        if config.get("host"):
            config["host"] = str(config["host"]).strip()
        if driver_key == "auto":
            if not config.get("host"):
                return {"ok": False, "error": "Enter the receiver IP address"}
            missing_field = None
        else:
            missing_field = self._missing_config_field(driver_class, config)
        if missing_field is not None:
            return {"ok": False, "error": self._config_error(missing_field)}

        if not self._is_current(starting_generation, starting_driver):
            return {"ok": False, "error": str(self._stale_receiver_error())}
        self._bind_receiver_state(data)
        if driver_key == "auto":
            same_host = self._host(data) == str(config.get("host") or "").strip()
            receiver["config"] = config
            receiver["id"] = receiver.get("id") if same_host else None
            receiver["driver"] = None
            self._active_driver = None
            self._initialized_driver = None
            self._receiver_generation += 1
            generation = self._receiver_generation
            self._save(data)
            found = await self._probe(config)
            if not self._is_current(generation):
                return {"ok": False, "error": str(self._stale_receiver_error())}
            current = self._settings()
            active = self._active_driver
            if active is not None:
                active_matches_receiver = (
                    current["receiver"].get("driver") == active.key
                    and self._host(current) == str(config["host"]).strip()
                )
                if found is not None:
                    found_class = self._class_for(found.driver)
                    if found_class is None:
                        active_matches_receiver = False
                    else:
                        _found_driver, found_config = self._normalized_driver_config(
                            found_class, found.config
                        )
                        _active_driver, active_config = self._normalized_driver_config(
                            type(active), active.config
                        )
                        active_matches_receiver = (
                            active_matches_receiver
                            and active.key == found.driver
                            and active_config == found_config
                        )
                if active_matches_receiver:
                    if found is not None and found.id:
                        resolved_id = str(found.id).upper()
                        if current["receiver"].get("id") != resolved_id:
                            current["receiver"]["id"] = resolved_id
                            self._bind_receiver_state(current)
                            self._save(current)
                    return {"ok": True}
                return {"ok": False, "error": str(self._stale_receiver_error())}
            if found is None:
                return {"ok": False, "error": "No supported receiver answered at that address"}
            found_class = self._class_for(found.driver)
            if found_class is None:
                return {"ok": False, "error": f"Unknown receiver driver: {found.driver}"}
            active_driver, found_config = self._normalized_driver_config(
                found_class, found.config
            )
            data = self._settings()
            data["receiver"].update(
                {"driver": found.driver, "config": found_config}
            )
            if found.id:
                data["receiver"]["id"] = str(found.id).upper()
            self._bind_receiver_state(data)
            self._save(data)
            self._active_driver = active_driver
            self._initialized_driver = None
            return {"ok": True}

        candidate, config = self._normalized_driver_config(driver_class, config)
        old_driver = receiver.get("driver")
        try:
            old_config = self._identity_config(
                driver_class, receiver.get("config") or {}
            )
            same_connection = (
                old_driver == driver_key
                and old_config == self._identity_config(driver_class, config)
            )
        except Exception:
            same_connection = False
        receiver["driver"] = driver_key
        receiver["config"] = config
        if not same_connection:
            receiver["id"] = None
        self._bind_receiver_state(data)
        self._active_driver = candidate
        self._initialized_driver = None
        self._receiver_generation += 1
        self._save(data)
        return {"ok": True}

    async def set_host(self, host: str) -> None:
        await self.configure("auto", {"host": host})

    async def discover(self) -> list[dict]:
        generation = self._receiver_generation
        starting_driver = self._active_driver
        try:
            found = await self._discover_found(2.5)
            if not self._is_current(generation, starting_driver):
                return []
            self._last_found = found
            results = []
            for item in self._last_found:
                driver_class = self._class_for(item.driver)
                results.append(
                    {
                        "ip": item.host,
                        "model": item.model,
                        "id": item.id,
                        "driver": item.driver,
                        "label": driver_class.label if driver_class else item.driver,
                    }
                )
            return results
        except Exception as exc:
            self._log("error", f"Discovery failed: {exc}")
            return []

    async def _discover_found(self, timeout: float) -> list[Found]:
        if self.registry is REGISTRY:
            return await discover_all(timeout)
        results = await asyncio.gather(
            *(driver.discover(timeout) for driver in self.registry),
            return_exceptions=True,
        )
        found: list[Found] = []
        seen: set[str] = set()
        for result in results:
            if isinstance(result, BaseException):
                continue
            for item in result:
                if item.id not in seen:
                    found.append(item)
                    seen.add(item.id)
        return found

    async def select_receiver(self, ip: str, ident: str) -> None:
        found = next(
            (
                item
                for item in self._last_found
                if item.host == ip and item.id.upper() == ident.upper()
            ),
            None,
        )
        data = self._settings()
        self._bind_receiver_state(data)
        if found is None:
            found = Found(
                driver="eiscp",
                model="eISCP receiver",
                id=ident.upper(),
                host=ip,
                config={"host": ip, "port": 60128},
            )
        data["receiver"] = {
            "driver": found.driver,
            "config": dict(found.config),
            "id": ident.upper(),
        }
        self._bind_receiver_state(data)
        self._save(data)
        driver_class = self._class_for(found.driver)
        self._active_driver = driver_class(dict(found.config)) if driver_class else None
        self._initialized_driver = None
        self._receiver_generation += 1

    def _mode_label(self, driver: Driver, code: str | None) -> str | None:
        if not code:
            return None
        return driver.mode_name(code)

    async def _ensure_initialized(
        self,
        driver: Driver,
        generation: int,
        expected_driver: str | None = None,
    ) -> Driver:
        if not self._is_current(generation, driver):
            raise self._stale_receiver_error()
        if self._initialized_driver is driver:
            return driver
        await self._run(
            "status",
            expected_driver=expected_driver,
            generation=generation,
            driver=driver,
        )
        active_driver = self._active_driver
        if active_driver is None or not self._is_current(generation, active_driver):
            raise self._stale_receiver_error()
        return active_driver

    async def get_state(self) -> dict:
        generation = self._receiver_generation
        try:
            driver = await self._get_driver(generation)
            if not self._is_current(generation, driver):
                raise self._stale_receiver_error()
            status = await self._run("status", generation=generation, driver=driver)
            driver = self._active_driver
            if driver is None or not self._is_current(generation, driver):
                raise self._stale_receiver_error()
            data = self._settings()
            await self._remember(
                status.volume, status.mode, generation=generation, driver=driver
            )
            data = self._settings()
            game = None
            if self._current_game:
                profile = data.get("games", {}).get(str(self._current_game["appid"]), {})
                game = {
                    **self._current_game,
                    "volume": profile.get("volume") is not None,
                    "mode": profile.get("mode") is not None,
                }
            modes = await driver.list_modes()
            if not self._is_current(generation, driver):
                raise self._stale_receiver_error()
            scale = driver.scale
            return {
                "ok": True,
                "volume": status.volume,
                "muted": status.muted,
                "max": self._effective_cap(data, scale),
                "cap": self._base_cap(data, scale),
                "late": bool(data.get("late", {}).get("active")),
                "late_cap": self._late_cap(data, scale),
                "zero": scale.zero,
                "mode": self._mode_label(driver, status.mode),
                "mode_code": status.mode,
                "modes": [{"code": mode.code, "name": mode.name} for mode in modes],
                "game": game,
                "driver": driver.key,
                "driver_label": driver.label,
                "scale": scale.as_dict(),
                "capabilities": [
                    capability
                    for capability in _CAPABILITY_ORDER
                    if capability in driver.capabilities
                ],
                "power": status.power,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def set_volume(self, value: int) -> dict:
        generation = self._receiver_generation
        try:
            data = self._settings()
            driver = await self._get_driver(generation)
            if not self._is_current(generation, driver):
                raise self._stale_receiver_error()
            driver = await self._ensure_initialized(driver, generation)
            scale = driver.scale
            requested_volume = int(value)
            volume = max(
                scale.min,
                min(self._effective_cap(data, scale), requested_volume),
            )
            confirmed = await self._run(
                "set_volume", volume, generation=generation, driver=driver
            )
            current_driver = self._active_driver
            if current_driver is None or not self._is_current(
                generation, current_driver
            ):
                raise self._stale_receiver_error()
            await self._remember(
                volume=confirmed, generation=generation, driver=current_driver
            )
            result = {"ok": True, "volume": confirmed}
            if confirmed != requested_volume:
                result["confirmed"] = False
            return result
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def set_mute(self, muted: bool) -> dict:
        generation = self._receiver_generation
        driver = self._active_driver
        try:
            result = await self._run(
                "set_mute", bool(muted), generation=generation, driver=driver
            )
            response = {"ok": True, "muted": result}
            if result != bool(muted):
                response["confirmed"] = False
            return response
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def set_power(self, on: bool) -> dict:
        generation = self._receiver_generation
        driver = self._active_driver
        try:
            result = await self._run(
                "set_power", bool(on), generation=generation, driver=driver
            )
            response = {"ok": True, "power": result}
            if result != bool(on):
                response["confirmed"] = False
            return response
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def set_max(self, value: int) -> None:
        generation = self._receiver_generation
        driver = await self._get_driver(generation)
        driver = await self._ensure_initialized(driver, generation)
        if not self._is_current(generation, driver):
            raise self._stale_receiver_error()
        data = self._settings()
        scale = driver.scale
        data["cap"] = max(1, min(scale.max, int(value)))
        data["cap_receiver"] = self._receiver_binding(data)
        self._save(data)

    async def set_late_max(self, value: int) -> None:
        generation = self._receiver_generation
        driver = await self._get_driver(generation)
        driver = await self._ensure_initialized(driver, generation)
        if not self._is_current(generation, driver):
            raise self._stale_receiver_error()
        data = self._settings()
        scale = driver.scale
        data["late_cap"] = max(1, min(scale.max, int(value)))
        data["late_cap_receiver"] = self._receiver_binding(data)
        self._save(data)

    async def set_mode(self, code: str) -> dict:
        generation = self._receiver_generation
        code = str(code).strip()
        if not code:
            return {"ok": False, "error": f"Invalid mode code: {code}"}
        try:
            driver = await self._get_driver(generation)
            if not self._is_current(generation, driver):
                raise self._stale_receiver_error()
            driver = await self._ensure_initialized(driver, generation)
            if MODE not in driver.capabilities:
                return {"ok": False, "error": "Listening modes need the eISCP protocol"}
            confirmed = await self._run(
                "set_mode", code, generation=generation, driver=driver
            )
            driver = self._active_driver
            if driver is None or not self._is_current(generation, driver):
                raise self._stale_receiver_error()
            await self._remember(
                mode=confirmed, generation=generation, driver=driver
            )
            response = {
                "ok": True,
                "mode": self._mode_label(driver, confirmed),
                "code": confirmed,
            }
            if confirmed != code:
                response["confirmed"] = False
            return response
        except Refused as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def _remember(
        self,
        volume: int | None = None,
        mode: str | None = None,
        generation: int | None = None,
        driver: Driver | None = None,
    ) -> None:
        if generation is None:
            generation = self._receiver_generation
        if driver is None:
            driver = self._active_driver
        if not self._is_current(generation, driver):
            return
        if not self._current_game:
            return
        suppression = self._suppress_save_until
        if (
            suppression is not None
            and suppression[:2] == (generation, self._current_game["appid"])
            and self.clock() < suppression[2]
        ):
            return
        data = self._settings()
        profile = data.get("games", {}).get(str(self._current_game["appid"]))
        if not profile:
            return
        if profile.get("driver") != (self._active_driver.key if self._active_driver else None):
            return
        if data.get("late", {}).get("active"):
            volume = None
        changed = False
        if volume is not None and profile.get("volume") is not None and profile["volume"] != volume:
            profile["volume"] = volume
            changed = True
        if mode is not None and profile.get("mode") is not None and profile["mode"] != mode:
            profile["mode"] = mode
            changed = True
        if changed:
            if self._is_current(generation, driver):
                self._save(data)

    async def set_current_game(self, appid: int, name: str) -> None:
        self._current_game = {"appid": int(appid), "name": name} if appid else None

    async def game_started(self, appid: int, name: str) -> dict:
        generation = self._receiver_generation
        self._current_game = {"appid": int(appid), "name": name}
        data = self._settings()
        profile = data.get("games", {}).get(str(int(appid)))
        if not profile:
            return {"applied": False}
        try:
            driver = await self._get_driver(generation)
            if not self._is_current(generation, driver):
                raise self._stale_receiver_error()
            if not self._current_game or self._current_game["appid"] != int(appid):
                return {"applied": False}
            data = self._settings()
            profile = data.get("games", {}).get(str(int(appid)))
            if not profile:
                return {"applied": False}
            if profile.get("driver") != driver.key:
                return {"applied": False}
            self._suppress_game_saves(generation, appid)
            driver = await self._ensure_initialized(
                driver, generation, expected_driver=driver.key
            )
            data = self._settings()
            profile = data.get("games", {}).get(str(int(appid)))
            if not profile or profile.get("driver") != driver.key:
                return {"applied": False}
            self._suppress_game_saves(generation, appid)
            applied = False
            volume = profile.get("volume")
            if volume is not None:
                volume = max(
                    driver.scale.min,
                    min(self._effective_cap(data, driver.scale), int(volume)),
                )
                await self._run(
                    "set_volume",
                    volume,
                    expected_driver=driver.key,
                    generation=generation,
                    driver=driver,
                )
                if not self._is_current(generation):
                    raise self._stale_receiver_error()
                driver = self._active_driver
                applied = True
            mode = profile.get("mode")
            if mode and MODE in driver.capabilities:
                mode_applied = False
                try:
                    await self._run(
                        "set_mode",
                        mode,
                        expected_driver=driver.key,
                        generation=generation,
                        driver=driver,
                    )
                    if not self._is_current(generation):
                        raise self._stale_receiver_error()
                    driver = self._active_driver
                    applied = True
                    mode_applied = True
                except Exception as exc:
                    self._log("error", f"Setting listening mode failed: {exc}")
                if mode_applied:
                    asyncio.create_task(
                        self._reapply_mode(
                            int(appid), mode, generation, driver
                        )
                    )
            return {"applied": applied}
        except Exception as exc:
            self._suppress_game_saves(generation, appid)
            self._log("error", f"Applying game profile failed: {exc}")
            return {"applied": False, "error": str(exc)}

    async def _reapply_mode(
        self,
        appid: int,
        mode: str | None,
        generation: int | None = None,
        driver: Driver | None = None,
    ) -> None:
        if not mode:
            return
        if generation is None:
            generation = self._receiver_generation
        if driver is None:
            driver = self._active_driver
        if not self._is_current(generation, driver):
            return
        result = self.sleep(self.reapply_delay)
        if asyncio.iscoroutine(result):
            await result
        if (
            not self._is_current(generation, driver)
            or not self._current_game
            or self._current_game["appid"] != appid
        ):
            return
        try:
            status: Status = await self._run(
                "status", generation=generation, driver=driver
            )
            if not self._is_current(generation, driver):
                return
            if status.mode and status.mode != mode:
                await self._run(
                    "set_mode",
                    mode,
                    expected_driver=driver.key,
                    generation=generation,
                    driver=driver,
                )
        except Exception as exc:
            self._log("error", f"Re-applying listening mode failed: {exc}")

    async def game_stopped(self, appid: int) -> None:
        if self._current_game and self._current_game["appid"] == int(appid):
            self._current_game = None

    async def set_game_remember(self, what: str, enabled: bool) -> dict:
        generation = self._receiver_generation
        current_game = dict(self._current_game) if self._current_game else None
        if what not in ("volume", "mode"):
            return {"ok": False, "error": f"Unknown setting: {what}"}
        if not current_game:
            return {"ok": False, "error": "No game is running"}
        key = str(current_game["appid"])
        data = self._settings()
        if enabled:
            try:
                driver = await self._get_driver(generation)
                if not self._is_current(generation, driver):
                    raise self._stale_receiver_error()
                status: Status = await self._run(
                    "status", generation=generation, driver=driver
                )
            except Exception as exc:
                return {"ok": False, "error": str(exc)}
            driver = self._active_driver
            if (
                driver is None
                or not self._is_current(generation, driver)
                or self._current_game != current_game
            ):
                return {"ok": False, "error": str(self._stale_receiver_error())}
            data = self._settings()
            if what == "volume":
                value = status.volume
                if data.get("late", {}).get("active"):
                    value = data.get("late", {}).get("prev_volume", value)
                if value is None:
                    return {"ok": False, "error": "Receiver doesn't report volume"}
            else:
                value = status.mode
                if not value:
                    return {"ok": False, "error": "Receiver doesn't report a listening mode"}
            profile = data.setdefault("games", {}).setdefault(key, {})
            if profile.get("driver") not in (None, driver.key):
                profile.pop("volume", None)
                profile.pop("mode", None)
            profile["name"] = current_game["name"]
            profile["driver"] = driver.key
            profile[what] = value
        else:
            games = data.setdefault("games", {})
            profile = games.get(key)
            if profile:
                profile.pop(what, None)
                if profile.get("volume") is None and profile.get("mode") is None:
                    games.pop(key, None)
        if not self._is_current(generation) or self._current_game != current_game:
            return {"ok": False, "error": str(self._stale_receiver_error())}
        self._save(data)
        return {"ok": True}

    async def list_games(self) -> list[dict]:
        data = self._settings()
        current_driver = self._class_for(data.get("receiver", {}).get("driver"))
        results = []
        for appid, profile in data.get("games", {}).items():
            code = profile.get("mode")
            name = None
            if code and current_driver:
                name = current_driver({}).mode_name(code)
            elif code:
                name = f"Mode {code}"
            results.append(
                {
                    "appid": int(appid),
                    "name": profile.get("name", ""),
                    "volume": profile.get("volume"),
                    "mode": code,
                    "mode_name": name,
                    "driver": profile.get("driver"),
                }
            )
        return results

    async def forget_game(self, appid: int) -> None:
        data = self._settings()
        data.get("games", {}).pop(str(int(appid)), None)
        self._save(data)

    async def set_late_night(self, on: bool) -> dict:
        generation = self._receiver_generation
        try:
            data = self._settings()
            driver = await self._get_driver(generation)
            if not self._is_current(generation, driver):
                raise self._stale_receiver_error()
            active = bool(data.get("late", {}).get("active"))
            if on and not active:
                status: Status = await self._run(
                    "status", generation=generation, driver=driver
                )
                if not self._is_current(generation, driver):
                    raise self._stale_receiver_error()
                if status.volume is None:
                    return {"ok": False, "error": "Turn the receiver on first"}
                data = self._settings()
                data["late"] = {"active": True, "prev_volume": status.volume}
                data["late_receiver"] = self._receiver_binding(data)
                self._save(data)
                cap = self._effective_cap(data, driver.scale)
                if status.volume > cap:
                    confirmed = await self._run(
                        "set_volume",
                        cap,
                        generation=generation,
                        driver=driver,
                    )
                    if not self._is_current(generation):
                        raise self._stale_receiver_error()
                    current_driver = self._active_driver
                    if current_driver is not None:
                        await self._remember(
                            volume=confirmed,
                            generation=generation,
                            driver=current_driver,
                        )
            elif not on and active:
                previous = data.get("late", {}).get("prev_volume")
                if not self._is_current(generation, driver):
                    raise self._stale_receiver_error()
                data["late"] = {"active": False}
                self._save(data)
                if previous is not None:
                    volume = max(
                        driver.scale.min,
                        min(self._effective_cap(data, driver.scale), int(previous)),
                    )
                    confirmed = await self._run(
                        "set_volume",
                        volume,
                        generation=generation,
                        driver=driver,
                    )
                    if not self._is_current(generation):
                        raise self._stale_receiver_error()
                    current_driver = self._active_driver
                    if current_driver is not None:
                        await self._remember(
                            volume=confirmed,
                            generation=generation,
                            driver=current_driver,
                        )
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
