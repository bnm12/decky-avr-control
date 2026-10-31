from __future__ import annotations

import os

import decky

from avr_control.controller import Controller
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
controller = Controller(settings_repository, logger=decky.logger)


class Plugin:
    async def _delegate(self, method: str, *args):
        try:
            return await getattr(controller, method)(*args)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def get_state(self) -> dict:
        return await self._delegate("get_state")

    async def set_volume(self, vol: int) -> dict:
        return await self._delegate("set_volume", vol)

    async def set_mute(self, muted: bool) -> dict:
        return await self._delegate("set_mute", muted)

    async def get_host(self) -> str:
        return await self._delegate("get_host")

    async def get_connection(self) -> dict:
        return await self._delegate("get_connection")

    async def set_host(self, host: str):
        return await self._delegate("set_host", host)

    async def set_max(self, value: int):
        return await self._delegate("set_max", value)

    async def set_late_max(self, value: int):
        return await self._delegate("set_late_max", value)

    async def set_mode(self, code: str) -> dict:
        return await self._delegate("set_mode", code)

    async def set_late_night(self, on: bool) -> dict:
        return await self._delegate("set_late_night", on)

    async def discover(self) -> list:
        return await self._delegate("discover")

    async def select_receiver(self, ip: str, ident: str):
        return await self._delegate("select_receiver", ip, ident)

    async def set_current_game(self, appid: int, name: str):
        return await self._delegate("set_current_game", appid, name)

    async def game_started(self, appid: int, name: str) -> dict:
        return await self._delegate("game_started", appid, name)

    async def game_stopped(self, appid: int):
        return await self._delegate("game_stopped", appid)

    async def set_game_remember(self, what: str, enabled: bool) -> dict:
        return await self._delegate("set_game_remember", what, enabled)

    async def list_games(self) -> list:
        return await self._delegate("list_games")

    async def forget_game(self, appid: int):
        return await self._delegate("forget_game", appid)

    async def set_power(self, on: bool) -> dict:
        return await self._delegate("set_power", on)

    async def list_drivers(self) -> list[dict]:
        return [
            {
                "key": driver.key,
                "label": driver.label,
                "fields": [
                    {
                        "key": field.key,
                        "label": field.label,
                        "secret": field.secret,
                        "required": field.required,
                    }
                    for field in driver.config_fields
                ],
            }
            for driver in REGISTRY
        ]

    async def configure(self, driver_key: str, config: dict) -> dict:
        return await self._delegate("configure", driver_key, config)

    async def _main(self):
        decky.logger.info("AVR Control loaded")

    async def _unload(self):
        driver = controller._active_driver
        if driver is not None:
            try:
                await driver.close()
            except Exception:
                pass
        decky.logger.info("AVR Control unloaded")
