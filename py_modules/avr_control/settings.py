"""Normalized settings values and the persistence seam."""

from __future__ import annotations

import copy
import json
import os
import tempfile
from dataclasses import dataclass, field
from typing import Any, Protocol

CURRENT_VERSION = 4
CURRENT_SCHEMA_VERSION = CURRENT_VERSION
_SECRET_NAMES = frozenset(
    {"token", "password", "secret", "api_key", "access_token", "authorization"}
)
_V1_KEYS = {
    "version",
    "host",
    "port",
    "proto",
    "id",
    "max",
    "late_max",
    "late",
    "games",
}
_V2_KEYS = {
    "version",
    "receiver",
    "cap",
    "cap_receiver",
    "late_cap",
    "late_cap_receiver",
    "late",
    "late_receiver",
    "games",
}


def _secret_key(key: Any, secret_keys: set[str] | frozenset[str]) -> bool:
    lowered = str(key).lower()
    return lowered in _SECRET_NAMES or lowered in {
        item.lower() for item in secret_keys
    }


def _masked(value: Any, secret_keys: set[str] | frozenset[str]) -> Any:
    if isinstance(value, dict):
        return {
            key: "***" if _secret_key(key, secret_keys) else _masked(item, secret_keys)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_masked(item, secret_keys) for item in value]
    if isinstance(value, tuple):
        return tuple(_masked(item, secret_keys) for item in value)
    return value


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _as_dict(value: Any) -> dict[str, Any]:
    return copy.deepcopy(value) if isinstance(value, dict) else {}


def _as_string(value: Any, default: str | None = None) -> str | None:
    return value if isinstance(value, str) else default


@dataclass
class ReceiverSettings:
    driver: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    id: str | None = None
    binding: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict, repr=False)
    secret_keys: frozenset[str] = field(default_factory=frozenset, repr=False)

    def __repr__(self) -> str:
        config = _masked(self.config, self.secret_keys)
        extra = _masked(self.extra, self.secret_keys)
        binding = _masked(self.binding, self.secret_keys)
        return (
            "ReceiverSettings("
            f"driver={self.driver!r}, config={config!r}, id={self.id!r}, "
            f"binding={binding!r}, extra={extra!r})"
        )


@dataclass
class CapSettings:
    cap: int | None = None
    binding: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict, repr=False)

    def __repr__(self) -> str:
        return (
            f"CapSettings(cap={self.cap!r}, "
            f"binding={_masked(self.binding, frozenset())!r})"
        )


