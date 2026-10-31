from __future__ import annotations

import math
import os
from typing import Any

import decky

from avr_control.application import (
    ConfigureReceiver,
    ForgetGame,
    GameStarted,
    GameStopped,
    RememberForGame,
    SelectReceiver,
    SetCaps,
    SetCurrentGame,
    SetLateNight,
    SetMode,
    SetMute,
    SetPower,
    SetVolume,
    ControlApplication,
)
from avr_control.drivers import REGISTRY
from avr_control.settings import FileSettingsStore, SettingsRepository

SETTINGS = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "settings.json")
LEGACY_SETTINGS = os.path.join(
    os.path.dirname(decky.DECKY_PLUGIN_SETTINGS_DIR),
    "pioneer-volume",
    "settings.json",
)
secret_keys = {
    field.key
    for driver in REGISTRY
    for field in driver.config_fields
    if field.secret
}
settings_repository = SettingsRepository(
    FileSettingsStore(SETTINGS, legacy_paths=[LEGACY_SETTINGS]),
    logger=decky.logger,
    secret_keys=secret_keys,
)
application = ControlApplication(settings_repository, logger=decky.logger)


def _intent(value: Any):
    if not isinstance(value, dict):
        raise ValueError("Intent must be an object")
    kind = value.get("type")
    types = {
        "set_volume": (SetVolume, {"display": "number"}),
        "set_mute": (SetMute, {"muted": "bool"}),
        "set_power": (SetPower, {"on": "bool"}),
        "set_mode": (SetMode, {"code": "string"}),
        "set_late_night": (SetLateNight, {"on": "bool"}),
        "set_caps": (
            SetCaps,
            {"cap_display": "number", "late_cap_display": "number"},
        ),
        "configure_receiver": (
            ConfigureReceiver,
            {"driver_key": "string", "config": "object"},
        ),
        "select_receiver": (SelectReceiver, {"id": "string"}),
        "game_started": (
            GameStarted,
            {"appid": "int", "name": "string"},
        ),
        "game_stopped": (GameStopped, {"appid": "int"}),
        "set_current_game": (
            SetCurrentGame,
            {"appid": "int", "name": "string"},
        ),
        "remember_for_game": (
            RememberForGame,
            {"what": "string", "on": "bool"},
        ),
        "forget_game": (ForgetGame, {"appid": "int"}),
    }
    if not isinstance(kind, str) or kind not in types:
        raise ValueError(f"Unknown intent type: {kind!r}")
    intent_class, fields = types[kind]
    allowed = {"type", *fields}
    extra = set(value) - allowed
    if extra:
        raise ValueError(f"Unexpected fields for {kind}: {', '.join(sorted(extra))}")
    missing = set(fields) - set(value)
    if missing:
        raise ValueError(f"Missing fields for {kind}: {', '.join(sorted(missing))}")
    parsed = {}
    for key, expected in fields.items():
        item = value[key]
        if expected == "bool":
            valid = isinstance(item, bool)
        elif expected == "string":
            valid = isinstance(item, str)
        elif expected == "object":
            valid = isinstance(item, dict) and all(
                isinstance(config_key, str) for config_key in item
            )
        elif expected == "int":
            valid = isinstance(item, int) and not isinstance(item, bool)
        else:
            valid = isinstance(item, (int, float)) and not isinstance(item, bool)
            if valid:
                try:
                    valid = math.isfinite(float(item))
                except (OverflowError, ValueError):
                    valid = False
        if not valid:
            raise ValueError(f"Invalid {key} for {kind}")
        parsed[key] = item
    return intent_class(**parsed)


class Plugin:
    async def snapshot(self) -> dict:
        return await application.snapshot()

    async def dispatch(self, intent: dict) -> dict:
        try:
            parsed = _intent(intent)
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        return await application.dispatch(parsed)

    async def discover(self) -> list:
        return await application.discover()

    async def list_drivers(self) -> list[dict]:
        return application.drivers()

    async def setup_options(self, driver_key: str, config: dict) -> dict:
        try:
            return await application.setup_options(driver_key, config)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def _main(self):
        decky.logger.info("AVR Control loaded")

    async def _unload(self):
        try:
            await application.close()
        except Exception:
            pass
        decky.logger.info("AVR Control unloaded")
