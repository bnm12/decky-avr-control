"""Receiver-independent application behaviour for the Decky adapter."""

from __future__ import annotations

import asyncio
import time
from typing import Callable

from .drivers import REGISTRY, discover_all, get_driver_class, probe_all
from .drivers.base import MODE, MUTE, POWER, VOLUME, Driver, DriverError, Found, Refused, Status
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
        self._last_rediscover: float | None = None
        self._last_found: list[Found] = []
        self._current_game: dict | None = None
        self._suppress_save_until = 0.0

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
        return int(data.get("cap") or scale.default_cap or scale.max)

    def _late_cap(self, data: dict, scale=None) -> int:
        scale = scale or self._scale_for(data)
        if data.get("late_cap"):
            return int(data["late_cap"])
        if scale.unit == "dB":
            return max(scale.min, scale.zero - 50 * scale.steps_per_unit)
        return min(scale.max, 20)

    def _effective_cap(self, data: dict, scale=None) -> int:
        scale = scale or self._scale_for(data)
        cap = self._base_cap(data, scale)
        if data.get("late", {}).get("active"):
            cap = min(cap, self._late_cap(data, scale))
        return max(scale.min, min(scale.max, cap))

    async def _get_driver(self) -> Driver:
        data = self._settings()
        receiver = data["receiver"]
        key = receiver.get("driver")
        config = dict(receiver.get("config") or {})
        if not config.get("host"):
            raise DriverError("Set the AVR IP address first, or tap Search network")
        driver_class = self._class_for(key)
        if driver_class is None:
            found = await self._probe(config)
            if found is None:
                raise DriverError("No supported receiver answered at that address")
            receiver["driver"] = found.driver
            receiver["config"] = dict(found.config)
            self._save(data)
            driver_class = self._class_for(found.driver)
            config = dict(found.config)
        if driver_class is None:
            raise DriverError(f"Unknown receiver driver: {key}")
        if (
            self._active_driver is None
            or self._active_driver.key != driver_class.key
            or self._active_driver.config.get("host") != config.get("host")
        ):
            self._active_driver = driver_class(config)
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

    def _persist_driver_config(self, driver: Driver) -> None:
        data = self._settings()
        receiver = data["receiver"]
        receiver["driver"] = driver.key
        receiver["config"] = dict(driver.config)
        self._save(data)

    async def _rediscover(self, data: dict) -> Driver | None:
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
        self._save(data)
        driver_class = self._class_for(found.driver)
        if driver_class is None:
            return None
        self._active_driver = driver_class(dict(found.config))
        return self._active_driver

    async def _run(self, method: str, *args, expected_driver: str | None = None):
        driver = await self._get_driver()
        try:
            result = await getattr(driver, method)(*args)
        except DriverError:
            data = self._settings()
            replacement = await self._rediscover(data)
            if replacement is None:
                raise
            if expected_driver is not None and replacement.key != expected_driver:
                raise DriverError("The saved game profile belongs to another receiver driver")
            result = await getattr(replacement, method)(*args)
            driver = replacement
        self._persist_driver_config(driver)
        return result

    async def get_host(self) -> str:
        return self._host(self._settings())

    async def configure(self, driver_key: str, config: dict) -> dict:
        data = self._settings()
        config = dict(config)
        if config.get("host"):
            config["host"] = str(config["host"]).strip()
        if not config.get("host"):
            return {"ok": False, "error": "Enter the receiver IP address"}

        receiver = data["receiver"]
        receiver["config"] = config
        receiver["id"] = None
        receiver["driver"] = None
        self._active_driver = None
        if driver_key == "auto":
            self._save(data)
            found = await self._probe(config)
            if found is None:
                return {"ok": False, "error": "No supported receiver answered at that address"}
            data = self._settings()
            data["receiver"].update(
                {"driver": found.driver, "config": dict(found.config)}
            )
            self._save(data)
            self._active_driver = self._class_for(found.driver)(dict(found.config))
            return {"ok": True}

        driver_class = self._class_for(driver_key)
        if driver_class is None:
            return {"ok": False, "error": f"Unknown receiver driver: {driver_key}"}
        receiver["driver"] = driver_key
        self._save(data)
        self._active_driver = driver_class(config)
        return {"ok": True}

    async def set_host(self, host: str) -> None:
        await self.configure("auto", {"host": host})

    async def discover(self) -> list[dict]:
        try:
            self._last_found = await self._discover_found(2.5)
            results = []
            for found in self._last_found:
                driver_class = self._class_for(found.driver)
                results.append(
                    {
                        "ip": found.host,
                        "model": found.model,
                        "id": found.id,
                        "driver": found.driver,
                        "label": driver_class.label if driver_class else found.driver,
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
        self._save(data)
        driver_class = self._class_for(found.driver)
        self._active_driver = driver_class(dict(found.config)) if driver_class else None

    def _mode_label(self, driver: Driver, code: str | None) -> str | None:
        if not code:
            return None
        return driver.mode_name(code)

    async def get_state(self) -> dict:
        try:
            driver = await self._get_driver()
            status = await self._run("status")
            driver = self._active_driver or driver
            data = self._settings()
            await self._remember(status.volume, status.mode)
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
        try:
            data = self._settings()
            driver = await self._get_driver()
            scale = driver.scale
            volume = max(scale.min, min(self._effective_cap(data, scale), int(value)))
            confirmed = await self._run("set_volume", volume)
            await self._remember(volume=confirmed)
            return {"ok": True, "volume": confirmed}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def set_mute(self, muted: bool) -> dict:
        try:
            return {"ok": True, "muted": await self._run("set_mute", bool(muted))}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def set_power(self, on: bool) -> dict:
        try:
            return {"ok": True, "power": await self._run("set_power", bool(on))}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def set_max(self, value: int) -> None:
        data = self._settings()
        scale = self._scale_for(data)
        data["cap"] = max(1, min(scale.max, int(value)))
        self._save(data)

    async def set_late_max(self, value: int) -> None:
        data = self._settings()
        scale = self._scale_for(data)
        data["late_cap"] = max(1, min(scale.max, int(value)))
        self._save(data)

    async def set_mode(self, code: str) -> dict:
        code = str(code).upper()
        import re

        if not re.fullmatch(r"[0-9A-F]{2,4}", code):
            return {"ok": False, "error": f"Invalid mode code: {code}"}
        try:
            driver = await self._get_driver()
            if MODE not in driver.capabilities:
                return {"ok": False, "error": "Listening modes need the eISCP protocol"}
            confirmed = await self._run("set_mode", code)
            await self._remember(mode=confirmed)
            driver = self._active_driver or driver
            return {
                "ok": True,
                "mode": self._mode_label(driver, confirmed),
                "code": confirmed,
            }
        except Refused as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def _remember(self, volume: int | None = None, mode: str | None = None) -> None:
        if not self._current_game or self.clock() < self._suppress_save_until:
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
            self._save(data)

    async def set_current_game(self, appid: int, name: str) -> None:
        self._current_game = {"appid": int(appid), "name": name} if appid else None

    async def game_started(self, appid: int, name: str) -> dict:
        self._current_game = {"appid": int(appid), "name": name}
        data = self._settings()
        profile = data.get("games", {}).get(str(int(appid)))
        if not profile:
            return {"applied": False}
        try:
            driver = await self._get_driver()
            if profile.get("driver") != driver.key:
                return {"applied": False}
            self._suppress_save_until = self.clock() + 6.0
            volume = profile.get("volume")
            if volume is not None:
                volume = max(
                    driver.scale.min,
                    min(self._effective_cap(data, driver.scale), int(volume)),
                )
                await self._run("set_volume", volume, expected_driver=driver.key)
            mode = profile.get("mode")
            if mode and MODE in driver.capabilities:
                try:
                    await self._run("set_mode", mode, expected_driver=driver.key)
                except Exception as exc:
                    self._log("error", f"Setting listening mode failed: {exc}")
            if mode and MODE in driver.capabilities:
                asyncio.create_task(self._reapply_mode(int(appid), mode))
            return {"applied": True}
        except Exception as exc:
            self._log("error", f"Applying game profile failed: {exc}")
            return {"applied": False, "error": str(exc)}

    async def _reapply_mode(self, appid: int, mode: str | None) -> None:
        if not mode:
            return
        result = self.sleep(self.reapply_delay)
        if asyncio.iscoroutine(result):
            await result
        if not self._current_game or self._current_game["appid"] != appid:
            return
        try:
            status: Status = await self._run("status")
            if status.mode and status.mode != mode:
                await self._run(
                    "set_mode", mode, expected_driver=self._active_driver.key
                )
        except Exception as exc:
            self._log("error", f"Re-applying listening mode failed: {exc}")

    async def game_stopped(self, appid: int) -> None:
        if self._current_game and self._current_game["appid"] == int(appid):
            self._current_game = None

    async def set_game_remember(self, what: str, enabled: bool) -> dict:
        if what not in ("volume", "mode"):
            return {"ok": False, "error": f"Unknown setting: {what}"}
        if not self._current_game:
            return {"ok": False, "error": "No game is running"}
        key = str(self._current_game["appid"])
        data = self._settings()
        if enabled:
            try:
                driver = await self._get_driver()
                status: Status = await self._run("status")
            except Exception as exc:
                return {"ok": False, "error": str(exc)}
            driver = self._active_driver or driver
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
            profile["name"] = self._current_game["name"]
            profile["driver"] = driver.key
            profile[what] = value
        else:
            games = data.setdefault("games", {})
            profile = games.get(key)
            if profile:
                profile.pop(what, None)
                if profile.get("volume") is None and profile.get("mode") is None:
                    games.pop(key, None)
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
                }
            )
        return results

    async def forget_game(self, appid: int) -> None:
        data = self._settings()
        data.get("games", {}).pop(str(int(appid)), None)
        self._save(data)

    async def set_late_night(self, on: bool) -> dict:
        try:
            data = self._settings()
            active = bool(data.get("late", {}).get("active"))
            if on and not active:
                status: Status = await self._run("status")
                data = self._settings()
                data["late"] = {"active": True, "prev_volume": status.volume}
                self._save(data)
                if status.volume is not None:
                    cap = self._effective_cap(data)
                    if status.volume > cap:
                        await self.set_volume(cap)
            elif not on and active:
                previous = data.get("late", {}).get("prev_volume")
                data["late"] = {"active": False}
                self._save(data)
                if previous is not None:
                    await self.set_volume(previous)
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