@dataclass
class LateNightSettings:
    active: bool = False
    prev_volume: int | None = None
    auto: bool = False
    cap: int | None = None
    cap_binding: dict[str, Any] | None = None
    binding: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class ScheduleSettings:
    enabled: bool = False
    start: str = "22:00"
    end: str = "07:00"
    mode: str | None = None
    override_window: str | None = None
    last_late_window: str | None = None
    last_mode_window: str | None = None
    extra: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class SleepWakeSettings:
    wake_on_resume: bool = False
    off_on_suspend: bool = False
    extra: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class GameProfile:
    appid: int
    name: str = ""
    driver: str | None = None
    volume: int | None = None
    mode: str | None = None
    extra: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class Settings:
    version: int = CURRENT_VERSION
    receiver: ReceiverSettings = field(default_factory=ReceiverSettings)
    cap: CapSettings = field(default_factory=CapSettings)
    late: LateNightSettings = field(default_factory=LateNightSettings)
    schedule: ScheduleSettings = field(default_factory=ScheduleSettings)
    sleep_wake: SleepWakeSettings = field(default_factory=SleepWakeSettings)
    games: dict[str, GameProfile] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict, repr=False)
    secret_keys: frozenset[str] = field(default_factory=frozenset, repr=False)

    def __post_init__(self) -> None:
        self.receiver.secret_keys = frozenset(
            set(self.receiver.secret_keys) | set(self.secret_keys)
        )

    def to_dict(self) -> dict[str, Any]:
        receiver = copy.deepcopy(self.receiver.extra)
        receiver.update(
            {
                "driver": self.receiver.driver,
                "config": copy.deepcopy(self.receiver.config),
            }
        )
        if self.receiver.id is not None:
            receiver["id"] = self.receiver.id
        if self.receiver.binding is not None:
            receiver["binding"] = copy.deepcopy(self.receiver.binding)

        late = copy.deepcopy(self.late.extra)
        late["active"] = bool(self.late.active)
        if self.late.prev_volume is not None:
            late["prev_volume"] = self.late.prev_volume
        if self.late.auto:
            late["auto"] = True

        schedule = copy.deepcopy(self.schedule.extra)
        schedule.update(
            {
                "enabled": bool(self.schedule.enabled),
                "start": self.schedule.start,
                "end": self.schedule.end,
                "mode": self.schedule.mode,
            }
        )
        if self.schedule.override_window is not None:
            schedule["override_window"] = self.schedule.override_window
        if self.schedule.last_late_window is not None:
            schedule["last_late_window"] = self.schedule.last_late_window
        if self.schedule.last_mode_window is not None:
            schedule["last_mode_window"] = self.schedule.last_mode_window

        sleep_wake = copy.deepcopy(self.sleep_wake.extra)
        sleep_wake.update(
            {
                "wake_on_resume": bool(self.sleep_wake.wake_on_resume),
                "off_on_suspend": bool(self.sleep_wake.off_on_suspend),
            }
        )

        games: dict[str, Any] = {}
        for key, profile in self.games.items():
            saved = copy.deepcopy(profile.extra)
            saved.update({"name": profile.name, "driver": profile.driver})
            if profile.volume is not None:
                saved["volume"] = profile.volume
            if profile.mode is not None:
                saved["mode"] = profile.mode
            games[str(key)] = saved

        data = copy.deepcopy(self.extra)
        data.update(
            {
                "version": CURRENT_VERSION,
                "receiver": receiver,
                "cap": self.cap.cap,
                "late_cap": self.late.cap,
                "late": late,
                "schedule": schedule,
                "sleep_wake": sleep_wake,
                "games": games,
            }
        )
        if self.cap.binding is not None:
            data["cap_receiver"] = copy.deepcopy(self.cap.binding)
        else:
            data.pop("cap_receiver", None)
        if self.late.cap_binding is not None:
            data["late_cap_receiver"] = copy.deepcopy(self.late.cap_binding)
        else:
            data.pop("late_cap_receiver", None)
        if self.late.binding is not None:
            data["late_receiver"] = copy.deepcopy(self.late.binding)
        else:
            data.pop("late_receiver", None)
        return data

    def __repr__(self) -> str:
        safe_extra = _masked(self.extra, self.secret_keys)
        safe_late_extra = _masked(self.late.extra, self.secret_keys)
        safe_profiles = {
            key: {
                "appid": profile.appid,
                "name": profile.name,
                "driver": profile.driver,
                "volume": profile.volume,
                "mode": profile.mode,
                "extra": _masked(profile.extra, self.secret_keys),
            }
            for key, profile in self.games.items()
        }
        late = (
            "LateNightSettings("
            f"active={self.late.active!r}, prev_volume={self.late.prev_volume!r}, "
            f"cap={self.late.cap!r}, "
            f"cap_binding={_masked(self.late.cap_binding, self.secret_keys)!r}, "
            f"binding={_masked(self.late.binding, self.secret_keys)!r}, "
            f"extra={safe_late_extra!r})"
        )
        cap = (
            "CapSettings("
            f"cap={self.cap.cap!r}, "
            f"binding={_masked(self.cap.binding, self.secret_keys)!r})"
        )
        return (
            "Settings("
            f"version={CURRENT_VERSION!r}, receiver={self.receiver!r}, "
            f"cap={cap}, late={late}, games={safe_profiles!r}, "
            f"extra={safe_extra!r})"
        )


def _known_driver(proto: Any) -> str | None:
    if proto == "telnet":
        return "pioneer_telnet"
    if proto in ("eiscp", "pioneer_telnet"):
        return proto
    return None


