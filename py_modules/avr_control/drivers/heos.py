"""Denon HEOS CLI driver for player volume and mute."""

from __future__ import annotations

import asyncio
import json
import weakref
import xml.etree.ElementTree as ET
from typing import Awaitable, Callable, ClassVar
from urllib.parse import parse_qs, urlencode

from .. import ssdp
from .base import MUTE, VOLUME, ConfigField, Driver, DriverError, Found, Status, VolumeScale

HEOS_PORT = 1255
HEOS_SSDP_ST = "urn:schemas-denon-com:device:ACT-Denon:1"
_REPLY_TIMEOUT = 2.0
_OPERATION_LOCKS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


async def _open(
    host: str, port: int
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    if not host:
        raise DriverError("Enter the IP address of a HEOS device")
    try:
        return await asyncio.wait_for(asyncio.open_connection(host, port), _REPLY_TIMEOUT)
    except Exception as exc:
        raise DriverError(f"Can't reach HEOS device at {host}:{port}: {exc}") from exc


async def _close(writer: asyncio.StreamWriter) -> None:
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass


async def _request(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    command: str,
    args: dict[str, str | int] | None = None,
    *,
    timeout: float = _REPLY_TIMEOUT,
) -> dict:
    request = f"heos://{command}"
    if args:
        request += "?" + urlencode(args)
    writer.write((request + "\r\n").encode("ascii"))
    await writer.drain()

    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise asyncio.TimeoutError()
        line = await asyncio.wait_for(reader.readline(), remaining)
        if not line:
            raise DriverError("HEOS closed the connection before replying")
        try:
            response = json.loads(line.decode("ascii"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DriverError("HEOS returned an invalid JSON response") from exc
        if not isinstance(response, dict) or not isinstance(response.get("heos"), dict):
            raise DriverError("HEOS returned an invalid response")
        metadata = response["heos"]
        message = str(metadata.get("message", ""))
        if message.strip().casefold() == "command under process":
            continue
        response_command = "".join(str(metadata.get("command", "")).split())
        if response_command != command:
            continue
        if str(metadata.get("result", "")).casefold() != "success":
            detail = message or str(metadata.get("result", "unknown error"))
            raise DriverError(f"HEOS command {command} failed: {detail}")
        return response


def _response_values(response: dict) -> dict:
    payload = response.get("payload")
    values = dict(payload) if isinstance(payload, dict) else {}
    message = str(response.get("heos", {}).get("message", ""))
    for key, items in parse_qs(message, keep_blank_values=True).items():
        if items:
            values[key] = items[-1].strip().strip("'\"")
    return values


def _description_value(raw_xml: str, key: str) -> str:
    try:
        root = ET.fromstring(raw_xml)
    except (ET.ParseError, TypeError):
        return ""
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == key:
            return (element.text or "").strip()
    return ""


def _player_text(player: dict, key: str) -> str:
    return str(player.get(key) or "").strip()


def _player_names(players: list[dict]) -> str:
    names = [str(player.get("name") or "(unnamed)") for player in players]
    return ", ".join(names) if names else "none"


class HeosDriver(Driver):
    key: ClassVar[str] = "heos"
    label: ClassVar[str] = "Denon HEOS (speakers, soundbars, HEOS-built-in)"
    config_fields: ClassVar[tuple[ConfigField, ...]] = (
        ConfigField("host", "HEOS device IP address"),
        ConfigField("player", "HEOS player name (blank = the device at this IP)", required=False),
    )
    capabilities = frozenset({VOLUME, MUTE})
    scale = VolumeScale(
        min=0, max=100, unit="%", zero=0, steps_per_unit=1, default_cap=60
    )

    @property
    def host(self) -> str:
        return str(self.config.get("host", "")).strip()

    @property
    def port(self) -> int:
        try:
            port = int(self.config.get("port", HEOS_PORT))
        except (TypeError, ValueError) as exc:
            raise DriverError("Invalid HEOS device port") from exc
        if not 1 <= port <= 65535:
            raise DriverError("Invalid HEOS device port")
        return port

    def _operation_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        locks = _OPERATION_LOCKS.get(loop)
        if locks is None:
            locks = weakref.WeakValueDictionary()
            _OPERATION_LOCKS[loop] = locks
        key = (self.host, self.port)
        lock = locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            locks[key] = lock
        return lock

    async def _transaction(
        self, action: Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable]
    ):
        try:
            async with self._operation_lock():
                reader, writer = await _open(self.host, self.port)
                try:
                    return await action(reader, writer)
                finally:
                    await _close(writer)
        except DriverError:
            raise
        except asyncio.TimeoutError as exc:
            raise DriverError("HEOS did not reply in time") from exc
        except Exception as exc:
            raise DriverError(f"HEOS request failed: {exc}") from exc

    async def _players(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> list[dict]:
        response = await _request(reader, writer, "player/get_players")
        payload = response.get("payload")
        if isinstance(payload, dict):
            payload = payload.get("players")
        if not isinstance(payload, list) or not all(
            isinstance(player, dict) for player in payload
        ):
            raise DriverError("HEOS returned an invalid player list")
        return payload

    @staticmethod
    def _unique_player(matches: list[dict], description: str) -> dict | None:
        if len(matches) > 1:
            raise DriverError(
                f"Multiple HEOS players match {description}: {_player_names(matches)}"
            )
        return matches[0] if matches else None

    def _resolve_player(self, players: list[dict]) -> dict:
        if not players:
            raise DriverError("No HEOS players were found")

        serial = str(self.config.get("serial", "")).strip()
        if serial:
            by_serial = [
                player for player in players if _player_text(player, "serial") == serial
            ]
            player = self._unique_player(by_serial, f"serial {serial}")
            if player is not None:
                return player

        by_ip = [player for player in players if _player_text(player, "ip") == self.host]
        player = self._unique_player(by_ip, self.host)
        if player is not None:
            return player

        requested_name = str(self.config.get("player", "")).strip()
        if requested_name:
            by_name = [
                player
                for player in players
                if _player_text(player, "name").casefold() == requested_name.casefold()
            ]
            if len(by_name) == 1:
                return by_name[0]
            if len(by_name) > 1:
                raise DriverError(
                    f"Multiple HEOS players are named {requested_name!r}: "
                    f"{_player_names(by_name)}"
                )
            raise DriverError(
                f"HEOS player {requested_name!r} was not found. "
                f"Available players: {_player_names(players)}"
            )

        if len(players) == 1:
            return players[0]
        raise DriverError(
            f"Multiple HEOS players found; set the player name. "
            f"Available players: {_player_names(players)}"
        )

    async def _target_pid(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> str:
        player = self._resolve_player(await self._players(reader, writer))
        pid = player.get("pid")
        if pid is None or str(pid).strip() == "":
            raise DriverError(f"HEOS player {player.get('name', '')!r} has no player ID")
        return str(pid)

    async def _get_volume(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, pid: str
    ) -> int:
        response = await _request(reader, writer, "player/get_volume", {"pid": pid})
        try:
            value = int(_response_values(response)["level"])
        except (KeyError, TypeError, ValueError) as exc:
            raise DriverError("HEOS returned an invalid volume level") from exc
        if not self.scale.min <= value <= self.scale.max:
            raise DriverError("HEOS returned a volume outside its 0–100 range")
        return value

    async def _get_mute(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, pid: str
    ) -> bool:
        response = await _request(reader, writer, "player/get_mute", {"pid": pid})
        values = _response_values(response)
        state = values.get("state", values.get("mute"))
        if isinstance(state, bool):
            return state
        normalized = str(state).strip().casefold()
        if normalized == "on":
            return True
        if normalized == "off":
            return False
        raise DriverError("HEOS returned an invalid mute state")

    async def status(self) -> Status:
        async def read(reader, writer):
            pid = await self._target_pid(reader, writer)
            volume = await self._get_volume(reader, writer, pid)
            muted = await self._get_mute(reader, writer, pid)
            return Status(volume=volume, muted=muted)

        return await self._transaction(read)

    async def set_volume(self, raw: int) -> int:
        try:
            value = max(self.scale.min, min(self.scale.max, int(raw)))
        except (TypeError, ValueError) as exc:
            raise DriverError("Invalid HEOS volume value") from exc

        async def write(reader, writer):
            pid = await self._target_pid(reader, writer)
            await _request(
                reader, writer, "player/set_volume", {"pid": pid, "level": value}
            )
            return await self._get_volume(reader, writer, pid)

        return await self._transaction(write)

    async def set_mute(self, muted: bool) -> bool:
        async def write(reader, writer):
            pid = await self._target_pid(reader, writer)
            await _request(
                reader,
                writer,
                "player/set_mute",
                {"pid": pid, "state": "on" if muted else "off"},
            )
            return await self._get_mute(reader, writer, pid)

        return await self._transaction(write)

    @classmethod
    async def discover(cls, timeout: float) -> list[Found]:
        if timeout <= 0:
            return []
        try:
            responses = await ssdp.search(HEOS_SSDP_ST, timeout)
        except Exception:
            return []

        candidates: dict[tuple[str, str], dict[str, str]] = {}
        for response in responses:
            location = str(response.get("location", "")).strip()
            host = str(response.get("ip", "")).strip()
            if location and host:
                candidates[(location, host)] = response

        async def fetch(item: dict[str, str]) -> Found | None:
            try:
                description = await ssdp.fetch_description(item["location"])
            except Exception:
                return None
            model = str(
                description.get("modelName")
                or description.get("friendlyName")
                or "HEOS device"
            ).strip()
            name = str(description.get("friendlyName", "")).strip()
            raw_xml = str(description.get("raw_xml", ""))
            serial = str(
                description.get("serialNumber")
                or _description_value(raw_xml, "serialNumber")
            ).strip()
            udn = str(description.get("UDN", "")).strip()
            host = item["ip"]
            config = {"host": host, "model": model, "player": name, "ip": host}
            if serial:
                config["serial"] = serial
            return Found(
                driver=cls.key,
                model=model,
                id=f"heos:{serial or udn or host}",
                host=host,
                config=config,
            )

        found = await asyncio.gather(*(fetch(item) for item in candidates.values()))
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
        driver = cls(config)

        async def identify() -> dict:
            async with driver._operation_lock():
                reader, writer = await _open(host, driver.port)
                try:
                    await _request(
                        reader, writer, "system/heart_beat", timeout=_REPLY_TIMEOUT
                    )
                    player = driver._resolve_player(await driver._players(reader, writer))
                finally:
                    await _close(writer)
                return player

        try:
            player = await asyncio.wait_for(identify(), _REPLY_TIMEOUT)
        except Exception:
            return None

        model = str(player.get("model") or player.get("name") or "HEOS player")
        name = _player_text(player, "name")
        ip = str(player.get("ip") or host)
        player_serial = _player_text(player, "serial")
        identity = player_serial or ip or host
        found_config = {
            **config,
            "host": host,
            "port": driver.port,
            "model": model,
            "player": name,
            "ip": ip,
        }
        found_config.pop("name", None)
        found_config.pop("serial", None)
        if player_serial:
            found_config["serial"] = player_serial
        return Found(
            driver=cls.key,
            model=model,
            id=str(config.get("id") or f"heos:{identity}"),
            host=host,
            config=found_config,
        )
