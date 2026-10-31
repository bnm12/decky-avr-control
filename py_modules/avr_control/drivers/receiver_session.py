"""Owned TCP stream transactions shared by drivers for the same endpoint."""

from __future__ import annotations

import asyncio
import contextlib
import weakref
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Generic, TypeVar

from .base import DriverError

Frame = TypeVar("Frame")
ReadFrame = Callable[[Any], Awaitable[Frame]]
Connect = Callable[[], Awaitable[tuple[Any, Any]]]
Classify = Callable[[Frame], str | None]
Handler = Callable[[Frame], None]
ConnectionHandler = Callable[[int], None]


class ReceiverSessionError(DriverError):
    """A stream failure, with a reason drivers may translate into protocol terms."""

    def __init__(self, message: str, *, kind: str):
        super().__init__(message)
        self.kind = kind


class _Transaction(Generic[Frame]):
    def __init__(
        self,
        classify: Classify[Frame],
    ):
        self.classify = classify
        self.frames: dict[str, Frame] = {}
        self.error: Exception | None = None
        self.changed = asyncio.Event()


class ReceiverSession(Generic[Frame]):
    """Serialize exchanges on one owned TCP stream.

    Drivers retain framing and response meaning in ``read_frame`` and
    ``classify``. The session owns stream setup, transaction ordering, deadlines,
    cancellation cleanup, optional replies, and delivery of unrelated frames.
    A zero ``idle_close`` closes after each transaction; registered subscribers
    keep the reader alive so pushed frames can be delivered.
    """

    _CONNECT_TIMEOUT = 2.0

    def __init__(
        self,
        connect: Connect,
        read_frame: ReadFrame[Frame],
        *,
        idle_close: float = 0.0,
    ):
        if idle_close < 0:
            raise ValueError("idle_close must not be negative")
        self._connect = connect
        self._read_frame = read_frame
        self._idle_close = idle_close
        self._reader: Any | None = None
        self._writer: Any | None = None
        self._reader_task: asyncio.Task | None = None
        self._active: _Transaction[Frame] | None = None
        self._subscribers: set[Handler[Frame]] = set()
        self._connection_handlers: set[ConnectionHandler] = set()
        self._connection_generation = 0
        self._lock = asyncio.Lock()
        self._group_owner: asyncio.Task | None = None
        self._group_depth = 0
        self._idle_task: asyncio.Task | None = None
        self._last_write: float | None = None

    async def transact(
        self,
        writes: Sequence[bytes],
        classify: Classify[Frame],
        required: frozenset[str],
        *,
        timeout: float,
        min_interval: float = 0,
        optional: frozenset[str] = frozenset(),
        optional_grace: float = 0,
    ) -> Mapping[str, Frame]:
        """Write one exchange and return its classified replies.

        Required keys bound the reply wait. Once they arrive, optional replies
        are collected for at most ``optional_grace``. A timeout or stream error
        discards the connection; cancellation also discards it but is propagated.
        """
        if timeout < 0 or min_interval < 0 or optional_grace < 0:
            raise ValueError("transaction timing values must not be negative")

        owner = asyncio.current_task()
        acquired = self._group_owner is not owner
        if acquired:
            await self._lock.acquire()
        transaction = _Transaction(classify)
        try:
            self._cancel_idle_close()
            await self._ensure_connected()
            self._active = transaction
            for data in writes:
                if not isinstance(data, bytes):
                    raise TypeError("transaction writes must be bytes")
                if min_interval and self._last_write is not None:
                    delay = min_interval - (
                        asyncio.get_running_loop().time() - self._last_write
                    )
                    if delay > 0:
                        await asyncio.sleep(delay)
                await self._write(data)
                self._last_write = asyncio.get_running_loop().time()

            deadline = asyncio.get_running_loop().time() + timeout
            while not required.issubset(transaction.frames):
                if transaction.error is not None:
                    raise transaction.error
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise ReceiverSessionError(
                        "TCP receiver did not confirm the transaction in time",
                        kind="confirmation",
                    )
                await self._wait_for_change(transaction, remaining)

            if optional and optional_grace:
                optional_deadline = (
                    asyncio.get_running_loop().time() + optional_grace
                )
                while not optional.issubset(transaction.frames):
                    if transaction.error is not None:
                        break
                    remaining = optional_deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        break
                    await self._wait_for_change(transaction, remaining)
            return dict(transaction.frames)
        except asyncio.CancelledError:
            await self._discard_stream()
            raise
        except asyncio.TimeoutError as exc:
            await self._discard_stream()
            raise ReceiverSessionError(
                "TCP receiver transaction timed out", kind="timeout"
            ) from exc
        except DriverError:
            await self._discard_stream()
            raise
        except Exception as exc:
            await self._discard_stream()
            raise ReceiverSessionError(
                f"TCP receiver transaction failed: {exc}", kind="stream"
            ) from exc
        finally:
            if self._active is transaction:
                self._active = None
            if acquired:
                self._lock.release()
            if not self._has_owner():
                await self._finish_transaction()

    def subscribe(self, handler: Handler[Frame]) -> Callable[[], None]:
        """Subscribe to frames not claimed by the active transaction."""
        self._subscribers.add(handler)
        try:
            asyncio.get_running_loop().create_task(self._start_subscriber_reader())
        except RuntimeError:
            self._subscribers.discard(handler)
            raise

        def unsubscribe() -> None:
            try:
                asyncio.get_running_loop().create_task(self.unsubscribe(handler))
            except RuntimeError:
                self._subscribers.discard(handler)

        return unsubscribe

    async def unsubscribe(self, handler: Handler[Frame]) -> None:
        """Remove a subscriber and finish closing its now-unowned stream."""
        self._subscribers.discard(handler)
        await self._finish_transaction()

    @property
    def connection_generation(self) -> int:
        """A monotonically increasing number for each established stream."""
        return self._connection_generation

    def on_connect(
        self, handler: ConnectionHandler
    ) -> Callable[[], None]:
        """Observe future stream connections without owning a frame subscription."""
        self._connection_handlers.add(handler)

        def unsubscribe() -> None:
            self._connection_handlers.discard(handler)

        return unsubscribe

    async def close(self) -> None:
        """Close the current stream; repeated calls are safe."""
        self._cancel_idle_close()
        owner = asyncio.current_task()
        if self._group_owner is owner:
            await self._discard_stream()
            return
        async with self._lock:
            await self._discard_stream()

    @contextlib.asynccontextmanager
    async def _transaction_group(self):
        """Keep a stream exclusively for a multi-step protocol operation.

        Most drivers use one ``transact`` per operation. HEOS discovers a player
        ID before it can form later requests, so its ordered request/reply steps
        share one operation-level group while still using the same transaction
        channel for each exchange.
        """
        owner = asyncio.current_task()
        if owner is self._group_owner:
            self._group_depth += 1
        else:
            await self._lock.acquire()
            self._group_owner = owner
            self._group_depth = 1
        try:
            self._cancel_idle_close()
            yield
        finally:
            self._group_depth -= 1
            if not self._group_depth:
                self._group_owner = None
                self._lock.release()
                await self._finish_transaction()

    async def _ensure_connected(self) -> None:
        if self._reader is not None and self._writer is not None:
            return
        try:
            reader, writer = await asyncio.wait_for(
                self._connect(), self._CONNECT_TIMEOUT
            )
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError as exc:
            raise ReceiverSessionError(
                "Can't connect to TCP receiver in time", kind="connect"
            ) from exc
        except Exception as exc:
            raise ReceiverSessionError(
                f"Can't connect to TCP receiver: {exc}", kind="connect"
            ) from exc
        self._reader, self._writer = reader, writer
        self._reader_task = asyncio.create_task(self._read_loop(reader, writer))
        self._connection_generation += 1
        for handler in tuple(self._connection_handlers):
            try:
                handler(self._connection_generation)
            except Exception:
                continue

    async def _write(self, data: bytes) -> None:
        writer = self._writer
        if writer is None:
            raise ReceiverSessionError("TCP receiver stream is closed", kind="eof")
        try:
            writer.write(data)
            await asyncio.wait_for(writer.drain(), self._CONNECT_TIMEOUT)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError as exc:
            raise ReceiverSessionError("TCP receiver write timed out", kind="timeout") from exc
        except Exception as exc:
            raise ReceiverSessionError(
                f"TCP receiver write failed: {exc}", kind="stream"
            ) from exc

    async def _read_loop(self, reader: Any, writer: Any) -> None:
        try:
            while reader is self._reader and writer is self._writer:
                frame = await self._read_frame(reader)
                transaction = self._active
                if transaction is None:
                    self._notify(frame)
                    continue
                try:
                    key = transaction.classify(frame)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    transaction.error = (
                        exc
                        if isinstance(exc, DriverError)
                        else DriverError(f"TCP response classifier failed: {exc}")
                    )
                    transaction.changed.set()
                    await self._discard_stream()
                    return
                if key is None:
                    self._notify(frame)
                elif key not in transaction.frames:
                    transaction.frames[key] = frame
                    transaction.changed.set()
                else:
                    self._notify(frame)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            transaction = self._active
            if transaction is not None:
                if isinstance(exc, DriverError):
                    transaction.error = exc
                elif isinstance(exc, asyncio.TimeoutError):
                    transaction.error = ReceiverSessionError(
                        "TCP receiver read timed out", kind="timeout"
                    )
                elif isinstance(exc, (EOFError, ConnectionError, OSError)):
                    transaction.error = ReceiverSessionError(
                        "TCP receiver closed the connection", kind="eof"
                    )
                else:
                    transaction.error = DriverError(
                        f"TCP receiver read failed: {exc}"
                    )
                transaction.changed.set()
            await self._discard_stream()

    async def _wait_for_change(
        self, transaction: _Transaction[Frame], timeout: float
    ) -> None:
        transaction.changed.clear()
        if transaction.error is not None:
            return
        try:
            await asyncio.wait_for(transaction.changed.wait(), timeout)
        except asyncio.TimeoutError:
            return

    def _notify(self, frame: Frame) -> None:
        for handler in tuple(self._subscribers):
            try:
                handler(frame)
            except Exception:
                continue

    async def _start_subscriber_reader(self) -> None:
        async with self._lock:
            if not self._subscribers:
                return
            self._cancel_idle_close()
            try:
                await self._ensure_connected()
            except DriverError:
                return

    async def _finish_transaction(self) -> None:
        async with self._lock:
            if self._active is not None or self._group_owner is not None:
                return
            if self._subscribers:
                if self._reader is None:
                    try:
                        await self._ensure_connected()
                    except DriverError:
                        return
                return
            if self._idle_close:
                self._schedule_idle_close()
            else:
                await self._discard_stream()

    def _has_owner(self) -> bool:
        return self._group_owner is not None

    def _schedule_idle_close(self) -> None:
        self._cancel_idle_close()

        async def expire() -> None:
            try:
                await asyncio.sleep(self._idle_close)
                async with self._lock:
                    if not self._subscribers and self._group_owner is None:
                        await self._discard_stream()
            except asyncio.CancelledError:
                return

        self._idle_task = asyncio.create_task(expire())

    def _cancel_idle_close(self) -> None:
        task = self._idle_task
        self._idle_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def _discard_stream(self) -> None:
        reader, writer = self._reader, self._writer
        task = self._reader_task
        self._reader = self._writer = self._reader_task = None
        self._last_write = None
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if writer is not None:
            try:
                await writer.wait_closed()
            except Exception:
                pass


_SESSIONS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def shared_session(
    host: str, port: int, read_frame: ReadFrame[Frame]
) -> ReceiverSession[Frame]:
    """Return the loop-local session shared by drivers using the same framing."""
    loop = asyncio.get_running_loop()
    sessions = _SESSIONS.get(loop)
    if sessions is None:
        sessions = weakref.WeakValueDictionary()
        _SESSIONS[loop] = sessions
    # Framing is part of the key: a custom port may intentionally host a
    # different protocol, and its frames must never be consumed by this adapter.
    key = (host, int(port), read_frame)
    session = sessions.get(key)
    if session is None:
        async def connect():
            return await asyncio.open_connection(host, int(port))

        session = ReceiverSession(connect, read_frame)
        sessions[key] = session
    return session