def _migrate_v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    proto = data.get("proto")
    driver = _known_driver(proto)
    receiver_config: dict[str, Any] = {}
    if data.get("host"):
        receiver_config["host"] = data["host"]
    if data.get("port") is not None:
        receiver_config["port"] = data["port"]

    old_late = data.get("late") if isinstance(data.get("late"), dict) else {}
    late = {
        key: copy.deepcopy(value)
        for key, value in old_late.items()
        if key not in {"active", "prev_volume"}
    }
    late["active"] = old_late.get("active", False)
    if old_late.get("prev_volume") is not None:
        late["prev_volume"] = old_late["prev_volume"]

    games: dict[str, dict[str, Any]] = {}
    old_games = data.get("games") if isinstance(data.get("games"), dict) else {}
    for appid, old_profile in old_games.items():
        if not isinstance(old_profile, dict):
            continue
        profile: dict[str, Any] = {
            key: copy.deepcopy(value)
            for key, value in old_profile.items()
            if key not in {"name", "volume", "mode"}
        }
        profile.update({"name": old_profile.get("name", ""), "driver": driver})
        for key in ("volume", "mode"):
            if old_profile.get(key) is not None:
                profile[key] = old_profile[key]
        games[str(appid)] = profile

    migrated: dict[str, Any] = {
        key: copy.deepcopy(value)
        for key, value in data.items()
        if key not in _V1_KEYS
    }
    migrated.update(
        {
            "version": 2,
            "receiver": {"driver": driver, "config": receiver_config},
            "cap": data.get("max"),
            "late_cap": data.get("late_max"),
            "late": late,
            "games": games,
        }
    )
    if data.get("id") is not None:
        migrated["receiver"]["id"] = data["id"]
    return migrated


def _migrate_v2_to_v3(data: dict[str, Any]) -> dict[str, Any]:
    migrated = copy.deepcopy(data)
    migrated["version"] = 3
    return migrated


def _migrate_v3_to_v4(data: dict[str, Any]) -> dict[str, Any]:
    migrated = copy.deepcopy(data)
    migrated["version"] = 4
    schedule = migrated.get("schedule")
    if not isinstance(schedule, dict):
        schedule = {}
    schedule.setdefault("enabled", False)
    schedule.setdefault("start", "22:00")
    schedule.setdefault("end", "07:00")
    schedule.setdefault("mode", None)
    migrated["schedule"] = schedule
    sleep_wake = migrated.get("sleep_wake")
    if not isinstance(sleep_wake, dict):
        sleep_wake = {}
    sleep_wake.setdefault("wake_on_resume", False)
    sleep_wake.setdefault("off_on_suspend", False)
    migrated["sleep_wake"] = sleep_wake
    return migrated


def migrate(data: Any) -> dict[str, Any]:
    """Apply ordered, idempotent migrations to the current settings schema."""
    result = copy.deepcopy(data) if isinstance(data, dict) else {}
    version = result.get("version", 1)
    if not isinstance(version, int) or isinstance(version, bool):
        version = (
            2
            if isinstance(result.get("receiver"), dict)
            else 1
        )
    if version < 2:
        result = _migrate_v1_to_v2(result)
        version = 2
    if version < 3:
        result = _migrate_v2_to_v3(result)
        version = 3
    if version < CURRENT_VERSION:
        result = _migrate_v3_to_v4(result)
    elif version > CURRENT_VERSION:
        result["version"] = CURRENT_VERSION
    return result


