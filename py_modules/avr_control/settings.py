"""Versioned settings persistence and migration from Pioneer Volume."""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any


def _known_driver(proto: Any) -> str | None:
    if proto == "telnet":
        return "pioneer_telnet"
    if proto in ("eiscp", "pioneer_telnet"):
        return proto
    return None


def migrate(data: dict) -> dict:
    """Convert the old flat settings dictionary into the version 2 schema."""
    data = data if isinstance(data, dict) else {}
    if data.get("version") == 2:
        return data

    proto = data.get("proto")
    driver = _known_driver(proto)
    receiver_config: dict[str, Any] = {}
    if data.get("host"):
        receiver_config["host"] = data["host"]
    if data.get("port") is not None:
        receiver_config["port"] = data["port"]

    old_late = data.get("late") if isinstance(data.get("late"), dict) else {}
    late = {"active": bool(old_late.get("active", False))}
    if old_late.get("prev_volume") is not None:
        late["prev_volume"] = old_late["prev_volume"]

    games: dict[str, dict] = {}
    old_games = data.get("games") if isinstance(data.get("games"), dict) else {}
    profile_driver = _known_driver(proto)
    for appid, old_profile in old_games.items():
        if not isinstance(old_profile, dict):
            continue
        profile: dict[str, Any] = {
            "name": old_profile.get("name", ""),
            "driver": profile_driver,
        }
        for key in ("volume", "mode"):
            if old_profile.get(key) is not None:
                profile[key] = old_profile[key]
        games[str(appid)] = profile

    receiver = {"driver": driver, "config": receiver_config}
    if data.get("id") is not None:
        receiver["id"] = data["id"]
    migrated = {
        "version": 2,
        "receiver": receiver,
        "cap": data.get("max"),
        "late_cap": data.get("late_max"),
        "late": late,
        "games": games,
    }
    return migrated


class SettingsStore:
    def __init__(
        self, path: str, legacy_paths: list[str] | tuple[str, ...] = (), logger=None
    ):
        self.path = os.fspath(path)
        self.legacy_paths = tuple(os.fspath(path) for path in legacy_paths)
        self.logger = logger
        self._memory_data: dict | None = None

    def load(self) -> dict:
        if self._memory_data is not None:
            return self._memory_data
        try:
            with open(self.path, "r", encoding="utf-8") as settings_file:
                data = json.load(settings_file)
        except FileNotFoundError:
            for legacy_path in self.legacy_paths:
                try:
                    with open(legacy_path, "r", encoding="utf-8") as legacy_file:
                        legacy_data = json.load(legacy_file)
                except (OSError, ValueError, TypeError):
                    continue
                migrated = migrate(
                    legacy_data if isinstance(legacy_data, dict) else {}
                )
                self._save_migration(migrated)
                return migrated
            return migrate({})
        except (OSError, ValueError, TypeError):
            return migrate({})
        if not isinstance(data, dict):
            return migrate({})
        migrated = migrate(data)
        if migrated is not data:
            self._save_migration(migrated)
        return migrated

    def _save_migration(self, data: dict) -> None:
        try:
            self.save(data)
        except OSError as exc:
            self._memory_data = data
            error = getattr(self.logger, "error", None)
            if error:
                error(f"Couldn't save migrated settings: {exc}")

    def save(self, data: dict) -> None:
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
            self._memory_data = None
        finally:
            if temporary_path and os.path.exists(temporary_path):
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass


def load_with_legacy(path: str, legacy_paths: list[str] | tuple[str, ...]) -> dict:
    """Load current settings, or adopt the first available old-plugin file."""
    return SettingsStore(path, legacy_paths=legacy_paths).load()
