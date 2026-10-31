from __future__ import annotations

import asyncio
import base64
import json
import re
import struct
import sys
import unittest
from pathlib import Path

from tests import PY_MODULES  # noqa: F401
from avr_control.drivers import get_driver_class
from avr_control.drivers.base import Found
from tests.fakes import FakeEiscp
from tests.replay import HttpHostMatcher, ReplayServer
from tests.test_denon import FakeDenon, FakeHttpDenon
from tests.test_heos import FakeHeos
from tests.test_sony import FakeSonyScalarWebAPI
from tests.test_upnp import FakeUpnpRenderer
from tests.test_yamaha import FakeYamahaYXC
from tests.test_yamaha_ync import FakeYamahaYNC
from avr_control.drivers.upnp import UpnpDriver
from tools.avr_probe import (
    RecordingTcpProxy,
    capture_found,
    read_snapshot,
    redact_bytes,
    redact_text,
    _redact_connections,
)

TRANSCRIPTS = Path(__file__).parent / "transcripts"
ALLOWED_TRANSCRIPT_SOURCES = {
    "simulator",
    "owner-hardware",
    "contributor-hardware",
    "reconstructed-from-measurements",
}


def _matcher_for(transcript: dict):
    rewrite_loopback_port = transcript.get("driver", {}).get("key") == "upnp"
    for connection in transcript.get("connections", []):
        for exchange in connection.get("exchanges", []):
            if exchange.get("dir") != "tx":
                continue
            data = base64.b64decode(exchange.get("data_b64", ""))
            if data.startswith((b"GET ", b"POST ", b"PUT ", b"DELETE ", b"PATCH ")):
                return HttpHostMatcher(rewrite_loopback_port=rewrite_loopback_port)
    return None


def _normalize_upnp_loopback_port(transcript: dict, proxy_port: int) -> None:
    """Replace the recording proxy's advertised URLBase port with a replay marker."""
    old_urls = (
        f"http://127.0.0.1:{proxy_port}/".encode("ascii"),
        f"http://IP{'X' * (len('127.0.0.1') - 2)}:{proxy_port}/".encode("ascii"),
    )
    marker_url = b"http://127.0.0.1:0/"
    for connection in transcript.get("connections", []):
        exchanges = connection.get("exchanges", [])
        index = 0
        while index < len(exchanges):
            if exchanges[index].get("dir") != "rx":
                index += 1
                continue
            end = index + 1
            while end < len(exchanges) and exchanges[end].get("dir") == "rx":
                end += 1
            raw = b"".join(
                base64.b64decode(item.get("data_b64", ""))
                for item in exchanges[index:end]
            )
            headers, separator, body = raw.partition(b"\r\n\r\n")
            rewritten = body
            for old_url in old_urls:
                rewritten = rewritten.replace(old_url, marker_url)
            if separator and rewritten != body:
                headers = re.sub(
                    rb"(?im)^content-length:\s*\d+\s*$",
                    f"Content-Length: {len(rewritten)}".encode("ascii"),
                    headers,
                )
                raw = headers + separator + rewritten
                exchanges[index]["data_b64"] = base64.b64encode(raw).decode("ascii")
                exchanges[index]["text"] = raw.decode("utf-8", errors="replace")
                del exchanges[index + 1 : end]
                exchanges[index]["t"] = round(float(exchanges[index]["t"]), 6)
                index += 1
            else:
                index = end