def _settings_from_data(data: dict[str, Any], secret_keys: frozenset[str]) -> Settings:
    receiver_data = data.get("receiver")
    if not isinstance(receiver_data, dict):
        receiver_data = {}
    receiver_known = {"driver", "config", "id", "binding"}
    receiver = ReceiverSettings(
        driver=_as_string(receiver_data.get("driver")),
        config=_as_dict(receiver_data.get("config")),
        id=_as_string(receiver_data.get("id")),
        binding=_as_dict(receiver_data.get("binding")) or None,
        extra={
            key: copy.deepcopy(value)
            for key, value in receiver_data.items()
            if key not in receiver_known
        },
        secret_keys=secret_keys,
    )

    late_data = data.get("late")
    if not isinstance(late_data, dict):
        late_data = {}
    previous_volume = _as_int(late_data.get("prev_volume"))
    late_known = {"active", "prev_volume", "auto"}
    late = LateNightSettings(
        active=late_data.get("active")
        if isinstance(late_data.get("active"), bool)
        else False,
        prev_volume=previous_volume,
        auto=late_data.get("auto")
        if isinstance(late_data.get("auto"), bool)
        else False,
        cap=_as_int(data.get("late_cap")),
        cap_binding=_as_dict(data.get("late_cap_receiver")) or None,
        binding=_as_dict(data.get("late_receiver")) or None,
        extra={
            key: copy.deepcopy(value)
            for key, value in late_data.items()
            if key not in late_known
        },
    )

    schedule_data = data.get("schedule")
    if not isinstance(schedule_data, dict):
        schedule_data = {}

    def valid_time(value: Any, default: str) -> str:
        if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
            return default
        hour, minute = value[:2], value[3:]
        if (
            not hour.isdigit()
            or not minute.isdigit()
            or int(hour) > 23
            or int(minute) > 59
        ):
            return default
        return value

    schedule_known = {
        "enabled",
        "start",
        "end",
        "mode",
        "override_window",
        "last_late_window",
        "last_mode_window",
    }
    schedule = ScheduleSettings(
        enabled=schedule_data.get("enabled")
        if isinstance(schedule_data.get("enabled"), bool)
        else False,
        start=valid_time(schedule_data.get("start"), "22:00"),
        end=valid_time(schedule_data.get("end"), "07:00"),
        mode=_as_string(schedule_data.get("mode")),
        override_window=_as_string(schedule_data.get("override_window")),
        last_late_window=_as_string(schedule_data.get("last_late_window")),
        last_mode_window=_as_string(schedule_data.get("last_mode_window")),
        extra={
            key: copy.deepcopy(value)
            for key, value in schedule_data.items()
            if key not in schedule_known
        },
    )

    sleep_wake_data = data.get("sleep_wake")
    if not isinstance(sleep_wake_data, dict):
        sleep_wake_data = {}
    sleep_wake = SleepWakeSettings(
        wake_on_resume=sleep_wake_data.get("wake_on_resume")
        if isinstance(sleep_wake_data.get("wake_on_resume"), bool)
        else False,
        off_on_suspend=sleep_wake_data.get("off_on_suspend")
        if isinstance(sleep_wake_data.get("off_on_suspend"), bool)
        else False,
        extra={
            key: copy.deepcopy(value)
            for key, value in sleep_wake_data.items()
            if key not in {"wake_on_resume", "off_on_suspend"}
        },
    )

    games: dict[str, GameProfile] = {}
    raw_games = data.get("games")
    if isinstance(raw_games, dict):
        for raw_appid, raw_profile in raw_games.items():
            appid = _as_int(raw_appid)
            if appid is None or not isinstance(raw_profile, dict):
                continue
            profile_known = {"name", "driver", "volume", "mode"}
            games[str(appid)] = GameProfile(
                appid=appid,
                name=_as_string(raw_profile.get("name"), "") or "",
                driver=_as_string(raw_profile.get("driver")),
                volume=_as_int(raw_profile.get("volume")),
                mode=_as_string(raw_profile.get("mode")),
                extra={
                    key: copy.deepcopy(value)
                    for key, value in raw_profile.items()
                    if key not in profile_known
                },
            )

    known = _V2_KEYS | {"receiver", "schedule", "sleep_wake"}
    return Settings(
        version=CURRENT_VERSION,
        receiver=receiver,
        cap=CapSettings(
            cap=_as_int(data.get("cap")),
            binding=_as_dict(data.get("cap_receiver")) or None,
        ),
        late=late,
        schedule=schedule,
        sleep_wake=sleep_wake,
        games=games,
        extra={
            key: copy.deepcopy(value)
            for key, value in data.items()
            if key not in known
        },
        secret_keys=secret_keys,
    )


