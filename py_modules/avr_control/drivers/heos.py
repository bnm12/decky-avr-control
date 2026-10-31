"""Denon HEOS CLI driver for player volume and mute."""

from __future__ import annotations

import asyncio
import json
import xml.etree.ElementTree as ET
from typing import Awaitable, Callable, ClassVar
from urllib.parse import parse_qs, urlencode

from .. import ssdp
from .base import (
    MUTE,
    VOLUME,
    ConfigField,
    Driver,
    DriverError,
    Found,
    ModeState,
    Status,
    VolumeScale,
)
from .receiver_session import ReceiverSessionError, shared_session

HEOS_PORT = 1255
HEOS_SSDP_ST = "urn:schemas-denon-com:device:ACT-Denon:1"
_REPLY_TIMEOUT = 2.0


async def _read_json_line(reader: asyncio.StreamReader) -> dict:
    line = await reader.readline()
    if not line:
        raise DriverError("HEOS closed the connection before replying")
    try:
        response = json.loads(line.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DriverError("HEOS returned an invalid JSON response") from exc
    if not isinstance(response, dict) or not isinstance(response.get("heos"), dict):
        raise DriverError("HEOS returned an invalid response")
    return response


async def _request(
    session,
    command: str,
    args: dict[str, str | int] | None = None,
    *,
    timeout: float = _REPLY_TIMEOUT,
) -> dict:
    request = f"heos://{command}"
    if args:
        request += "?" + urlencode(args)
    def classify(response: dict) -> str | None:
        metadata = response["heos"]
        message = str(metadata.get("message", ""))
        if message.strip().casefold() == "command under process":
            return "interim"
        response_command = "".join(str(metadata.get("command", "")).split())
        if response_command != command:
            return None
        if str(metadata.get("result", "")).casefold() != "success":
            detail = message or str(metadata.get("result", "unknown error"))
            raise DriverError(f"HEOS command {command} failed: {detail}")
        return "response"

    frames = await session.transact(
        [(request + "\r\n").encode("ascii")],
        classify,
        frozenset({"response"}),
        timeout=timeout,
    )
    return frames["response"]


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


def _serial_selector(config: dict) -> str:
    """The selector a learned serial belongs to: the player name, else the host."""
    name = str(config.get("player") or "").strip().casefold()
    return name or str(config.get("host") or "").strip()


def _player_names(players: list[dict]) -> str:
    names = [str(player.get("name") or "(unnamed)") for player in players]
    return ", ".join(names) if names else "none"


class HeosDriver(Driver):
    key: ClassVar[str] = "heos"
    label: ClassVar[str] = "Denon HEOS (speakers, soundbars, HEOS-built-in)"
    identity_keys: ClassVar[tuple[str, ...]] = ("host", "player")
    config_fields: ClassVar[tuple[ConfigField, ...]] = (
        ConfigField("host", "HEOS device IP address"),
        ConfigField("player", "HEOS player name (blank = the device at this IP)", required=False),
    )
    capabilities = frozenset({VOLUME, MUTE})
    scale = VolumeScale(
        min=0, max=100, unit="%", zero=0, steps_per_unit=1, default_cap=60
    )

    def __init__(self, config: dict):
        super().__init__(config)
        self._session = None

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

    def _receiver_session(self):
        if not self.host:
            raise DriverError("Enter the IP address of a HEOS device")
        if self._session is None:
            self._session = shared_session(self.host, self.port, _read_json_line)
        return self._session

    async def _transaction(self, action):
        try:
            async with self._receiver_session()._transaction_group():
                return await action()
        except ReceiverSessionError as exc:
            if exc.kind == "timeout":
                raise DriverError("HEOS did not reply in time") from exc
            if exc.kind == "connect":
                raise DriverError(
                    f"Can't reach HEOS device at {self.host}:{self.port}: {exc}"
                ) from exc
            if exc.kind == "eof":
                raise DriverError("HEOS closed the connection before replying") from exc
            raise
        except DriverError:
            raise
        except Exception as exc:
            raise DriverError(f"HEOS request failed: {exc}") from exc

    async def _request(
        self,
        command: str,
        args: dict[str, str | int] | None = None,
        *,
        timeout: float = _REPLY_TIMEOUT,
    ) -> dict:
        return await _request(self._receiver_session(), command, args, timeout=timeout)

    async def _players(self) -> list[dict]:
        response = await self._request("player/get_players")
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
        if serial and str(self.config.get("serial_for", "")) == _serial_selector(
            self.config
        ):
            by_serial = [
                player for player in players if _player_text(player, "serial") == serial
            ]
            player = self._unique_player(by_serial, f"serial {serial}")
            if player is not None:
                return player

        # An explicitly chosen player name beats "the player at this IP": one
        # HEOS device controls the whole system, so the host may be another player.
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

        by_ip = [player for player in players if _player_text(player, "ip") == self.host]
        player = self._unique_player(by_ip, self.host)
        if player is not None:
            return player

        if len(players) == 1:
            return players[0]
        raise DriverError(
            f"Multiple HEOS players found; set the player name. "
            f"Available players: {_player_names(players)}"
        )

    async def _target_pid(self) -> str:
        player = self._resolve_player(await self._players())
        self._learn_serial(player)
        pid = player.get("pid")
        if pid is None or str(pid).strip() == "":
            raise DriverError(f"HEOS player {player.get('name', '')!r} has no player ID")
        return str(pid)

    def _learn_serial(self, player: dict) -> None:
        serial = _player_text(player, "serial")
        if serial:
            self.config["serial"] = serial
            self.config["serial_for"] = _serial_selector(self.config)
        else:
            self.config.pop("serial", None)
            self.config.pop("serial_for", None)

    async def _get_volume(
        self, pid: str
    ) -> int:
        response = await self._request("player/get_volume", {"pid": pid})
        try:
            value = int(_response_values(response)["level"])
        except (KeyError, TypeError, ValueError) as exc:
            raise DriverError("HEOS returned an invalid volume level") from exc
        if not self.scale.min <= value <= self.scale.max:
            raise DriverError("HEOS returned a volume outside its 0–100 range")
        return value

    async def _get_mute(
        self, pid: str
    ) -> bool:
        response = await self._request("player/get_mute", {"pid": pid})
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
        async def read():
            pid = await self._target_pid()
            volume = await self._get_volume(pid)
            muted = await self._get_mute(pid)
            return Status(volume=volume, muted=muted)

        return await self._transaction(read)

    async def watch(
        self, on_change: Callable[[Status], None]
    ) -> Callable[[], Awaitable[None]] | None:
        session = self._receiver_session()
        target: dict[str, str | None] = {"pid": None}

        def receive(response: dict) -> None:
            metadata = response.get("heos", {})
            command = "".join(str(metadata.get("command", "")).split())
            if command != "event/player_volume_changed":
                return
            values = _response_values(response)
            if str(values.get("pid", "")) != target["pid"]:
                return

            volume = None
            raw_volume = values.get("level", values.get("volume"))
            if raw_volume is not None:
                try:
                    parsed = int(raw_volume)
                except (TypeError, ValueError):
                    return
                if not self.scale.min <= parsed <= self.scale.max:
                    return
                volume = parsed

            muted = None
            raw_mute = values.get("mute", values.get("state"))
            if isinstance(raw_mute, bool):
                muted = raw_mute
            elif str(raw_mute).strip().casefold() in {"on", "off"}:
                muted = str(raw_mute).strip().casefold() == "on"
            if volume is not None or muted is not None:
                on_change(Status(volume=volume, muted=muted))

        session.subscribe(receive)
        try:
            async def register() -> None:
                target["pid"] = await self._target_pid()
                await self._request(
                    "system/register_for_change_events", {"enable": "on"}
                )

            await self._transaction(register)
        except asyncio.CancelledError:
            await session.unsubscribe(receive)
            raise
        except DriverError:
            await session.unsubscribe(receive)
            return None

        registration = {"generation": session.connection_generation, "active": True}
        registration_lock = asyncio.Lock()
        reconnect_tasks: set[asyncio.Task] = set()

        async def register_after_reconnect(generation: int) -> None:
            async with registration_lock:
                if (
                    not registration["active"]
                    or generation <= registration["generation"]
                ):
                    return

                async def register_again() -> None:
                    target["pid"] = await self._target_pid()
                    await self._request(
                        "system/register_for_change_events", {"enable": "on"}
                    )

                try:
                    await self._transaction(register_again)
                    registration["generation"] = session.connection_generation
                except DriverError:
                    return

        def connected(generation: int) -> None:
            task = asyncio.create_task(register_after_reconnect(generation))
            reconnect_tasks.add(task)
            task.add_done_callback(reconnect_tasks.discard)

        unsubscribe_connect = session.on_connect(connected)

        async def unsubscribe() -> None:
            registration["active"] = False
            unsubscribe_connect()
            pending = tuple(reconnect_tasks)
            for task in pending:
                if not task.done():
                    task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            await session.unsubscribe(receive)

        return unsubscribe

    async def mode_state(self, status: Status | None = None) -> ModeState:
        return ModeState(None, (), complete=False)

    @classmethod
    def mode_label(cls, code: str) -> str:
        return f"Mode {code}"

    async def set_volume(self, raw: int) -> int:
        try:
            value = max(self.scale.min, min(self.scale.max, int(raw)))
        except (TypeError, ValueError) as exc:
            raise DriverError("Invalid HEOS volume value") from exc

        async def write():
            pid = await self._target_pid()
            await self._request(
                "player/set_volume", {"pid": pid, "level": value}
            )
            return await self._get_volume(pid)

        return await self._transaction(write)

    async def set_mute(self, muted: bool) -> bool:
        async def write():
            pid = await self._target_pid()
            await self._request(
                "player/set_mute",
                {"pid": pid, "state": "on" if muted else "off"},
            )
            return await self._get_mute(pid)

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
                config["serial_for"] = _serial_selector(config)
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
            async with driver._receiver_session()._transaction_group():
                await driver._request(
                    "system/heart_beat", timeout=_REPLY_TIMEOUT
                )
                return driver._resolve_player(await driver._players())

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
        found_config.pop("serial_for", None)
        if player_serial:
            found_config["serial"] = player_serial
            found_config["serial_for"] = _serial_selector(found_config)
        return Found(
            driver=cls.key,
            model=model,
            id=str(config.get("id") or f"heos:{identity}"),
            host=host,
            config=found_config,
        )