async def generate_simulator_transcripts() -> None:
    TRANSCRIPTS.mkdir(parents=True, exist_ok=True)
    heos = FakeHeos()
    heos.players = heos.players[:1]
    receivers = [
        ("eiscp", FakeEiscp(), "VSX-935 simulator", {}),
        ("denon", FakeDenon(), "AVR-X3800H simulator", {}),
        ("yamaha", FakeYamahaYXC(), "RX-V6A simulator", {}),
        ("yamaha_ync", FakeYamahaYNC(), "RX-V671 simulator", {}),
        ("sony", FakeSonyScalarWebAPI(), "STR-DN1080 simulator", {}),
        ("upnp", FakeUpnpRenderer(sonos=True), "Sonos Play:1 simulator", {}),
        ("heos", heos, "HEOS AVR simulator", {}),
    ]

    async def capture_and_write(key: str, fake, model: str, config: dict) -> None:
        port = await fake.start()
        found = Found(
            driver=key,
            model=model,
            id=f"simulator-{key}-receiver",
            host="127.0.0.1",
            config={"host": "127.0.0.1", "port": port, **config},
        )
        proxy_ports: list[int] = []

        def advertise_recording_proxy(proxy_port: int) -> None:
            proxy_ports.append(proxy_port)
            fake.server.server_port = proxy_port

        try:
            transcript = await capture_found(
                found,
                redact=True,
                include_discovery=False,
                source="simulator",
                proxy_started=(
                    advertise_recording_proxy if key == "upnp" else None
                ),
            )
        finally:
            await fake.close()
        filename = TRANSCRIPTS / f"{key}-simulator.json"
        if key == "upnp":
            _normalize_upnp_loopback_port(transcript, proxy_ports[0])
        filename.write_text(
            json.dumps(transcript, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote {filename}")

    for key, fake, model, config in receivers:
        await capture_and_write(key, fake, model, config)

    http_denon = FakeHttpDenon()
    http_denon_found = Found(
        driver="denon",
        model="AVR-X3800H HTTP simulator",
        id="simulator-denon-http-receiver",
        host="127.0.0.1",
        config={
            "host": "127.0.0.1",
            "port": http_denon.port,
            "http_port": http_denon.port,
            "transport": "http",
        },
    )
    try:
        http_transcript = await capture_found(
            http_denon_found,
            redact=True,
            include_discovery=False,
            source="simulator",
        )
    finally:
        http_denon.close()
    http_filename = TRANSCRIPTS / "denon-http-simulator.json"
    http_filename.write_text(
        json.dumps(http_transcript, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {http_filename}")


class TranscriptReplayTests(unittest.IsolatedAsyncioTestCase):
    async def _assert_transcript_replays(self, transcript: dict, name: str):
        self.assertEqual(transcript["schema_version"], 1)
        self.assertIn(transcript.get("source"), ALLOWED_TRANSCRIPT_SOURCES)
        driver_class = get_driver_class(transcript["driver"]["key"])
        self.assertIsNotNone(driver_class)
        matcher = _matcher_for(transcript)
        replay = ReplayServer(transcript["connections"], matcher=matcher)
        port = await replay.start()
        config = {"host": "127.0.0.1", "port": port}
        if transcript["driver"]["key"] == "denon" and isinstance(
            matcher, HttpHostMatcher
        ):
            config.update({"transport": "http", "http_port": port})
        driver = driver_class(config)
        driver_error = None
        try:
            actual = await asyncio.wait_for(read_snapshot(driver), timeout=15)
        except Exception as exc:
            driver_error = exc
            actual = None
        finally:
            await driver.close()
            await replay.close()
        try:
            replay.assert_complete()
        except AssertionError as exc:
            if driver_error is not None:
                raise AssertionError(
                    f"{exc}\nDriver also failed: {driver_error}"
                ) from driver_error
            raise
        if driver_error is not None:
            raise driver_error
        for field in ("status", "capabilities", "scale", "modes", "getters"):
            self.assertEqual(
                actual[field],
                transcript[field],
                f"{name}: driver {field} differs from fixture",
            )

    async def test_every_transcript_replays_against_its_driver(self):
        files = sorted(TRANSCRIPTS.glob("*.json"))
        self.assertTrue(files, "No transcript fixtures found")
        for path in files:
            with self.subTest(transcript=path.name):
                transcript = json.loads(path.read_text(encoding="utf-8"))
                await self._assert_transcript_replays(transcript, path.name)

    async def test_hardware_labelled_fixture_is_replayed(self):
        transcript = json.loads(
            (TRANSCRIPTS / "denon-simulator.json").read_text(encoding="utf-8")
        )
        # Exercise hardware provenance in memory; keep the saved fixture unchanged.
        transcript["source"] = "contributor-hardware"
        await self._assert_transcript_replays(
            transcript, "synthetic-contributor-hardware.json"
        )

    def test_redaction_masks_ids_without_changing_eiscp_frame_length(self):
        host = "192.168.10.22"
        receiver_id = "0009B0617D30"
        payload = (
            f"!1NJA2-http://{host}/album_art.cgi\r"
            f"!1ECNVSX-935/60128/XX/{receiver_id}\r"
        ).encode("ascii")
        packet = b"ISCP" + struct.pack(">IIB3x", 16, len(payload), 1) + payload
        cleaned = redact_bytes(packet, host, [receiver_id])
        header_size, data_size = struct.unpack(">II", cleaned[4:12])
        self.assertEqual(header_size, 16)
        self.assertEqual(data_size, len(cleaned) - header_size)
        self.assertEqual(len(cleaned), len(packet))
        self.assertNotIn(host.encode(), cleaned)
        self.assertNotIn(receiver_id.encode(), cleaned)
        visible = redact_text(packet.decode("latin-1"), host, [receiver_id])
        self.assertIn("RECEIVER_IP", visible)
        self.assertNotIn(host, visible)

    def test_redaction_hides_identifiers_split_across_exchange_chunks(self):
        host = "192.168.10.22"
        receiver_id = "0009B0617D30"
        transcript = {
            "connections": [
                {
                    "exchanges": [
                        {"dir": "rx", "t": 0.1, "_raw": b"host=192.168."},
                        {"dir": "rx", "t": 0.2, "_raw": b"10.22; id=0009B0"},
                        {"dir": "rx", "t": 0.3, "_raw": b"617D30"},
                    ]
                }
            ]
        }

        _redact_connections(transcript["connections"], host, [receiver_id])
        output = json.loads(json.dumps(transcript))
        exchanges = output["connections"][0]["exchanges"]
        encoded_bytes = b"".join(
            base64.b64decode(exchange["data_b64"]) for exchange in exchanges
        )
        text = "".join(exchange["text"] for exchange in exchanges)
        self.assertNotIn(host.encode("ascii"), encoded_bytes)
        self.assertNotIn(receiver_id.encode("ascii"), encoded_bytes)
        self.assertNotIn(host, text)
        self.assertNotIn(receiver_id, text)

    async def test_capture_defaults_to_contributor_hardware_provenance(self):
        receiver = FakeDenon()
        port = await receiver.start()
        found = Found(
            driver="denon",
            model="AVR-X3800H simulator",
            id="simulator-denon-receiver",
            host="127.0.0.1",
            config={"host": "127.0.0.1", "port": port},
        )
        try:
            transcript = await capture_found(found)
        finally:
            await receiver.close()

        self.assertEqual(transcript["source"], "contributor-hardware")

    async def test_upnp_probe_result_can_be_captured_through_recording_proxy(self):
        receiver = FakeUpnpRenderer(sonos=True)
        port = await receiver.start()
        found = await UpnpDriver.probe({"host": "127.0.0.1", "port": port})

        try:
            transcript = await capture_found(found)
        finally:
            await receiver.close()

        requests = [
            base64.b64decode(exchange["data_b64"])
            for connection in transcript["connections"]
            for exchange in connection["exchanges"]
            if exchange["dir"] == "tx"
        ]
        self.assertTrue(
            any(request.startswith(b"GET /xml/device_description.xml") for request in requests)
        )
        self.assertTrue(
            any(
                request.startswith(
                    b"POST /MediaRenderer/RenderingControl/Control"
                )
                for request in requests
            )
        )

    async def test_recording_proxy_captures_both_directions(self):
        async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            try:
                data = await reader.read(64)
                writer.write(data.upper())
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        receiver = await asyncio.start_server(echo, "127.0.0.1", 0)
        receiver_port = receiver.sockets[0].getsockname()[1]
        proxy = RecordingTcpProxy("127.0.0.1", receiver_port)
        proxy_port = await proxy.start()
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
            writer.write(b"probe")
            await writer.drain()
            self.assertEqual(await reader.readexactly(5), b"PROBE")
            writer.close()
            await writer.wait_closed()
            await asyncio.sleep(0.01)
        finally:
            await proxy.close()
            receiver.close()
            await receiver.wait_closed()

        exchanges = proxy.connections[0]["exchanges"]
        self.assertIn(("tx", b"probe"), [(item["dir"], item["_raw"]) for item in exchanges])
        self.assertIn(("rx", b"PROBE"), [(item["dir"], item["_raw"]) for item in exchanges])
        self.assertTrue(all(item["t"] >= 0 for item in exchanges))

    async def test_http_matcher_replays_requests_with_a_different_host(self):
        matcher = HttpHostMatcher()
        expected = b"GET /api HTTP/1.1\r\nHost: 127.0.0.1:8123\r\n\r\n"
        actual = b"GET /api HTTP/1.1\r\nHost: receiver.local:8123\r\n\r\n"
        self.assertTrue(matcher(expected, actual))

        expected = (
            b"POST /api HTTP/1.1\r\n"
            b"Host: 127.0.0.1:8123\r\n"
            b"Content-Length: 4\r\n\r\nbody"
        )
        response = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}"
        replay = ReplayServer(
            [
                {
                    "exchanges": [
                        {
                            "dir": "tx",
                            "data_b64": base64.b64encode(expected).decode("ascii"),
                        },
                        {
                            "dir": "rx",
                            "data_b64": base64.b64encode(response).decode("ascii"),
                        },
                    ]
                }
            ],
            matcher=matcher,
        )
        port = await replay.start()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        request = (
            b"POST /api HTTP/1.1\r\n"
            + f"Host: receiver.local:{port}\r\n".encode("ascii")
            + b"Content-Length: 4\r\n\r\nbody"
        )
        writer.write(request)
        await writer.drain()
        self.assertEqual(await reader.readexactly(len(response)), response)
        writer.close()
        await writer.wait_closed()
        await replay.close()
        replay.assert_complete()


if __name__ == "__main__":
    if sys.argv[1:] == ["--generate"]:
        asyncio.run(generate_simulator_transcripts())
    else:
        unittest.main()