class FileSettingsStore:
    """Filesystem adapter; writes JSON atomically and adopts injected legacy paths."""

    def __init__(
        self, path: str, legacy_paths: list[str] | tuple[str, ...] = ()
    ):
        self.path = os.fspath(path)
        self.legacy_paths = tuple(os.fspath(item) for item in legacy_paths)
        self.adopted_legacy = False

    def load(self) -> dict[str, Any]:
        self.adopted_legacy = False
        try:
            with open(self.path, "r", encoding="utf-8") as settings_file:
                data = json.load(settings_file)
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            for legacy_path in self.legacy_paths:
                try:
                    with open(legacy_path, "r", encoding="utf-8") as legacy_file:
                        legacy_data = json.load(legacy_file)
                except (OSError, ValueError, TypeError):
                    continue
                self.adopted_legacy = True
                return legacy_data if isinstance(legacy_data, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}
        return {}

    def save(self, data: dict[str, Any]) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=directory,
                prefix=".settings-",
                suffix=".tmp",
                delete=False,
            ) as settings_file:
                temporary_path = settings_file.name
                json.dump(data, settings_file)
                settings_file.flush()
                os.fsync(settings_file.fileno())
            os.replace(temporary_path, self.path)
            self.adopted_legacy = False
        finally:
            if temporary_path and os.path.exists(temporary_path):
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass


class MemorySettingsStore:
    """In-memory persistence adapter with the same raw-data contract as the file store."""

    def __init__(
        self,
        data: dict[str, Any] | None = None,
        legacy_data: dict[str, Any] | None = None,
    ):
        self.data = copy.deepcopy(data) if isinstance(data, dict) else {}
        self._has_data = isinstance(data, dict)
        self.legacy_data = copy.deepcopy(legacy_data)
        self.adopted_legacy = False

    def load(self) -> dict[str, Any]:
        self.adopted_legacy = False
        if not self._has_data and self.legacy_data is not None:
            self.adopted_legacy = True
            return copy.deepcopy(self.legacy_data)
        return copy.deepcopy(self.data)

    def save(self, data: dict[str, Any]) -> None:
        self.data = copy.deepcopy(data)
        self._has_data = True
        self.adopted_legacy = False


class SettingsStoreAdapter(Protocol):
    def load(self) -> dict[str, Any]: ...

    def save(self, data: dict[str, Any]) -> None: ...


class SettingsRepository:
    """Load and save normalized settings through an injected persistence adapter."""

    def __init__(
        self,
        store: SettingsStoreAdapter | str | os.PathLike | None = None,
        *,
        path: str | os.PathLike | None = None,
        legacy_paths: list[str] | tuple[str, ...] = (),
        logger=None,
        secret_keys: set[str] | frozenset[str] = frozenset(),
    ):
        if isinstance(store, (str, os.PathLike)):
            path = store
            store = None
        if store is None:
            if path is None:
                raise TypeError("SettingsRepository requires a store or path")
            store = FileSettingsStore(os.fspath(path), legacy_paths)
        self.store = store
        self.logger = logger
        self.secret_keys = frozenset(secret_keys)
        self._memory_settings: Settings | None = None

    def load(self) -> Settings:
        if self._memory_settings is not None:
            return copy.deepcopy(self._memory_settings)

        raw_data = self.store.load()
        if not isinstance(raw_data, dict):
            raw_data = {}
        migrated = migrate(raw_data)
        settings = _settings_from_data(migrated, self.secret_keys)
        canonical = settings.to_dict()
        if migrated != raw_data or getattr(self.store, "adopted_legacy", False):
            self._save_migration(settings)
        elif canonical != raw_data:
            self._save_migration(settings)
        return settings

    def _save_migration(self, settings: Settings) -> None:
        try:
            self.store.save(settings.to_dict())
        except OSError:
            self._memory_settings = copy.deepcopy(settings)
            error = getattr(self.logger, "error", None)
            if error:
                error("Couldn't save migrated settings; keeping them in memory")

    def save(self, settings: Settings) -> None:
        if not isinstance(settings, Settings):
            raise TypeError("SettingsRepository.save expects Settings")
        normalized = _settings_from_data(
            migrate(settings.to_dict()), self.secret_keys
        )
        try:
            self.store.save(normalized.to_dict())
        except OSError:
            raise
        self._memory_settings = None


class SettingsStore(FileSettingsStore):
    """Compatibility name for the filesystem adapter."""


def load_with_legacy(
    path: str, legacy_paths: list[str] | tuple[str, ...]
) -> dict[str, Any]:
    """Compatibility helper returning the normalized persisted representation."""
    return SettingsRepository(path, legacy_paths=legacy_paths).load().to_dict()
