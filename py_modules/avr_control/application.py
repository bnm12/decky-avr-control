"""Receiver-independent application behaviour for the Decky adapter."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time
from datetime import date, timedelta
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TypeAlias

from .drivers import REGISTRY, discover_all, get_driver_class, probe_all
from .drivers.base import (
    MODE,
    MUTE,
    POWER,
    VOLUME,
    Driver,
    DriverError,
    Found,
    ModeState,
    Refused,
    Status,
    Unsupported,
    VolumeScale,
)
from .settings import GameProfile, Settings, SettingsRepository
from .volume import VolumePolicy

_CAPABILITY_ORDER = (VOLUME, MUTE, POWER, MODE)
_SCHEDULE_MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class SetVolume:
    display: float


@dataclass(frozen=True)
class SetMute:
    muted: bool


@dataclass(frozen=True)
class SetPower:
    on: bool


@dataclass(frozen=True)
class SetMode:
    code: str


@dataclass(frozen=True)
class SetLateNight:
    on: bool


@dataclass(frozen=True)
class SetSchedule:
    enabled: bool
    start: str
    end: str
    mode: str | None


@dataclass(frozen=True)
class SetSleepWake:
    wake_on_resume: bool
    off_on_suspend: bool


@dataclass(frozen=True)
class SystemResumed:
    pass


@dataclass(frozen=True)
class SystemSuspending:
    pass


@dataclass(frozen=True)
class PanelOpened:
    pass


@dataclass(frozen=True)
class PanelClosed:
    pass


@dataclass(frozen=True)
class SetCaps:
    cap_display: float
    late_cap_display: float


@dataclass(frozen=True)
class ConfigureReceiver:
    driver_key: str
    config: dict[str, Any]


@dataclass(frozen=True)
class SelectReceiver:
    id: str


@dataclass(frozen=True)
class GameStarted:
    appid: int
    name: str


@dataclass(frozen=True)
class GameStopped:
    appid: int


@dataclass(frozen=True)
class SetCurrentGame:
    appid: int
    name: str


@dataclass(frozen=True)
class RememberForGame:
    what: str
    on: bool


@dataclass(frozen=True)
class ForgetGame:
    appid: int


ControlIntent: TypeAlias = (
    SetVolume
    | SetMute
    | SetPower
    | SetMode
    | SetLateNight
    | SetSchedule
    | SetSleepWake
    | SystemResumed
    | SystemSuspending
    | PanelOpened
    | PanelClosed
    | SetCaps
    | ConfigureReceiver
    | SelectReceiver
    | GameStarted
    | GameStopped
    | SetCurrentGame
    | RememberForGame
    | ForgetGame
)


class ControlApplication:
    def __init__(
        self,
        store: SettingsRepository,
        registry=None,
        logger=None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], object] = asyncio.sleep,
        wall_clock: Callable[[], float] = time.time,
        localtime: Callable[[float], time.struct_time] = time.localtime,
        reapply_delay: float = 4.0,
        rediscover_interval: float = 30.0,
        schedule_interval: float = 30.0,
        resume_window: float = 20.0,
        resume_retry_interval: float = 5.0,
        suspend_timeout: float = 1.5,
        publish: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
    ):
        self.store = store
        self.registry = registry if registry is not None else REGISTRY
        self.logger = logger
        self.clock = clock
        self.sleep = sleep
        self.wall_clock = wall_clock
        self.localtime = localtime
        self.reapply_delay = reapply_delay
        self.rediscover_interval = rediscover_interval
        self.schedule_interval = schedule_interval
        self.resume_window = resume_window
        self.resume_retry_interval = resume_retry_interval
        self.suspend_timeout = suspend_timeout
        self.publish = publish
        self._active_driver: Driver | None = None
        self._receiver_generation = 0
        self._initialized_driver: Driver | None = None
        self._last_status: tuple[int, Driver, Status] | None = None
        self._last_rediscover: float | None = None
        self._last_found: list[Found] = []
        self._current_game: dict | None = None
        self._suppress_save_until: tuple[int, int, float] | None = None
        self._reapply_task: asyncio.Task | None = None
        self._schedule_task: asyncio.Task | None = None
        self._resume_task: asyncio.Task | None = None
        self._suspend_task: asyncio.Task | None = None
        self._schedule_generation = 0
        self._schedule_config: tuple | None = None
        self._schedule_window_key: str | None = None
        self._schedule_attempts: dict[tuple[str, str], int] = {}
        self._schedule_exhausted_logged: set[tuple[str, str]] = set()
        self._schedule_late_done = False
        self._schedule_mode_done = False
        self._panel_open = False
        self._watch_unsubscribe: Callable[[], Awaitable[None]] | None = None
        self._watch_driver: Driver | None = None
        self._watch_generation: int | None = None
        self._push_active = False
        self._revision = 0
        self._push_sequence = 0
        self._push_field_sequences: dict[str, int] = {}
        self._watch_start_token = 0
        self._watch_lock = asyncio.Lock()
        self._push_tasks: set[asyncio.Task] = set()

    def _log(self, level: str, message: str) -> None:
        log = getattr(self.logger, level, None)
        if log:
            log(message)

    async def _stop_watch_locked(self, invalidate_start: bool = True) -> None:
        if invalidate_start:
            self._watch_start_token += 1
        self._push_active = False
        self._watch_driver = None
        self._watch_generation = None
        unsubscribe = self._watch_unsubscribe
        self._watch_unsubscribe = None
        if unsubscribe is not None:
            try:
                await unsubscribe()
            except Exception as exc:
                self._log("debug", f"Receiver watch unsubscribe failed: {exc}")

    async def _stop_watch(self) -> None:
        async with self._watch_lock:
            await self._stop_watch_locked()

    async def _start_watch(self) -> None:
        if not self._panel_open or self.publish is None:
            return
        generation = self._receiver_generation
        start_token: int | None = None
        try:
            driver = await self._get_driver(generation)
            async with self._watch_lock:
                if (
                    not self._panel_open
                    or not self._is_current(generation, driver)
                ):
                    return
                if self._watch_generation == generation and self._watch_driver is driver:
                    return
                self._watch_start_token += 1
                start_token = self._watch_start_token
                await self._stop_watch_locked(invalidate_start=False)
                self._watch_generation = generation
                self._watch_driver = driver

            await self._run("status", generation=generation, driver=driver)
            if (
                start_token != self._watch_start_token
                or not self._panel_open
                or not self._is_current(generation, driver)
            ):
                return

            def on_change(partial: Status) -> None:
                if (
                    start_token != self._watch_start_token
                    or not self._panel_open
                    or not self._push_active
                    or self._watch_driver is not driver
                    or self._watch_generation != generation
                    or not self._is_current(generation, driver)
                ):
                    return
                cached = self._last_status
                status = (
                    Status(
                        cached[2].volume,
                        cached[2].muted,
                        cached[2].power,
                        cached[2].mode,
                    )
                    if cached is not None
                    and cached[0] == generation
                    and cached[1] is driver
                    else Status()
                )
                for field in ("volume", "muted", "power", "mode"):
                    value = getattr(partial, field)
                    if value is not None:
                        setattr(status, field, value)
                self._push_sequence += 1
                for field in ("volume", "muted", "power", "mode"):
                    if getattr(partial, field) is not None:
                        self._push_field_sequences[field] = self._push_sequence
                self._last_status = (generation, driver, status)
                self._revision += 1
                revision = self._revision
                task = asyncio.create_task(
                    self._publish_push_snapshot(
                        revision, generation, driver, start_token
                    )
                )
                self._push_tasks.add(task)
                task.add_done_callback(self._push_tasks.discard)

            self._push_active = True
            unsubscribe = await driver.watch(on_change)
            if unsubscribe is None:
                async with self._watch_lock:
                    if (
                        start_token == self._watch_start_token
                        and self._watch_generation == generation
                        and self._watch_driver is driver
                    ):
                        self._push_active = False
                        self._watch_driver = None
                        self._watch_generation = None
                return
            async with self._watch_lock:
                if (
                    start_token != self._watch_start_token
                    or not self._panel_open
                    or not self._is_current(generation, driver)
                    or self._watch_driver is not driver
                    or self._watch_generation != generation
                ):
                    await unsubscribe()
                    return
                self._watch_unsubscribe = unsubscribe
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._log("debug", f"Receiver push watch unavailable: {exc}")
            async with self._watch_lock:
                if (
                    start_token is not None
                    and start_token == self._watch_start_token
                    and self._watch_generation == generation
                ):
                    await self._stop_watch_locked()

    async def _publish_push_snapshot(
        self, revision: int, generation: int, driver: Driver, start_token: int
    ) -> None:
        if (
            start_token != self._watch_start_token
            or not self._push_active
            or not self._is_current(generation, driver)
            or self._watch_generation != generation
        ):
            return
        try:
            snapshot = await self._snapshot(refresh=False)
            if (
                start_token != self._watch_start_token
                or not self._push_active
                or not self._is_current(generation, driver)
                or self._watch_generation != generation
            ):
                return
            snapshot["revision"] = revision
            snapshot["push"] = True
            callback = self.publish
            if callback is not None:
                result = callback(snapshot)
                if inspect.isawaitable(result):
                    await result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._log("error", f"Push snapshot publish failed: {exc}")
            async with self._watch_lock:
                if (
                    start_token == self._watch_start_token
                    and self._watch_generation == generation
                    and self._watch_driver is driver
                ):
                    await self._stop_watch_locked()

    def _cancel_reapply(self) -> None:
        task = self._reapply_task
        self._reapply_task = None
        if task is not None and not task.done():
            task.cancel()

    @staticmethod
    def _schedule_minutes(value: str) -> int | None:
        if (
            not isinstance(value, str)
            or len(value) != 5
            or value[2] != ":"
            or not value[:2].isdigit()
            or not value[3:].isdigit()
        ):
            return None
        hour, minute = int(value[:2]), int(value[3:])
        if hour > 23 or minute > 59:
            return None
        return hour * 60 + minute

    def _schedule_window(self, schedule, timestamp: float) -> tuple[bool, str | None]:
        if not schedule.enabled:
            return False, None
        start = self._schedule_minutes(schedule.start)
        end = self._schedule_minutes(schedule.end)
        if start is None or end is None or start == end:
            return False, None
        local = self.localtime(timestamp)
        now = local.tm_hour * 60 + local.tm_min
        today = date(local.tm_year, local.tm_mon, local.tm_mday)
        if start < end:
            return (start <= now < end, today.isoformat())
        if now >= start:
            return True, today.isoformat()
        if now < end:
            return True, (today - timedelta(days=1)).isoformat()
        return False, None

    async def _delay(self, seconds: float) -> None:
        result = self.sleep(seconds)
        if asyncio.iscoroutine(result):
            await result

    def start_periodic(self) -> None:
        if self._schedule_task is None or self._schedule_task.done():
            self._schedule_task = asyncio.create_task(self._run_periodic())

    async def _run_periodic(self) -> None:
        try:
            while True:
                try:
                    await self.tick()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._log("error", f"Schedule tick failed: {exc}")
                await self._delay(self.schedule_interval)
        except asyncio.CancelledError:
            pass

    async def tick(self) -> None:
        """Apply the current schedule window, retrying incomplete boundary work."""
        schedule_generation = self._schedule_generation
        data = self._settings()
        now = self.wall_clock()
        active, window_key = self._schedule_window(data.schedule, now)
        config = (
            data.schedule.enabled,
            data.schedule.start,
            data.schedule.end,
            data.schedule.mode,
        )
        if config != self._schedule_config:
            self._schedule_config = config
            self._schedule_window_key = None
            self._schedule_attempts.clear()
            self._schedule_exhausted_logged.clear()
            self._schedule_late_done = False
            self._schedule_mode_done = False

        if active and window_key is not None:
            if window_key != self._schedule_window_key:
                self._schedule_window_key = window_key
                self._schedule_attempts.clear()
                self._schedule_exhausted_logged.clear()
                self._schedule_late_done = (
                    data.schedule.last_late_window == window_key
                )
                self._schedule_mode_done = (
                    data.schedule.last_mode_window == window_key
                )
            if data.schedule.override_window != window_key:
                if data.schedule.override_window is not None:
                    data.schedule.override_window = None
                    self._save(data)
                data = self._settings()
            if data.schedule.override_window == window_key:
                self._mark_schedule_late_done(
                    window_key, schedule_generation, config
                )
            await self._apply_schedule_window(
                window_key, schedule_generation, config
            )
            return

        self._schedule_window_key = None
        self._schedule_attempts.clear()
        self._schedule_exhausted_logged.clear()
        self._schedule_late_done = False
        self._schedule_mode_done = False
        if data.schedule.override_window is not None:
            data.schedule.override_window = None
            self._save(data)
        data = self._settings()
        if data.late.active and data.late.auto:
            await self._exit_schedule_late_night(
                schedule_generation, config, window_key
            )

    def _schedule_operation_is_current(
        self,
        generation: int,
        config: tuple,
        window_key: str | None,
        active: bool,
    ) -> bool:
        if generation != self._schedule_generation:
            return False
        schedule = self._settings().schedule
        current_config = (
            schedule.enabled,
            schedule.start,
            schedule.end,
            schedule.mode,
        )
        if current_config != config:
            return False
        current_active, current_key = self._schedule_window(
            schedule, self.wall_clock()
        )
        return current_active == active and current_key == window_key

    def _begin_schedule_attempt(self, window_key: str, operation: str) -> bool:
        key = (window_key, operation)
        attempts = self._schedule_attempts.get(key, 0)
        if attempts >= _SCHEDULE_MAX_ATTEMPTS:
            if key not in self._schedule_exhausted_logged:
                self._schedule_exhausted_logged.add(key)
                self._log(
                    "warning",
                    f"Scheduled {operation} boundary remains pending after "
                    f"{_SCHEDULE_MAX_ATTEMPTS} attempts for {window_key}",
                )
            return False
        return True

    def _log_schedule_unconfirmed(
        self, window_key: str, operation: str, observed: Any, requested: Any
    ) -> None:
        key = (window_key, operation)
        attempts = self._schedule_attempts.get(key, 0) + 1
        self._schedule_attempts[key] = attempts
        if attempts >= _SCHEDULE_MAX_ATTEMPTS:
            message = (
                f"Scheduled {operation} boundary remains pending after "
                f"{attempts} attempts for {window_key}"
            )
        else:
            message = (
                f"Scheduled {operation} was not confirmed for {window_key}: "
                f"requested {requested}, observed {observed}; will retry"
            )
        self._log("warning", message)

    async def _apply_schedule_window(
        self, window_key: str, schedule_generation: int, config: tuple
    ) -> None:
        if not self._schedule_operation_is_current(
            schedule_generation, config, window_key, active=True
        ):
            return
        data = self._settings()
        if data.schedule.override_window == window_key:
            self._mark_schedule_late_done(
                window_key, schedule_generation, config
            )
        mode = data.schedule.mode
        if self._schedule_late_done and (
            self._schedule_mode_done or mode is None
        ):
            return

        generation = self._receiver_generation
        pending_op: str | None = None
        try:
            driver = await self._get_driver(generation)
            if not self._is_current(generation, driver):
                return
            status: Status = await self._run(
                "status", generation=generation, driver=driver
            )
            if not self._is_current(generation, driver) or not self._schedule_operation_is_current(
                schedule_generation, config, window_key, active=True
            ):
                return
            if status.power is False:
                return

            data = self._settings()
            if not self._schedule_late_done:
                if VOLUME not in driver.capabilities:
                    self._mark_schedule_late_done(
                        window_key, schedule_generation, config
                    )
                elif data.schedule.override_window == window_key:
                    self._mark_schedule_late_done(
                        window_key, schedule_generation, config
                    )
                elif data.late.active and not data.late.auto:
                    self._mark_schedule_late_done(
                        window_key, schedule_generation, config
                    )
                elif status.volume is not None:
                    if not data.late.active:
                        if not self._schedule_operation_is_current(
                            schedule_generation, config, window_key, active=True
                        ):
                            return
                        data = self._settings()
                        data.late.active = True
                        data.late.prev_volume = status.volume
                        data.late.auto = True
                        data.late.binding = self._receiver_binding(data)
                        self._save(data)
                    cap = self._effective_cap(self._settings(), driver.scale)
                    observed_volume = status.volume
                    if status.volume > cap:
                        if not self._schedule_operation_is_current(
                            schedule_generation, config, window_key, active=True
                        ):
                            return
                        if self._begin_schedule_attempt(window_key, "volume"):
                            pending_op = "volume"
                            observed_volume = await self._run(
                                "set_volume",
                                cap,
                                generation=generation,
                                driver=driver,
                            )
                            pending_op = None
                            if not self._is_current(generation, driver) or not self._schedule_operation_is_current(
                                schedule_generation, config, window_key, active=True
                            ):
                                return
                            if observed_volume is None or observed_volume > cap:
                                self._log_schedule_unconfirmed(
                                    window_key, "volume", observed_volume, cap
                                )
                    if observed_volume is not None and observed_volume <= cap:
                        self._mark_schedule_late_done(
                            window_key, schedule_generation, config
                        )

            if self._schedule_mode_done:
                return
            data = self._settings()
            mode = data.schedule.mode
            if mode is None or MODE not in driver.capabilities:
                self._mark_schedule_mode_done(
                    window_key, schedule_generation, config
                )
                return
            profile_has_mode = False
            if self._current_game is not None:
                profile = data.games.get(str(self._current_game["appid"]))
                profile_has_mode = bool(
                    profile
                    and profile.driver == driver.key
                    and profile.mode
                )
            if profile_has_mode:
                self._mark_schedule_mode_done(
                    window_key, schedule_generation, config
                )
                return
            if status.mode == mode:
                self._mark_schedule_mode_done(
                    window_key, schedule_generation, config
                )
                return
            if not self._schedule_operation_is_current(
                schedule_generation, config, window_key, active=True
            ):
                return
            if not self._begin_schedule_attempt(window_key, "mode"):
                return
            pending_op = "mode"
            observed_mode = await self._run(
                "set_mode", mode, generation=generation, driver=driver
            )
            pending_op = None
            if (
                not self._is_current(generation, driver)
                or not self._schedule_operation_is_current(
                    schedule_generation, config, window_key, active=True
                )
            ):
                return
            if observed_mode == mode:
                self._mark_schedule_mode_done(
                    window_key, schedule_generation, config
                )
            else:
                self._log_schedule_unconfirmed(
                    window_key, "mode", observed_mode, mode
                )
        except (Refused, Unsupported) as exc:
            # The receiver understood and said no: count it against the bounded
            # budget. Only transport failures retry on every tick.
            if pending_op is not None:
                self._log_schedule_unconfirmed(
                    window_key, pending_op, f"refused ({exc})", "change"
                )
            else:
                self._log("error", f"Applying time-of-day profile failed: {exc}")
        except Exception as exc:
            self._log("error", f"Applying time-of-day profile failed: {exc}")

    def _mark_schedule_mode_done(
        self, window_key: str, schedule_generation: int, config: tuple
    ) -> None:
        if not self._schedule_operation_is_current(
            schedule_generation, config, window_key, active=True
        ):
            return
        data = self._settings()
        if data.schedule.last_mode_window != window_key:
            data.schedule.last_mode_window = window_key
            self._save(data)
        self._schedule_mode_done = True

    def _mark_schedule_late_done(
        self, window_key: str, schedule_generation: int, config: tuple
    ) -> None:
        if not self._schedule_operation_is_current(
            schedule_generation, config, window_key, active=True
        ):
            return
        data = self._settings()
        if data.schedule.last_late_window != window_key:
            data.schedule.last_late_window = window_key
            self._save(data)
        self._schedule_late_done = True

    async def _exit_schedule_late_night(
        self,
        schedule_generation: int,
        config: tuple,
        window_key: str | None,
    ) -> None:
        generation = self._receiver_generation
        try:
            driver = await self._get_driver(generation)
            if not self._is_current(generation, driver):
                return
            status: Status = await self._run(
                "status", generation=generation, driver=driver
            )
            if (
                not self._is_current(generation, driver)
                or not self._schedule_operation_is_current(
                    schedule_generation, config, window_key, active=False
                )
                or status.power is False
                or status.volume is None
            ):
                return
            data = self._settings()
            if not data.late.active or not data.late.auto:
                return
            previous = data.late.prev_volume
            if previous is not None:
                volume = VolumePolicy.clamp(
                    int(previous), self._base_cap(data, driver.scale), driver.scale
                )
                if status.volume != volume:
                    if not self._schedule_operation_is_current(
                        schedule_generation, config, window_key, active=False
                    ):
                        return
                    await self._run(
                        "set_volume", volume, generation=generation, driver=driver
                    )
                    if not self._is_current(generation, driver) or not self._schedule_operation_is_current(
                        schedule_generation, config, window_key, active=False
                    ):
                        return
            data = self._settings()
            if (
                not data.late.active
                or not data.late.auto
                or not self._schedule_operation_is_current(
                    schedule_generation, config, window_key, active=False
                )
            ):
                return
            data.late.active = False
            data.late.prev_volume = None
            data.late.auto = False
            self._save(data)
            if previous is not None:
                await self._remember(
                    volume=volume, generation=generation, driver=driver
                )
        except Exception as exc:
            self._log("error", f"Restoring scheduled late-night volume failed: {exc}")

    def _start_resume_task(self) -> None:
        if self._suspend_task is not None and not self._suspend_task.done():
            self._suspend_task.cancel()
        if self._resume_task is None or self._resume_task.done():
            self._resume_task = asyncio.create_task(self._wake_receiver())

    def _start_suspend_task(self) -> None:
        if self._resume_task is not None and not self._resume_task.done():
            self._resume_task.cancel()
        if self._suspend_task is None or self._suspend_task.done():
            self._suspend_task = asyncio.create_task(self._suspend_receiver())

    async def _wake_receiver(self) -> None:
        data = self._settings()
        if not data.sleep_wake.wake_on_resume:
            return
        generation = self._receiver_generation
        deadline = self.clock() + self.resume_window
        while self.clock() <= deadline and self._is_current(generation):
            if not self._settings().sleep_wake.wake_on_resume:
                return
            remaining = max(0.01, deadline - self.clock())
            try:
                driver = await asyncio.wait_for(
                    self._get_driver(generation), timeout=min(4.0, remaining)
                )
                if not self._is_current(generation, driver):
                    return
                if POWER not in driver.capabilities:
                    return
                remaining = max(0.01, deadline - self.clock())
                timeout = min(4.0, remaining)
                status: Status = await asyncio.wait_for(
                    self._run("status", generation=generation, driver=driver),
                    timeout=timeout,
                )
                if not self._is_current(generation, driver):
                    return
                if status.power is True:
                    return
                if status.power is False:
                    await asyncio.wait_for(
                        self._run(
                            "set_power",
                            True,
                            generation=generation,
                            driver=driver,
                        ),
                        timeout=min(4.0, max(0.01, deadline - self.clock())),
                    )
                    return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._log("debug", f"Receiver wake retry: {exc}")
            remaining = deadline - self.clock()
            if remaining <= 0:
                break
            await self._delay(min(self.resume_retry_interval, remaining))

    async def _suspend_receiver(self) -> None:
        data = self._settings()
        if not data.sleep_wake.off_on_suspend:
            return
        generation = self._receiver_generation
        try:
            await asyncio.wait_for(
                self._suspend_receiver_command(generation),
                timeout=self.suspend_timeout,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._log("debug", f"Best-effort receiver power-off failed: {exc}")

    async def _suspend_receiver_command(self, generation: int) -> None:
        if not self._settings().sleep_wake.off_on_suspend:
            return
        driver = await self._get_driver(generation)
        if not self._is_current(generation, driver):
            return
        if POWER not in driver.capabilities:
            return
        await self._run(
            "set_power", False, generation=generation, driver=driver
        )

    def _settings(self) -> Settings:
        return self.store.load()

    def _save(self, data: Settings) -> None:
        self.store.save(data)

    @staticmethod
    def _host(data: Settings) -> str:
        return str(data.receiver.config.get("host", "")).strip()

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

    def _receiver_binding(self, data: Settings) -> dict:
        receiver = data.receiver
        driver = receiver.driver
        ident = receiver.id
        if ident:
            return {"driver": driver, "id": str(ident).upper()}
        config = receiver.config or {}
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

    def _legacy_receiver_binding(self, data: Settings) -> dict:
        receiver = data.receiver
        driver = receiver.driver
        ident = receiver.id
        if ident:
            return {"driver": driver, "id": str(ident).upper()}
        config = receiver.config or {}
        driver_class = self._class_for(driver)
        if driver_class is not None:
            try:
                config = self._binding_config(driver_class, config)
            except Exception:
                config = receiver.config or {}
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

    def _bind_receiver_state(self, data: Settings) -> bool:
        binding = self._receiver_binding(data)
        legacy_binding = self._legacy_receiver_binding(data)
        changed = data.receiver.binding != binding
        data.receiver.binding = binding
        for value, binding_owner, binding_attribute in (
            (data.cap.cap, data.cap, "binding"),
            (data.late.cap, data.late, "cap_binding"),
        ):
            previous = getattr(binding_owner, binding_attribute)
            if previous is None:
                if value is not None:
                    setattr(binding_owner, binding_attribute, binding)
                    changed = True
            elif previous != binding:
                setattr(binding_owner, binding_attribute, binding)
                if previous != legacy_binding:
                    if binding_owner is data.cap:
                        data.cap.cap = None
                    else:
                        data.late.cap = None
                changed = True

        late = data.late
        previous = late.binding
        if previous is None:
            if late.active or late.prev_volume is not None:
                late.binding = binding
                changed = True
        elif previous != binding:
            late.binding = binding
            if previous != legacy_binding:
                data.late.active = False
                data.late.prev_volume = None
                data.late.auto = False
            changed = True
        if not late.active and late.auto:
            late.auto = False
            changed = True

        return changed

    def _scale_for(self, data: Settings):
        if self._active_driver is not None:
            return self._active_driver.scale
        key = data.receiver.driver
        driver_class = self._class_for(key) or self._class_for("eiscp")
        return driver_class.scale

    def _class_for(self, key: str | None):
        if key is None:
            return None
        if self.registry is REGISTRY:
            return get_driver_class(key)
        return next((driver for driver in self.registry if driver.key == key), None)

    def _base_cap(self, data: Settings, scale=None) -> int:
        scale = scale or self._scale_for(data)
        return VolumePolicy.base_cap(data.cap.cap, scale)

    def _late_cap(self, data: Settings, scale=None) -> int:
        scale = scale or self._scale_for(data)
        return VolumePolicy.late_cap(data.late.cap, scale)

    def _effective_cap(self, data: Settings, scale=None) -> int:
        scale = scale or self._scale_for(data)
        return VolumePolicy.effective_cap(
            data.cap.cap, data.late.cap, data.late.active, scale
        )

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
        receiver = data.receiver
        key = receiver.driver
        config = dict(receiver.config or {})
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
            receiver.driver = found.driver
            receiver.config = dict(found.config)
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
        config_changed = receiver.config != normalized_config
        receiver.config = normalized_config
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
            self._cancel_reapply()
            self._last_status = None
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
        receiver = data.receiver
        _instance, config = self._normalized_driver_config(
            type(driver), driver.config
        )
        if receiver.driver == driver.key and receiver.config == config:
            return
        receiver.driver = driver.key
        receiver.config = config
        self._bind_receiver_state(data)
        self._save(data)

    def _bind_unbound_profiles(self, data: Settings, driver_key: str) -> None:
        changed = False
        for profile in data.games.values():
            if profile.driver is None:
                profile.driver = driver_key
                changed = True
        if changed:
            self._save(data)

    async def _rediscover(
        self,
        data: Settings,
        generation: int | None = None,
        starting_driver: Driver | None = None,
    ) -> Driver | None:
        if generation is None:
            generation = self._receiver_generation
        if starting_driver is None:
            starting_driver = self._active_driver
        ident = data.receiver.id
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
        await self._stop_watch()
        data = self._settings()
        data.receiver.driver = found.driver
        data.receiver.config = dict(found.config)
        data.receiver.id = found.id
        self._bind_receiver_state(data)
        self._save(data)
        driver_class = self._class_for(found.driver)
        if driver_class is None:
            return None
        self._cancel_reapply()
        self._active_driver = driver_class(dict(found.config))
        self._initialized_driver = None
        self._last_status = None
        await self._start_watch()
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
        push_sequence = self._push_sequence if method == "status" else None
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
            status = Status(result.volume, result.muted, result.power, result.mode)
            cached = self._last_status
            if (
                push_sequence is not None
                and cached is not None
                and cached[0] == generation
                and cached[1] is driver
            ):
                for field in ("volume", "muted", "power", "mode"):
                    if self._push_field_sequences.get(field, 0) > push_sequence:
                        setattr(status, field, getattr(cached[2], field))
            result = status
            self._initialized_driver = driver
            self._last_status = (
                generation,
                driver,
                Status(result.volume, result.muted, result.power, result.mode),
            )
            if (
                push_sequence is not None
                and self._push_sequence != push_sequence
            ):
                self._revision += 1
        elif self._last_status is not None:
            cached_generation, cached_driver, status = self._last_status
            if cached_generation == generation and cached_driver is driver:
                updated = Status(status.volume, status.muted, status.power, status.mode)
                attribute = {
                    "set_volume": "volume",
                    "set_mute": "muted",
                    "set_power": "power",
                    "set_mode": "mode",
                }.get(method)
                if attribute is not None:
                    setattr(updated, attribute, result)
                    self._last_status = (generation, driver, updated)
        self._persist_driver_config(driver, generation)
        return result

    async def _get_connection(self) -> dict:
        receiver = self._settings().receiver
        driver_key = receiver.driver
        config = dict(receiver.config or {})
        driver_class = self._class_for(driver_key)
        if driver_class is None:
            fields = {"host": str(config["host"])} if config.get("host") else {}
            return {
                "configured": bool(config.get("host")) if driver_key is None else False,
                "driver": driver_key,
                "label": None,
                "summary": str(config.get("host") or ""),
                "fields": fields,
            }

        fields = {
            field.key: str(config[field.key])
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

    async def _configure(self, driver_key: str, config: dict) -> dict:
        starting_generation = self._receiver_generation
        starting_driver = self._active_driver
        data = self._settings()
        config = dict(config)
        receiver = data.receiver
        driver_class = None
        if driver_key != "auto":
            driver_class = self._class_for(driver_key)
            if driver_class is None:
                return {"ok": False, "error": f"Unknown receiver driver: {driver_key}"}
        if driver_key != "auto" and receiver.driver == driver_key:
            saved_config = dict(receiver.config or {})
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
        self._cancel_reapply()
        await self._stop_watch()
        self._bind_receiver_state(data)
        if driver_key == "auto":
            same_host = self._host(data) == str(config.get("host") or "").strip()
            receiver.config = config
            receiver.id = receiver.id if same_host else None
            receiver.driver = None
            self._active_driver = None
            self._initialized_driver = None
            self._last_status = None
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
                    current.receiver.driver == active.key
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
                        if current.receiver.id != resolved_id:
                            current.receiver.id = resolved_id
                            self._bind_receiver_state(current)
                            self._save(current)
                    await self._start_watch()
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
            data.receiver.driver = found.driver
            data.receiver.config = found_config
            if found.id:
                data.receiver.id = str(found.id).upper()
            self._bind_receiver_state(data)
            self._save(data)
            self._active_driver = active_driver
            self._initialized_driver = None
            self._last_status = None
            await self._start_watch()
            return {"ok": True}

        candidate, config = self._normalized_driver_config(driver_class, config)
        old_driver = receiver.driver
        try:
            old_config = self._identity_config(
                driver_class, receiver.config or {}
            )
            same_connection = (
                old_driver == driver_key
                and old_config == self._identity_config(driver_class, config)
            )
        except Exception:
            same_connection = False
        receiver.driver = driver_key
        receiver.config = config
        if not same_connection:
            receiver.id = None
        self._bind_receiver_state(data)
        self._active_driver = candidate
        self._initialized_driver = None
        self._last_status = None
        self._receiver_generation += 1
        self._save(data)
        await self._start_watch()
        return {"ok": True}

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

    async def _select_receiver(self, ip: str, ident: str) -> None:
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
        self._cancel_reapply()
        await self._stop_watch()
        data.receiver = type(data.receiver)(
            driver=found.driver,
            config=dict(found.config),
            id=ident.upper(),
            secret_keys=data.secret_keys,
        )
        self._bind_receiver_state(data)
        self._save(data)
        driver_class = self._class_for(found.driver)
        self._active_driver = driver_class(dict(found.config)) if driver_class else None
        self._initialized_driver = None
        self._last_status = None
        self._receiver_generation += 1
        await self._start_watch()

    def _mode_label(self, driver: Driver, code: str | None) -> str | None:
        if not code:
            return None
        return type(driver).mode_label(code)

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

    async def _read_state(
        self, refresh: bool = True, allow_unknown: bool = False
    ) -> dict:
        generation = self._receiver_generation
        cached = self._last_status
        if (
            not refresh
            and cached is not None
            and cached[0] == generation
            and cached[1] is self._active_driver
        ):
            status = cached[2]
        elif allow_unknown and not refresh:
            return {"ok": False, "error": "Receiver state hasn't been read yet"}
        else:
            status = None
        try:
            driver = await self._get_driver(generation)
            if not self._is_current(generation, driver):
                raise self._stale_receiver_error()
            if status is not None and cached is not None and cached[1] is not driver:
                status = None
            if status is None:
                status = await self._run(
                    "status", generation=generation, driver=driver
                )
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
                profile = data.games.get(str(self._current_game["appid"]))
                game = {
                    **self._current_game,
                    "volume": profile is not None and profile.volume is not None,
                    "mode": profile is not None and profile.mode is not None,
                }
            mode_state: ModeState = await driver.mode_state(status)
            if not self._is_current(generation, driver):
                raise self._stale_receiver_error()
            scale = driver.scale
            current_code = mode_state.current
            current_label = None
            if current_code:
                current_label = next(
                    (
                        mode.name
                        for mode in mode_state.selectable
                        if mode.code == current_code
                    ),
                    type(driver).mode_label(current_code),
                )
            return {
                "ok": True,
                "volume": status.volume,
                "muted": status.muted,
                "max": self._effective_cap(data, scale),
                "cap": self._base_cap(data, scale),
                "late": data.late.active,
                "late_cap": self._late_cap(data, scale),
                "mode": current_label,
                "mode_code": current_code,
                "modes": [
                    {"code": mode.code, "name": mode.name}
                    for mode in mode_state.selectable
                ],
                "modes_complete": mode_state.complete,
                "game": game,
                "driver": driver.key,
                "driver_label": driver.label,
                "capabilities": [
                    capability
                    for capability in _CAPABILITY_ORDER
                    if capability in driver.capabilities
                ],
                "power": status.power,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def _set_volume(self, value: int) -> dict:
        generation = self._receiver_generation
        try:
            data = self._settings()
            driver = await self._get_driver(generation)
            if not self._is_current(generation, driver):
                raise self._stale_receiver_error()
            driver = await self._ensure_initialized(driver, generation)
            scale = driver.scale
            requested_volume = int(value)
            volume = VolumePolicy.clamp(
                requested_volume, self._effective_cap(data, scale), scale
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

    async def _set_mute(self, muted: bool) -> dict:
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

    async def _set_power(self, on: bool) -> dict:
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

    async def _set_mode(self, code: str) -> dict:
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
        profile = data.games.get(str(self._current_game["appid"]))
        if not profile:
            return
        if profile.driver != (self._active_driver.key if self._active_driver else None):
            return
        if data.late.active:
            volume = None
        changed = False
        if volume is not None and profile.volume is not None and profile.volume != volume:
            profile.volume = volume
            changed = True
        if mode is not None and profile.mode is not None and profile.mode != mode:
            profile.mode = mode
            changed = True
        if changed:
            if self._is_current(generation, driver):
                self._save(data)

    async def _set_current_game(self, appid: int, name: str) -> None:
        current = {"appid": int(appid), "name": name} if appid else None
        if current != self._current_game:
            self._cancel_reapply()
        self._current_game = current

    async def _game_started(self, appid: int, name: str) -> dict:
        self._cancel_reapply()
        generation = self._receiver_generation
        self._current_game = {"appid": int(appid), "name": name}
        data = self._settings()
        profile = data.games.get(str(int(appid)))
        if not profile:
            return {"applied": False}
        try:
            driver = await self._get_driver(generation)
            if not self._is_current(generation, driver):
                raise self._stale_receiver_error()
            if not self._current_game or self._current_game["appid"] != int(appid):
                return {"applied": False}
            data = self._settings()
            profile = data.games.get(str(int(appid)))
            if not profile:
                return {"applied": False}
            if profile.driver != driver.key:
                return {"applied": False}
            self._suppress_game_saves(generation, appid)
            driver = await self._ensure_initialized(
                driver, generation, expected_driver=driver.key
            )
            data = self._settings()
            profile = data.games.get(str(int(appid)))
            if not profile or profile.driver != driver.key:
                return {"applied": False}
            self._suppress_game_saves(generation, appid)
            applied = False
            volume = profile.volume
            if volume is not None:
                volume = VolumePolicy.clamp(
                    int(volume), self._effective_cap(data, driver.scale), driver.scale
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
            mode = profile.mode
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
                    self._reapply_task = asyncio.create_task(
                        self._reapply_mode(int(appid), mode, generation, driver)
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

    async def _game_stopped(self, appid: int) -> None:
        if self._current_game and self._current_game["appid"] == int(appid):
            self._cancel_reapply()
            self._current_game = None

    async def _set_game_remember(self, what: str, enabled: bool) -> dict:
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
                if data.late.active:
                    value = data.late.prev_volume if data.late.prev_volume is not None else value
                if value is None:
                    return {"ok": False, "error": "Receiver doesn't report volume"}
            else:
                value = status.mode
                if not value:
                    return {"ok": False, "error": "Receiver doesn't report a listening mode"}
            profile = data.games.get(key)
            if profile is None:
                profile = GameProfile(
                    appid=int(key),
                    name=current_game["name"],
                    driver=driver.key,
                )
                data.games[key] = profile
            elif profile.driver not in (None, driver.key):
                profile.volume = None
                profile.mode = None
            profile.name = current_game["name"]
            profile.driver = driver.key
            if what == "volume":
                profile.volume = value
            else:
                profile.mode = value
        else:
            profile = data.games.get(key)
            if profile:
                if what == "volume":
                    profile.volume = None
                else:
                    profile.mode = None
                if profile.volume is None and profile.mode is None:
                    data.games.pop(key, None)
        if not self._is_current(generation) or self._current_game != current_game:
            return {"ok": False, "error": str(self._stale_receiver_error())}
        self._save(data)
        return {"ok": True}

    async def _forget_game(self, appid: int) -> None:
        data = self._settings()
        data.games.pop(str(int(appid)), None)
        self._save(data)

    async def _set_schedule(self, intent: SetSchedule) -> dict:
        if (
            self._schedule_minutes(intent.start) is None
            or self._schedule_minutes(intent.end) is None
        ):
            return {"ok": False, "error": "Times must use HH:MM (24-hour) format"}
        if intent.start == intent.end:
            return {"ok": False, "error": "Start and end times must be different"}
        if intent.mode is not None and not isinstance(intent.mode, str):
            return {"ok": False, "error": "Invalid listening mode"}
        mode = intent.mode.strip() if intent.mode else None
        data = self._settings()
        changed = (
            data.schedule.enabled != bool(intent.enabled)
            or data.schedule.start != intent.start
            or data.schedule.end != intent.end
            or data.schedule.mode != mode
        )
        data.schedule.enabled = bool(intent.enabled)
        data.schedule.start = intent.start
        data.schedule.end = intent.end
        data.schedule.mode = mode
        if changed:
            self._schedule_generation += 1
            self._schedule_attempts.clear()
            self._schedule_exhausted_logged.clear()
            data.schedule.override_window = None
            data.schedule.last_late_window = None
            data.schedule.last_mode_window = None
            self._schedule_config = None
            self._schedule_window_key = None
            self._schedule_late_done = False
            self._schedule_mode_done = False
        self._save(data)
        return {"ok": True}

    async def _set_sleep_wake(self, intent: SetSleepWake) -> dict:
        data = self._settings()
        data.sleep_wake.wake_on_resume = bool(intent.wake_on_resume)
        data.sleep_wake.off_on_suspend = bool(intent.off_on_suspend)
        self._save(data)
        if not data.sleep_wake.wake_on_resume and self._resume_task is not None:
            self._resume_task.cancel()
        if not data.sleep_wake.off_on_suspend and self._suspend_task is not None:
            self._suspend_task.cancel()
        return {"ok": True}

    async def _set_late_night(self, on: bool) -> dict:
        self._schedule_generation += 1
        generation = self._receiver_generation
        try:
            data = self._settings()
            schedule_active, window_key = self._schedule_window(
                data.schedule, self.wall_clock()
            )
            changed = False
            if schedule_active and window_key is not None:
                data.schedule.override_window = window_key
                changed = True
            if data.late.auto:
                data.late.auto = False
                changed = True
            if changed:
                self._save(data)
            driver = await self._get_driver(generation)
            if not self._is_current(generation, driver):
                raise self._stale_receiver_error()
            active = data.late.active
            if on and not active:
                status: Status = await self._run(
                    "status", generation=generation, driver=driver
                )
                if not self._is_current(generation, driver):
                    raise self._stale_receiver_error()
                if status.volume is None:
                    return {"ok": False, "error": "Turn the receiver on first"}
                data = self._settings()
                data.late.active = True
                data.late.prev_volume = status.volume
                data.late.auto = False
                data.late.binding = self._receiver_binding(data)
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
                previous = data.late.prev_volume
                if not self._is_current(generation, driver):
                    raise self._stale_receiver_error()
                data.late.active = False
                data.late.prev_volume = None
                data.late.auto = False
                self._save(data)
                if previous is not None:
                    volume = VolumePolicy.clamp(
                        int(previous),
                        self._effective_cap(data, driver.scale),
                        driver.scale,
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
            elif not on and not active and data.late.auto:
                data.late.auto = False
                self._save(data)
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def snapshot(self) -> dict[str, Any]:
        return await self._snapshot(refresh=True)

    async def _snapshot(
        self, refresh: bool, allow_unknown: bool = False
    ) -> dict[str, Any]:
        connection = await self._get_connection()
        state = await self._read_state(
            refresh=refresh, allow_unknown=allow_unknown
        )
        data = self._settings()
        driver = self._active_driver
        scale = driver.scale if state.get("ok") and driver is not None else None
        error = state.get("error")
        volume = None
        caps = None
        modes = {"current": None, "selectable": [], "complete": False}
        receiver = {"power": None, "muted": None, "volume": None}
        capabilities: list[str] = []
        game = None

        if state.get("ok") and scale is not None:
            raw_volume = state.get("volume")
            if raw_volume is not None:
                volume = {
                    "raw": raw_volume,
                    "display": VolumePolicy.to_display(raw_volume, scale),
                }
            receiver = {
                "power": state.get("power"),
                "muted": state.get("muted"),
                "volume": volume,
            }
            capabilities = list(state.get("capabilities", []))
            code = state.get("mode_code")
            modes = {
                "current": (
                    {"code": code, "label": state.get("mode") or f"Mode {code}"}
                    if code
                    else None
                ),
                "selectable": [
                    {"code": item["code"], "label": item["name"]}
                    for item in state.get("modes", [])
                ],
                "complete": bool(state.get("modes_complete")),
            }
            caps = {
                "cap_display": VolumePolicy.to_display(state["cap"], scale),
                "late_cap_display": VolumePolicy.to_display(
                    state["late_cap"], scale
                ),
                "effective_max_display": VolumePolicy.to_display(
                    state["max"], scale
                ),
                "late_night": bool(state["late"]),
            }
            state_game = state.get("game")
            if state_game is not None:
                game = {
                    "appid": state_game["appid"],
                    "name": state_game["name"],
                    "remembers_volume": bool(state_game["volume"]),
                    "remembers_mode": bool(state_game["mode"]),
                }

        scale_snapshot = VolumePolicy.display_scale(scale) if scale is not None else None

        profiles = []
        for profile in data.games.values():
            profile_driver = self._class_for(profile.driver)
            profile_scale = self._profile_scale(profile.driver)
            mode_label = (
                profile_driver.mode_label(profile.mode)
                if profile.mode and profile_driver is not None
                else (f"Mode {profile.mode}" if profile.mode else None)
            )
            profiles.append(
                {
                    "appid": profile.appid,
                    "name": profile.name,
                    "driver": profile.driver,
                    "volume_display": (
                        VolumePolicy.to_display(profile.volume, profile_scale)
                        if profile.volume is not None and profile_scale is not None
                        else None
                    ),
                    "mode_label": mode_label,
                    "same_receiver": (
                        profile.driver is not None
                        and profile.driver == data.receiver.driver
                    ),
                }
            )

        active_now, _window_key = self._schedule_window(
            data.schedule, self.wall_clock()
        )
        power_supported = bool(
            driver is not None and POWER in driver.capabilities
        )
        result = {
            "connection": connection,
            "receiver": receiver,
            "scale": scale_snapshot,
            "caps": caps,
            "modes": modes,
            "game": game,
            "profiles": profiles,
            "capabilities": capabilities,
            "schedule": {
                "enabled": data.schedule.enabled,
                "start": data.schedule.start,
                "end": data.schedule.end,
                "mode": data.schedule.mode,
                "active_now": active_now,
                "auto": data.late.auto,
            },
            "sleep_wake": {
                "wake_on_resume": data.sleep_wake.wake_on_resume,
                "off_on_suspend": data.sleep_wake.off_on_suspend,
                "supports_power": power_supported,
            },
            "revision": self._revision,
            "push": self._push_active,
        }
        if error:
            result["error"] = error
        return result

    def _profile_scale(self, driver_key: str | None) -> VolumeScale | None:
        if driver_key is None:
            return None
        if (
            self._active_driver is not None
            and self._active_driver.key == driver_key
        ):
            return self._active_driver.scale
        driver_class = self._class_for(driver_key)
        declared_scale = getattr(driver_class, "scale", None) if driver_class else None
        return declared_scale if isinstance(declared_scale, VolumeScale) else None

    def drivers(self) -> list[dict[str, Any]]:
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
            for driver in self.registry
        ]

    async def setup_options(
        self, driver_key: str, config: dict[str, Any]
    ) -> dict[str, list[dict[str, str]]]:
        driver_class = self._class_for(driver_key)
        if driver_class is None:
            raise DriverError(f"Unknown receiver driver: {driver_key}")
        setup_config = dict(config)
        receiver = self._settings().receiver
        if receiver.driver == driver_key:
            saved_config = dict(receiver.config or {})
            setup_config = {**saved_config, **setup_config}
            for field in driver_class.config_fields:
                if field.secret and not str(
                    setup_config.get(field.key, "")
                ).strip():
                    if saved_config.get(field.key):
                        setup_config[field.key] = saved_config[field.key]
        options = await driver_class.setup_options(setup_config)
        return {
            key: [{"value": option.value, "label": option.label} for option in values]
            for key, values in options.items()
        }

    async def dispatch(self, intent: ControlIntent) -> dict[str, Any]:
        result: dict[str, Any] | None
        result_generation: int | None = None
        result_driver: Driver | None = None
        result_receiver_id: str | None = None
        try:
            if isinstance(intent, SetVolume):
                generation = self._receiver_generation
                driver = await self._get_driver(generation)
                driver = await self._ensure_initialized(driver, generation)
                if not self._is_current(generation, driver):
                    raise self._stale_receiver_error()
                selected_receiver_id = self._settings().receiver.id
                raw = VolumePolicy.to_raw(intent.display, driver.scale)
                result = await self._set_volume(raw)
                result_generation = generation
                result_driver = self._active_driver
                result_receiver_id = (
                    str(selected_receiver_id).upper()
                    if selected_receiver_id is not None
                    else None
                )
            elif isinstance(intent, SetMute):
                result = await self._set_mute(intent.muted)
                result_generation = self._receiver_generation
                result_driver = self._active_driver
            elif isinstance(intent, SetPower):
                result = await self._set_power(intent.on)
                result_generation = self._receiver_generation
                result_driver = self._active_driver
            elif isinstance(intent, SetMode):
                result = await self._set_mode(intent.code)
                result_generation = self._receiver_generation
                result_driver = self._active_driver
            elif isinstance(intent, SetLateNight):
                result = await self._set_late_night(intent.on)
            elif isinstance(intent, SetSchedule):
                result = await self._set_schedule(intent)
            elif isinstance(intent, SetSleepWake):
                result = await self._set_sleep_wake(intent)
            elif isinstance(intent, SystemResumed):
                data = self._settings()
                if data.sleep_wake.wake_on_resume:
                    self._start_resume_task()
                result = {"ok": True}
            elif isinstance(intent, SystemSuspending):
                data = self._settings()
                if data.sleep_wake.off_on_suspend:
                    self._start_suspend_task()
                result = {"ok": True}
            elif isinstance(intent, PanelOpened):
                self._panel_open = True
                await self._start_watch()
                result = {"ok": True}
            elif isinstance(intent, PanelClosed):
                self._panel_open = False
                await self._stop_watch()
                result = {"ok": True}
            elif isinstance(intent, SetCaps):
                generation = self._receiver_generation
                driver = await self._get_driver(generation)
                driver = await self._ensure_initialized(driver, generation)
                if not self._is_current(generation, driver):
                    raise self._stale_receiver_error()
                cap = max(
                    1,
                    VolumePolicy.to_raw(intent.cap_display, driver.scale),
                )
                late_cap = max(
                    1,
                    VolumePolicy.to_raw(intent.late_cap_display, driver.scale),
                )
                data = self._settings()
                data.cap.cap = cap
                data.cap.binding = self._receiver_binding(data)
                data.late.cap = late_cap
                data.late.cap_binding = self._receiver_binding(data)
                self._save(data)
                result = {"ok": True}
            elif isinstance(intent, ConfigureReceiver):
                result = await self._configure(intent.driver_key, intent.config)
            elif isinstance(intent, SelectReceiver):
                found = next(
                    (
                        item
                        for item in self._last_found
                        if item.id.upper() == intent.id.upper()
                    ),
                    None,
                )
                if found is None:
                    result = {
                        "ok": False,
                        "error": "Receiver is no longer in the discovery results",
                    }
                else:
                    await self._select_receiver(found.host, found.id)
                    result = {"ok": True}
            elif isinstance(intent, GameStarted):
                result = await self._game_started(intent.appid, intent.name)
            elif isinstance(intent, GameStopped):
                await self._game_stopped(intent.appid)
                result = {"ok": True}
            elif isinstance(intent, SetCurrentGame):
                await self._set_current_game(intent.appid, intent.name)
                result = {"ok": True}
            elif isinstance(intent, RememberForGame):
                result = await self._set_game_remember(intent.what, intent.on)
            elif isinstance(intent, ForgetGame):
                await self._forget_game(intent.appid)
                result = {"ok": True}
            else:
                raise ValueError(f"Unsupported control intent: {type(intent).__name__}")
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}

        try:
            local_only = isinstance(
                intent,
                (
                    SetCurrentGame,
                    GameStopped,
                    ForgetGame,
                    GameStarted,
                    SetSchedule,
                    SetSleepWake,
                    SystemResumed,
                    SystemSuspending,
                    PanelOpened,
                    PanelClosed,
                ),
            )
            current_snapshot = await self._snapshot(
                refresh=False,
                allow_unknown=local_only,
            )
        except Exception as exc:
            current_snapshot = {
                "connection": await self._get_connection(),
                "receiver": {"power": None, "muted": None, "volume": None},
                "scale": None,
                "caps": None,
                "modes": {"current": None, "selectable": [], "complete": False},
                "game": None,
                "profiles": [],
                "capabilities": [],
                "schedule": {
                    "enabled": False,
                    "start": "22:00",
                    "end": "07:00",
                    "mode": None,
                    "active_now": False,
                    "auto": False,
                },
                "sleep_wake": {
                    "wake_on_resume": False,
                    "off_on_suspend": False,
                    "supports_power": False,
                },
                "revision": self._revision,
                "push": self._push_active,
                "error": str(exc),
            }
        outcome: dict[str, Any] = {
            "ok": bool(
                result is None
                or (result.get("ok", True) and not result.get("error"))
            ),
            "snapshot": current_snapshot,
        }
        error = result.get("error") if result else None
        if error:
            outcome["error"] = str(error)
        has_command_state = result is not None and any(
            field in result for field in ("volume", "muted", "power", "mode")
        )
        if (
            outcome["ok"]
            and has_command_state
            and result_generation is not None
            and (
                not self._is_current(result_generation, result_driver)
                or (
                    result_receiver_id is not None
                    and str(self._settings().receiver.id or "").upper()
                    != result_receiver_id
                )
            )
        ):
            outcome["ok"] = False
            outcome["error"] = str(self._stale_receiver_error())
        if result is not None and "confirmed" in result:
            if outcome["ok"]:
                outcome["confirmed"] = bool(result["confirmed"])

        if outcome["ok"] and result is not None and has_command_state:
            receiver = current_snapshot.get("receiver", {})
            if "volume" in result:
                active_scale = self._active_driver.scale if self._active_driver else None
                if active_scale is not None and result["volume"] is not None:
                    receiver["volume"] = {
                        "raw": result["volume"],
                        "display": VolumePolicy.to_display(
                            result["volume"], active_scale
                        ),
                    }
            if "muted" in result:
                receiver["muted"] = result["muted"]
            if "power" in result:
                receiver["power"] = result["power"]
            if "mode" in result and result.get("code"):
                current_snapshot["modes"]["current"] = {
                    "code": result["code"],
                    "label": result["mode"],
                }
        return outcome

    async def close(self) -> None:
        self._panel_open = False
        await self._stop_watch()
        tasks = (
            self._schedule_task,
            self._resume_task,
            self._suspend_task,
            self._reapply_task,
        )
        self._schedule_task = None
        self._resume_task = None
        self._suspend_task = None
        self._reapply_task = None
        for task in tasks:
            if task is not None and not task.done():
                task.cancel()
        pending = [task for task in tasks if task is not None]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        push_tasks = tuple(self._push_tasks)
        for task in push_tasks:
            if not task.done():
                task.cancel()
        if push_tasks:
            await asyncio.gather(*push_tasks, return_exceptions=True)
        if self._active_driver is not None:
            await self._active_driver.close()
