from __future__ import annotations

import asyncio
import unittest

from tests import PY_MODULES  # noqa: F401
from tests.fakes import InMemoryDuplex, InMemoryStreamReader
from avr_control.drivers.base import DriverError
from avr_control.drivers.receiver_session import ReceiverSession


async def _read_line(reader: InMemoryStreamReader) -> bytes:
    line = await reader.readline()
    if not line:
        raise asyncio.IncompleteReadError(b"", 1)
    return line.rstrip(b"\r\n")


def _reply(frame: bytes) -> str | None:
    return "reply" if frame.startswith(b"reply:") else None


class ReceiverSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_transactions_are_serialized(self):
        first_received = asyncio.Event()
        release_first = asyncio.Event()
        commands: list[bytes] = []
        active = 0
        max_active = 0

        async def handle(reader, writer):
            nonlocal active, max_active
            while line := await reader.readline():
                command = line.strip()
                commands.append(command)
                active += 1
                max_active = max(max_active, active)
                if command == b"A":
                    first_received.set()
                    await release_first.wait()
                writer.write(b"reply:" + command + b"\n")
                await writer.drain()
                active -= 1

        duplex = InMemoryDuplex(handle)
        session = ReceiverSession(duplex.connect, _read_line)
        first = asyncio.create_task(
            session.transact([b"A\n"], _reply, frozenset({"reply"}), timeout=1)
        )
        await asyncio.wait_for(first_received.wait(), 1)
        second = asyncio.create_task(
            session.transact([b"B\n"], _reply, frozenset({"reply"}), timeout=1)
        )
        await asyncio.sleep(0.02)
        self.assertEqual(commands, [b"A"])
        release_first.set()
        first_result, second_result = await asyncio.gather(first, second)

        self.assertEqual(first_result["reply"], b"reply:A")
        self.assertEqual(second_result["reply"], b"reply:B")
        self.assertEqual(max_active, 1)
        await session.close()
        await duplex.close()

    async def test_pushed_frames_interleaved_with_replies_reach_subscribers(self):
        async def handle(reader, writer):
            while line := await reader.readline():
                if line.strip() == b"QUERY":
                    writer.write(b"push:volume\nreply:state\n")
                    await writer.drain()

        duplex = InMemoryDuplex(handle)
        session = ReceiverSession(duplex.connect, _read_line)
        pushed: list[bytes] = []
        received_push = asyncio.Event()

        def on_frame(frame: bytes) -> None:
            pushed.append(frame)
            received_push.set()

        unsubscribe = session.subscribe(on_frame)
        result = await session.transact(
            [b"QUERY\n"], _reply, frozenset({"reply"}), timeout=1
        )

        self.assertEqual(result["reply"], b"reply:state")
        await asyncio.wait_for(received_push.wait(), 1)
        self.assertEqual(pushed, [b"push:volume"])
        unsubscribe()
        await session.close()
        await duplex.close()

    async def test_timeout_and_cancellation_discard_streams(self):
        release_slow = asyncio.Event()

        async def handle(reader, writer):
            while line := await reader.readline():
                command = line.strip()
                if command == b"SLOW":
                    await release_slow.wait()
                elif command == b"OK":
                    writer.write(b"reply:ok\n")
                    await writer.drain()

        duplex = InMemoryDuplex(handle)
        session = ReceiverSession(duplex.connect, _read_line)
        with self.assertRaises(DriverError):
            await session.transact(
                [b"TIMEOUT\n"], _reply, frozenset({"reply"}), timeout=0.02
            )
        self.assertEqual(len(duplex.connections), 1)

        cancelled = asyncio.create_task(
            session.transact([b"SLOW\n"], _reply, frozenset({"reply"}), timeout=1)
        )
        await asyncio.sleep(0.02)
        cancelled.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await cancelled
        release_slow.set()

        result = await session.transact(
            [b"OK\n"], _reply, frozenset({"reply"}), timeout=1
        )
        self.assertEqual(result["reply"], b"reply:ok")
        self.assertEqual(len(duplex.connections), 3)
        await session.close()
        await duplex.close()

    async def test_eof_and_failed_reconnect_raise_driver_error(self):
        accepted = 0

        async def handle(reader, writer):
            nonlocal accepted
            accepted += 1
            line = await reader.readline()
            if accepted == 1:
                writer.close()
                return
            if line.strip() == b"GOOD":
                writer.write(b"reply:good\n")
                await writer.drain()

        duplex = InMemoryDuplex(handle)
        failed_connects = 0

        async def connect():
            nonlocal failed_connects
            failed_connects += 1
            if failed_connects == 2:
                raise OSError("receiver unavailable")
            return await duplex.connect()

        session = ReceiverSession(connect, _read_line)
        with self.assertRaises(DriverError):
            await session.transact(
                [b"DROP\n"], _reply, frozenset({"reply"}), timeout=1
            )
        with self.assertRaises(DriverError):
            await session.transact(
                [b"GOOD\n"], _reply, frozenset({"reply"}), timeout=1
            )
        result = await session.transact(
            [b"GOOD\n"], _reply, frozenset({"reply"}), timeout=1
        )
        self.assertEqual(result["reply"], b"reply:good")
        await session.close()
        await session.close()
        result = await session.transact(
            [b"GOOD\n"], _reply, frozenset({"reply"}), timeout=1
        )
        self.assertEqual(result["reply"], b"reply:good")
        self.assertEqual(len(duplex.connections), 3)
        await duplex.close()

    async def test_min_interval_spaces_writes_in_one_transaction(self):
        async def handle(reader, writer):
            received = []
            while len(received) < 3:
                line = await reader.readline()
                if not line:
                    return
                received.append(line)
            writer.write(b"reply:done\n")
            await writer.drain()

        duplex = InMemoryDuplex(handle)
        session = ReceiverSession(duplex.connect, _read_line)
        await session.transact(
            [b"ONE\n", b"TWO\n", b"THREE\n"],
            _reply,
            frozenset({"reply"}),
            timeout=1,
            min_interval=0.03,
        )
        write_times = [item[1] for item in duplex.client_writes]
        self.assertEqual(len(write_times), 3)
        self.assertGreaterEqual(write_times[1] - write_times[0], 0.025)
        self.assertGreaterEqual(write_times[2] - write_times[1], 0.025)
        await session.close()
        await duplex.close()

    async def test_optional_reply_grace_collects_late_frame(self):
        async def handle(reader, writer):
            while line := await reader.readline():
                if line.strip() == b"QUERY":
                    writer.write(b"reply:required\n")
                    await writer.drain()
                    await asyncio.sleep(0.02)
                    writer.write(b"optional:extra\n")
                    await writer.drain()

        duplex = InMemoryDuplex(handle)
        session = ReceiverSession(duplex.connect, _read_line)
        result = await session.transact(
            [b"QUERY\n"],
            lambda frame: (
                "reply"
                if frame.startswith(b"reply:")
                else "optional"
                if frame.startswith(b"optional:")
                else None
            ),
            frozenset({"reply"}),
            timeout=1,
            optional=frozenset({"optional"}),
            optional_grace=0.1,
        )
        self.assertEqual(result["reply"], b"reply:required")
        self.assertEqual(result["optional"], b"optional:extra")
        await session.close()
        await duplex.close()

    async def test_idle_close_reuses_then_expires_connection(self):
        async def handle(reader, writer):
            while line := await reader.readline():
                writer.write(b"reply:pong\n")
                await writer.drain()

        duplex = InMemoryDuplex(handle)
        session = ReceiverSession(duplex.connect, _read_line, idle_close=0.08)
        for _ in range(2):
            result = await session.transact(
                [b"PING\n"], _reply, frozenset({"reply"}), timeout=1
            )
            self.assertEqual(result["reply"], b"reply:pong")
            await asyncio.sleep(0.02)
        self.assertEqual(len(duplex.connections), 1)

        await asyncio.sleep(0.1)
        await session.transact(
            [b"PING\n"], _reply, frozenset({"reply"}), timeout=1
        )
        self.assertEqual(len(duplex.connections), 2)
        await session.close()
        await duplex.close()

    async def test_background_reading_delivers_frames_while_subscribed(self):
        async def handle(reader, writer):
            writer.write(b"push:startup\n")
            await writer.drain()
            while await reader.readline():
                pass

        duplex = InMemoryDuplex(handle)
        session = ReceiverSession(duplex.connect, _read_line)
        frames: list[bytes] = []
        received = asyncio.Event()

        def on_frame(frame: bytes) -> None:
            frames.append(frame)
            received.set()

        unsubscribe = session.subscribe(on_frame)
        await asyncio.wait_for(received.wait(), 1)
        self.assertEqual(frames, [b"push:startup"])
        unsubscribe()
        await session.close()
        await duplex.close()

    async def test_unsubscribe_closes_only_after_last_subscriber_leaves(self):
        async def handle(reader, _writer):
            while await reader.readline():
                pass

        duplex = InMemoryDuplex(handle)
        session = ReceiverSession(duplex.connect, _read_line)
        first_handler = lambda _frame: None
        second_handler = lambda _frame: None
        session.subscribe(first_handler)
        session.subscribe(second_handler)
        for _ in range(10):
            if duplex.connections:
                break
            await asyncio.sleep(0)
        writer = duplex.connections[0][1]

        await session.unsubscribe(first_handler)
        self.assertFalse(writer.is_closing())
        await session.unsubscribe(second_handler)
        self.assertTrue(writer.is_closing())

        await session.close()
        await duplex.close()


if __name__ == "__main__":
    unittest.main()
