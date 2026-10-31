"""Replay recorded TCP exchanges against drivers without receiver hardware."""

from __future__ import annotations

import asyncio
import base64
import re
from collections.abc import Callable


def _diff(expected: bytes, actual: bytes) -> str:
    limit = min(len(expected), len(actual))
    mismatch = next(
        (index for index in range(limit) if expected[index] != actual[index]), limit
    )
    start = max(0, mismatch - 12)
    end = mismatch + 24
    return (
        f"first difference at byte {mismatch}; expected length={len(expected)}, "
        f"actual length={len(actual)}\n"
        f"  expected: {expected[start:end]!r}\n"
        f"  actual:   {actual[start:end]!r}"
    )


class HttpHostMatcher:
    """Matcher for one HTTP request; ignore Host differences from the proxy."""

    _CONTENT_LENGTH = re.compile(rb"(?im)^content-length:\s*(\d+)\s*$")

    async def read(self, reader: asyncio.StreamReader, _expected: bytes) -> bytes:
        headers = await reader.readuntil(b"\r\n\r\n")
        match = self._CONTENT_LENGTH.search(headers)
        body = await reader.readexactly(int(match.group(1))) if match else b""
        return headers + body

    def __call__(self, expected: bytes, actual: bytes) -> bool:
        def normalize(request: bytes) -> bytes:
            return re.sub(
                rb"(?im)^host:\s*[^\r\n]*",
                b"Host: <receiver>",
                request,
            )

        return normalize(expected) == normalize(actual)


class ReplayServer:
    """A TCP server that serves the next recorded connection transcript.

    By default, client bytes must exactly match each recorded ``tx`` chunk.
    Pass a callable matcher to normalize a protocol (for example
    ``HttpHostMatcher`` to ignore HTTP Host header differences).
    """

    def __init__(
        self,
        connections: list[dict],
        matcher: Callable[[bytes, bytes], bool] | None = None,
    ):
        self.connections = connections
        self.matcher = matcher or (lambda expected, actual: expected == actual)
        self.server: asyncio.AbstractServer | None = None
        self.port: int | None = None
        self._next_connection = 0
        self.errors: list[str] = []
        self._tasks: set[asyncio.Task] = set()

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = int(self.server.sockets[0].getsockname()[1])
        return self.port

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        index = self._next_connection
        self._next_connection += 1
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        try:
            if index >= len(self.connections):
                raise AssertionError(f"Unexpected TCP connection #{index + 1}")
            exchanges = self.connections[index].get("exchanges", [])
            exchange_index = 0
            while exchange_index < len(exchanges):
                exchange = exchanges[exchange_index]
                expected = exchange.get("data_b64")
                if expected is None:
                    expected_bytes = bytes(exchange.get("_raw", b""))
                else:
                    expected_bytes = base64.b64decode(expected)
                direction = exchange.get("dir")
                if direction == "rx":
                    writer.write(expected_bytes)
                    await writer.drain()
                    exchange_index += 1
                    continue
                if direction != "tx":
                    raise AssertionError(
                        f"Connection #{index + 1}, exchange #{exchange_index + 1}: "
                        f"unknown direction {direction!r}"
                    )

                tx_index = exchange_index
                reader_method = getattr(self.matcher, "read", None)
                if reader_method is None:
                    actual = await reader.readexactly(len(expected_bytes))
                    expected_group = expected_bytes
                    exchange_index += 1
                else:
                    tx_chunks = [expected_bytes]
                    next_index = exchange_index + 1
                    while (
                        next_index < len(exchanges)
                        and exchanges[next_index].get("dir") == "tx"
                    ):
                        next_exchange = exchanges[next_index]
                        encoded = next_exchange.get("data_b64")
                        tx_chunks.append(
                            base64.b64decode(encoded)
                            if encoded is not None
                            else bytes(next_exchange.get("_raw", b""))
                        )
                        next_index += 1
                    expected_group = b"".join(tx_chunks)
                    actual = await reader_method(reader, expected_group)
                    exchange_index = next_index
                if not self.matcher(expected_group, actual):
                    raise AssertionError(
                        f"Connection #{index + 1}, tx exchange #{tx_index + 1} "
                        f"did not match:\n{_diff(expected_group, actual)}"
                    )
            extra = await reader.read(1)
            if extra:
                raise AssertionError(
                    f"Connection #{index + 1} sent unexpected bytes after its "
                    f"transcript:\n{_diff(b'', extra)}"
                )
            writer.close()
            await writer.wait_closed()
        except asyncio.IncompleteReadError as exc:
            self.errors.append(
                f"Connection #{index + 1} ended early: expected "
                f"{exc.expected} more byte(s), received {len(exc.partial)}"
            )
        except Exception as exc:
            self.errors.append(str(exc))
            writer.close()
            await asyncio.gather(writer.wait_closed(), return_exceptions=True)
        finally:
            if task is not None:
                self._tasks.discard(task)

    def assert_complete(self) -> None:
        if self.errors:
            raise AssertionError("\n".join(self.errors))
        if self._next_connection != len(self.connections):
            raise AssertionError(
                f"Expected {len(self.connections)} replay connection(s), "
                f"received {self._next_connection}"
            )
