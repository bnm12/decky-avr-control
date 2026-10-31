#!/usr/bin/env python3
"""Discover, probe, and record read-only AVR protocol transcripts.

Examples:
    python tools/avr_probe.py discover
    python tools/avr_probe.py probe 192.168.1.42
    python tools/avr_probe.py capture 192.168.1.42

The script uses only the Python standard library and the repository's driver
modules. Home Assistant is deliberately excluded because its token is secret.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import datetime as _datetime
import hashlib
import json
import platform
import re
import socket
import struct
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
PY_MODULES = str(REPO_ROOT / "py_modules")
if PY_MODULES not in sys.path:
    sys.path.insert(0, PY_MODULES)

from avr_control import ssdp
from avr_control.drivers import REGISTRY, discover_all, get_driver_class, probe_all
from avr_control.drivers.base import Found

TOOL_VERSION = "1.0"
EISCP_PORT = 60128
DEFAULT_CAPTURE_OUT = "avr_probe_capture.json"
_MAC_RE = re.compile(
    rb"(?<![A-Fa-f0-9])(?:[A-Fa-f0-9]{2}[:-]){5}[A-Fa-f0-9]{2}(?![A-Fa-f0-9])"
    rb"|(?<![A-Fa-f0-9])[A-Fa-f0-9]{12}(?![A-Fa-f0-9])"
)
_TEXT_MAC_RE = re.compile(
    r"(?<![A-Fa-f0-9])(?:[A-Fa-f0-9]{2}[:-]){5}[A-Fa-f0-9]{2}(?![A-Fa-f0-9])"
    r"|(?<![A-Fa-f0-9])[A-Fa-f0-9]{12}(?![A-Fa-f0-9])"
)
_XML_ID_RE = re.compile(
    r"(?is)(<(?:UDN|serialNumber|serial|device_id|deviceId)[^>]*>)(.*?)(</(?:UDN|serialNumber|serial|device_id|deviceId)\s*>)"
)
_BYTE_XML_ID_RE = re.compile(
    rb"(?is)(<(?:UDN|serialNumber|serial|device_id|deviceId)[^>]*>)([^<]{6,})(</(?:UDN|serialNumber|serial|device_id|deviceId)\s*>)"
)
_SERIAL_FIELD_RE = re.compile(
    r"""(?i)((?:serial(?:number)?|device[_-]?id|udn)\s*["']?\s*[:=]\s*["']?)([A-Za-z0-9._:-]{6,})"""
)
_BYTE_SERIAL_FIELD_RE = re.compile(
    rb"""(?i)((?:serial(?:number)?|device[_-]?id|udn)\s*["']?\s*[:=]\s*["']?)([A-Za-z0-9._:-]{6,})"""
)
_ECN_ID_RE = re.compile(r"ECN[^/\r\n]+/\d+/[^/\r\n]*/([0-9A-Fa-f]{8,})")


def _packet(message: str) -> bytes:
    payload = message.encode("ascii")
    return b"ISCP" + struct.pack(">IIB3x", 16, len(payload), 1) + payload


def _packet_payload(data: bytes) -> bytes:
    if len(data) >= 16 and data[:4] == b"ISCP":
        header_size, data_size = struct.unpack(">II", data[4:12])
        if header_size >= 16 and header_size + data_size <= len(data):
            return data[header_size : header_size + data_size]
    return data


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _identity_strings(found: Found, raw_discovery: dict | None) -> list[str]:
    candidates: list[str] = []
    if found.id and found.id != found.host:
        candidates.append(found.id)

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"text", "xml", "raw_xml"} and isinstance(child, str):
                    for match in _XML_ID_RE.finditer(child):
                        candidates.append(match.group(2).strip())
                    candidates.extend(_ECN_ID_RE.findall(child))
                    candidates.extend(_TEXT_MAC_RE.findall(child))
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(raw_discovery or {})
    unique: list[str] = []
    for candidate in candidates:
        value = candidate.strip()
        if value and value != found.host and value not in unique:
            unique.append(value)
    return unique


def _raw_identities(response: dict, host: str) -> list[str]:
    shell = Found(driver="", model="", id="", host=host)
    return _identity_strings(shell, {"reply": response})


def _redact_raw_discovery(
    discovery: dict[str, list[dict]], known: dict[str, list[str]] | None = None
) -> None:
    known = known or {}
    for group in discovery.values():
        for response in group:
            host = str(response.get("host", ""))
            identities = list(known.get(host, []))
            for value in _raw_identities(response, host):
                if value not in identities:
                    identities.append(value)
            raw = base64.b64decode(response.get("data_b64", ""))
            response["data_b64"] = base64.b64encode(
                redact_bytes(raw, host, identities)
            ).decode("ascii")
            response["text"] = redact_text(
                response.get("text", ""), host, identities
            )
            for key, value in list(response.items()):
                if key in {"data_b64", "description"}:
                    continue
                if isinstance(value, str):
                    response[key] = redact_text(value, host, identities)
            description = response.get("description")
            if not isinstance(description, dict):
                continue
            xml = description.get("xml")
            if isinstance(xml, str):
                xml_bytes = redact_bytes(xml.encode("utf-8"), host, identities)
                redacted_xml = _decode(xml_bytes)
                description["xml"] = redacted_xml
                description["xml_b64"] = base64.b64encode(
                    xml_bytes
                ).decode("ascii")
            for key, value in list(description.items()):
                if key in {"xml", "xml_b64"}:
                    continue
                if isinstance(value, str):
                    description[key] = redact_text(value, host, identities)


def _fixed_placeholder(label: str | bytes, original: bytes) -> bytes:
    """Make an obvious placeholder without changing protocol byte offsets."""
    placeholder = label if isinstance(label, bytes) else label.encode("ascii")
    if len(original) <= len(placeholder):
        return placeholder[: len(original)]
    return placeholder + b"X" * (len(original) - len(placeholder))


def _fixed_ip_placeholder(original: bytes) -> bytes:
    if len(original) >= len(b"RECEIVER_IP"):
        return _fixed_placeholder(b"RECEIVER_IP", original)
    if len(original) <= 2:
        return b"IP"[: len(original)]
    return b"IP" + b"X" * (len(original) - 2)


def redact_bytes(data: bytes, host: str, identities: list[str] | None = None) -> bytes:
    """Redact sensitive byte strings with fixed-width replacements.

    Preserving byte counts keeps eISCP payload sizes and HTTP Content-Length
    correct even when a sensitive value occurs in a framed protocol payload.
    """
    result = data
    if host:
        host_bytes = host.encode("utf-8", errors="ignore")
        if host_bytes:
            result = result.replace(host_bytes, _fixed_ip_placeholder(host_bytes))
    for identity in identities or []:
        secret = identity.encode("utf-8", errors="ignore")
        if secret and secret in result:
            result = result.replace(secret, _fixed_placeholder(b"RECEIVER_ID", secret))

    def replace_mac(match: re.Match[bytes]) -> bytes:
        return _fixed_placeholder(b"RECEIVER_ID", match.group(0))

    result = _MAC_RE.sub(replace_mac, result)
    result = _BYTE_XML_ID_RE.sub(
        lambda match: match.group(1)
        + _fixed_placeholder(b"RECEIVER_ID", match.group(2))
        + match.group(3),
        result,
    )
    return _BYTE_SERIAL_FIELD_RE.sub(
        lambda match: match.group(1)
        + _fixed_placeholder(b"RECEIVER_ID", match.group(2)),
        result,
    )


def redact_text(text: str, host: str, identities: list[str] | None = None) -> str:
    result = text.replace(host, "RECEIVER_IP") if host else text
    for index, identity in enumerate(identities or [], start=1):
        if identity:
            result = result.replace(identity, f"RECEIVER_ID_{index}")

    def replace_mac(match: re.Match[str]) -> str:
        return "RECEIVER_ID_" + hashlib.sha256(
            match.group(0).encode("ascii")
        ).hexdigest()[:8]

    result = _TEXT_MAC_RE.sub(replace_mac, result)
    result = _SERIAL_FIELD_RE.sub(r"\1RECEIVER_ID", result)

    def replace_xml_id(match: re.Match[str]) -> str:
        value = match.group(2)
        if not value.strip():
            return match.group(0)
        return f"{match.group(1)}RECEIVER_ID{match.group(3)}"

    return _XML_ID_RE.sub(replace_xml_id, result)


def redact_document(
    document: dict, host: str, identities: list[str] | None = None
) -> dict:
    """Redact ordinary JSON values; byte exchanges are handled separately."""
    identities = identities or []

    def clean(value: Any, key: str = "") -> Any:
        if isinstance(value, dict):
            return {item_key: clean(item, item_key) for item_key, item in value.items()}
        if isinstance(value, list):
            return [clean(item, key) for item in value]
        if isinstance(value, str):
            if key in {"data_b64", "xml_b64"}:
                return value
            if key == "id":
                if host and value == host:
                    return "RECEIVER_IP"
                for index, identity in enumerate(identities, start=1):
                    if value == identity:
                        return f"RECEIVER_ID_{index}"
            return redact_text(value, host, identities)
        return value

    return clean(document)


def _redact_connections(
    connections: list[dict], host: str, identities: list[str]
) -> None:
    for connection in connections:
        exchanges = connection.get("exchanges", [])
        for direction in ("tx", "rx"):
            selected = [item for item in exchanges if item.get("dir") == direction]
            raw_chunks = [item.pop("_raw", b"") for item in selected]
            combined = b"".join(raw_chunks)
            sanitized = redact_bytes(combined, host, identities)
            position = 0
            for item, raw in zip(selected, raw_chunks):
                clean = sanitized[position : position + len(raw)]
                position += len(raw)
                item["data_b64"] = base64.b64encode(clean).decode("ascii")
                item["text"] = _decode(clean)
        for item in exchanges:
            item.pop("_raw", None)
            item["t"] = round(float(item["t"]), 6)
        exchanges.sort(key=lambda item: item["t"])


class _RawDatagram(asyncio.DatagramProtocol):
    def __init__(self, kind: str):
        self.kind = kind
        self.transport = None
        self.items: list[dict[str, Any]] = []

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, address) -> None:
        item: dict[str, Any] = {
            "kind": self.kind,
            "host": address[0],
            "port": address[1],
            "data_b64": base64.b64encode(data).decode("ascii"),
            "text": _decode(data),
        }
        if self.kind == "ssdp":
            headers: dict[str, str] = {}
            for line in _decode(data).splitlines()[1:]:
                if ":" in line:
                    key, value = line.split(":", 1)
                    headers[key.strip().lower()] = value.strip()
            item["location"] = headers.get("location", "")
            item["usn"] = headers.get("usn", "")
            item["server"] = headers.get("server", "")
            item["st"] = headers.get("st", "")
        self.items.append(item)


def _local_broadcast() -> str | None:
    try:
        addresses = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        return None
    for address in addresses:
        ip = address[4][0]
        parts = ip.split(".")
        if len(parts) == 4 and not ip.startswith("127."):
            return ".".join(parts[:3] + ["255"])
    return None


async def _raw_eiscp_discovery(timeout: float) -> list[dict]:
    loop = asyncio.get_running_loop()
    protocol = _RawDatagram("eiscp")
    try:
        transport, _ = await loop.create_datagram_endpoint(
            lambda: protocol,
            local_addr=("0.0.0.0", EISCP_PORT),
            allow_broadcast=True,
        )
    except OSError:
        transport, _ = await loop.create_datagram_endpoint(
            lambda: protocol, local_addr=("0.0.0.0", 0), allow_broadcast=True
        )
    targets = [("255.255.255.255", EISCP_PORT)]
    broadcast = _local_broadcast()
    if broadcast and broadcast != targets[0][0]:
        targets.append((broadcast, EISCP_PORT))
    messages = (_packet("!xECNQSTN\r"), _packet("!pECNQSTN\r"))
    deadline = loop.time() + max(timeout, 0)
    try:
        while loop.time() < deadline:
            for target in targets:
                for message in messages:
                    try:
                        transport.sendto(message, target)
                    except OSError:
                        pass
            await asyncio.sleep(min(0.9, max(0, deadline - loop.time())))
        return protocol.items
    finally:
        transport.close()


async def _raw_ssdp_discovery(timeout: float) -> list[dict]:
    loop = asyncio.get_running_loop()
    protocol = _RawDatagram("ssdp")
    transport, _ = await loop.create_datagram_endpoint(
        lambda: protocol, local_addr=("0.0.0.0", 0), allow_broadcast=True
    )
    targets = ("upnp:rootdevice", "urn:schemas-upnp-org:device:MediaRenderer:1")
    requests = [
        (
            "M-SEARCH * HTTP/1.1\r\n"
            f"HOST: {ssdp.SSDP_ADDRESS[0]}:{ssdp.SSDP_ADDRESS[1]}\r\n"
            'MAN: "ssdp:discover"\r\n'
            f"MX: {max(1, int(timeout))}\r\n"
            f"ST: {target}\r\n\r\n"
        ).encode("ascii")
        for target in targets
    ]
    deadline = loop.time() + max(timeout, 0)
    try:
        for request in requests:
            try:
                transport.sendto(request, ssdp.SSDP_ADDRESS)
            except OSError:
                pass
        await asyncio.sleep(max(0, deadline - loop.time()))
        replies = protocol.items
    finally:
        transport.close()

    async def attach_description(reply: dict) -> None:
        location = reply.get("location", "")
        if not location:
            return
        try:
            description = await ssdp.fetch_description(location)
        except Exception as exc:
            reply["description_error"] = type(exc).__name__
            return
        xml = str(description.get("raw_xml", ""))
        reply["description"] = {
            "friendlyName": description.get("friendlyName", ""),
            "manufacturer": description.get("manufacturer", ""),
            "modelName": description.get("modelName", ""),
            "UDN": description.get("UDN", ""),
            "xml_b64": base64.b64encode(xml.encode("utf-8")).decode("ascii"),
            "xml": xml,
        }

    await asyncio.gather(*(attach_description(reply) for reply in replies))
    return replies


async def raw_discovery(timeout: float) -> dict[str, list[dict]]:
    eiscp_replies, ssdp_replies = await asyncio.gather(
        _raw_eiscp_discovery(timeout), _raw_ssdp_discovery(timeout)
    )
    return {"eiscp": eiscp_replies, "ssdp": ssdp_replies}


class RecordingTcpProxy:
    """Local TCP forwarder that records every stream chunk in both directions."""

    def __init__(self, target_host: str, target_port: int):
        self.target_host = target_host
        self.target_port = int(target_port)
        self.started = time.monotonic()
        self.connections: list[dict] = []
        self.errors: list[str] = []
        self._handlers: set[asyncio.Task] = set()
        self.server: asyncio.AbstractServer | None = None

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return int(self.server.sockets[0].getsockname()[1])

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        if self._handlers:
            await asyncio.gather(*self._handlers, return_exceptions=True)

    def _record(self, connection: dict, direction: str, data: bytes) -> None:
        connection["exchanges"].append(
            {
                "dir": direction,
                "t": time.monotonic() - self.started,
                "_raw": bytes(data),
            }
        )

    async def _handle(
        self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ) -> None:
        handler = asyncio.current_task()
        if handler is not None:
            self._handlers.add(handler)
        connection = {"port": self.target_port, "exchanges": []}
        self.connections.append(connection)
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(
                self.target_host, self.target_port
            )
        except Exception as exc:
            self.errors.append(f"Could not connect to receiver: {exc}")
            client_writer.close()
            await client_writer.wait_closed()
            if handler is not None:
                self._handlers.discard(handler)
            return

        async def forward(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
            direction: str,
        ) -> None:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    return
                self._record(connection, direction, chunk)
                writer.write(chunk)
                await writer.drain()

        tasks = (
            asyncio.create_task(forward(client_reader, upstream_writer, "tx")),
            asyncio.create_task(forward(upstream_reader, client_writer, "rx")),
        )
        try:
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                exception = task.exception()
                if exception is not None:
                    self.errors.append(f"Proxy forwarding failed: {exception}")
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        finally:
            upstream_writer.close()
            client_writer.close()
            await asyncio.gather(
                upstream_writer.wait_closed(),
                client_writer.wait_closed(),
                return_exceptions=True,
            )
            if handler is not None:
                self._handlers.discard(handler)


async def read_snapshot(driver) -> dict:
    """Call the read-only driver seam and return a JSON-serializable snapshot."""
    status = await driver.status()
    status_data = {
        "volume": status.volume,
        "muted": status.muted,
        "power": status.power,
        "mode": status.mode,
    }
    capabilities = sorted(driver.capabilities)
    scale = driver.scale.as_dict()
    try:
        modes = [
            {"code": mode.code, "name": mode.name}
            for mode in await driver.list_modes()
        ]
        modes_error = None
    except Exception as exc:
        modes = []
        modes_error = f"{type(exc).__name__}: {exc}"

    getter_names = {
        "volume": "get_volume",
        "mute": "get_mute",
        "power": "get_power",
        "mode": "get_mode",
    }
    getters: dict[str, Any] = {}
    for capability, method_name in getter_names.items():
        if capability not in capabilities:
            continue
        try:
            getters[capability] = await getattr(driver, method_name)()
        except Exception as exc:
            getters[capability] = {"error": f"{type(exc).__name__}: {exc}"}
    return {
        "status": status_data,
        "capabilities": capabilities,
        "scale": scale,
        "modes": modes,
        "modes_error": modes_error,
        "getters": getters,
    }


async def _restore_state(driver, state: dict) -> list[str]:
    errors: list[str] = []
    setters = (
        ("volume", "set_volume"),
        ("muted", "set_mute"),
        ("mode", "set_mode"),
        ("power", "set_power"),
    )
    for field, method_name in setters:
        value = state.get(field)
        if value is None:
            continue
        try:
            await getattr(driver, method_name)(value)
        except Exception as exc:
            errors.append(f"{field}: {type(exc).__name__}: {exc}")
    return errors


async def _exercise_writes(driver, status: dict, power: bool) -> list[str]:
    """Run only explicitly requested state changes and best-effort restore."""
    errors: list[str] = []
    original = dict(status)
    restore = dict(original)
    if not power:
        restore.pop("power", None)
    try:
        if original.get("volume") is not None:
            await driver.set_volume(original["volume"])
        if original.get("muted") is not None:
            try:
                await driver.set_mute(not original["muted"])
            finally:
                await driver.set_mute(original["muted"])
        if original.get("mode") is not None:
            await driver.set_mode(original["mode"])
        if power and original.get("power") is not None:
            await driver.set_power(False)
            await driver.set_power(True)
    except Exception as exc:
        errors.append(f"write probe: {type(exc).__name__}: {exc}")
    finally:
        errors.extend(await _restore_state(driver, restore))
    return errors


def _default_port(driver_class, found: Found) -> int | None:
    configured = found.config.get("port")
    if configured is not None:
        try:
            return int(configured)
        except (TypeError, ValueError):
            return None
    for attr in ("EISCP_PORT", "DENON_PORT", "DEFAULT_PORT"):
        value = getattr(sys.modules[driver_class.__module__], attr, None)
        if value:
            return int(value)
    ports = getattr(sys.modules[driver_class.__module__], "TELNET_PORTS", ())
    return int(ports[0]) if ports else None


async def capture_found(
    found: Found,
    *,
    redact: bool = True,
    write: bool = False,
    power: bool = False,
    include_discovery: bool = False,
    source: str = "contributor-hardware",
) -> dict:
    """Capture a receiver already resolved to a driver, primarily for tests too."""
    driver_class = get_driver_class(found.driver)
    if driver_class is None:
        raise ValueError(f"Unknown driver key: {found.driver}")
    if found.driver == "homeassistant" or driver_class.key == "homeassistant":
        raise ValueError(
            "Home Assistant is excluded from capture because its token is secret."
        )
    target_port = _default_port(driver_class, found)
    if target_port is None:
        raise ValueError("The selected driver did not report a TCP port.")

    discovery = await raw_discovery(0.75) if include_discovery else {}
    recorder = RecordingTcpProxy(found.host, target_port)
    proxy_port = await recorder.start()
    config = dict(found.config)
    config.update({"host": "127.0.0.1", "port": proxy_port})
    driver = driver_class(config)
    readout: dict = {}
    write_errors: list[str] = []
    try:
        readout = await read_snapshot(driver)
        if write:
            status = readout["status"]
            state = {
                "volume": status["volume"],
                "muted": status["muted"],
                "power": status["power"],
                "mode": status["mode"],
            }
            write_errors = await _exercise_writes(driver, state, power)
    finally:
        try:
            await driver.close()
        except Exception:
            pass
        await recorder.close()

    identity_values = _identity_strings(found, discovery)
    connections = recorder.connections
    if redact:
        _redact_connections(connections, found.host, identity_values)

    document = {
        "schema_version": 1,
        "source": source,
        "tool": {"name": "avr_probe", "version": TOOL_VERSION},
        "timestamp": _datetime.datetime.now(_datetime.timezone.utc).isoformat(),
        "runtime": {"python": platform.python_version(), "os": platform.platform()},
        "driver": {"key": found.driver, "label": driver_class.label},
        "found": {
            "model": found.model,
            "id": found.id,
            "host": found.host,
        },
        "status": readout.get("status", {}),
        "capabilities": readout.get("capabilities", []),
        "scale": readout.get("scale", {}),
        "modes": readout.get("modes", []),
        "modes_error": readout.get("modes_error"),
        "getters": readout.get("getters", {}),
        "write_restore_errors": write_errors,
        "proxy_errors": list(recorder.errors),
        "connections": connections,
        "raw_discovery": discovery,
        "notes": [],
    }
    if found.driver in {"denon", "pioneer_telnet"}:
        document["notes"].append(
            "The local recording proxy uses the receiver's selected TCP port."
        )
    document["notes"].append(
        "The driver connects to 127.0.0.1 through the proxy; an HTTP driver would "
        "therefore send that address in its Host header."
    )
    if not redact:
        document["notes"].append(
            "Redaction was disabled; review this file before sharing it."
        )
    if redact:
        _redact_raw_discovery(
            document["raw_discovery"], {found.host: identity_values}
        )
        raw_replies = document.pop("raw_discovery")
        document = redact_document(document, found.host, identity_values)
        document["raw_discovery"] = raw_replies
    else:
        for connection in connections:
            for exchange in connection.get("exchanges", []):
                raw = exchange.pop("_raw", b"")
                exchange["data_b64"] = base64.b64encode(raw).decode("ascii")
                exchange["text"] = _decode(raw)
                exchange["t"] = round(float(exchange["t"]), 6)
    return document


async def _resolve(host: str, driver_key: str | None) -> Found | None:
    if driver_key:
        driver_class = get_driver_class(driver_key)
        if driver_class is None:
            raise ValueError(f"Unknown driver key: {driver_key}")
        return await driver_class.probe({"host": host})
    return await probe_all({"host": host})


async def _best_found(host: str, driver_key: str | None) -> Found | None:
    """Prefer a discovered model/port, then fall back to probing the address."""
    try:
        discovered = await discover_all(1.0)
    except Exception:
        discovered = []
    for item in discovered:
        if item.host == host and (driver_key is None or item.driver == driver_key):
            return item
    return await _resolve(host, driver_key)


def _write_json(document: dict, output: str) -> Path:
    path = Path(output)
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


async def command_discover(timeout: float, output: str) -> int:
    found = await discover_all(timeout)
    raw = await raw_discovery(timeout)
    document = {
        "schema_version": 1,
        "source": "discovery",
        "tool": {"name": "avr_probe", "version": TOOL_VERSION},
        "timestamp": _datetime.datetime.now(_datetime.timezone.utc).isoformat(),
        "runtime": {"python": platform.python_version(), "os": platform.platform()},
        "found": [
            {"driver": item.driver, "model": item.model, "id": item.id, "host": item.host}
            for item in found
        ],
        "raw_discovery": raw,
    }
    identities_by_host = {
        item.host: _identity_strings(item, raw) for item in found
    }
    _redact_raw_discovery(raw, identities_by_host)
    document["raw_discovery"] = raw
    raw_replies = document.pop("raw_discovery")
    sanitized = document
    for item in found:
        sanitized = redact_document(
            sanitized, item.host, identities_by_host[item.host]
        )
    sanitized["raw_discovery"] = raw_replies
    for item in found:
        print(
            f"{item.driver}: {item.model} ({item.id}) at {item.host}"
        )
    if not found:
        print("No receivers were discovered.")
    path = _write_json(sanitized, output)
    print(f"Raw discovery replies saved to {path}")
    return 0


async def command_probe(host: str) -> int:
    found = await probe_all({"host": host})
    if found is None:
        print(f"No supported receiver answered at {host}.")
        return 1
    driver_class = get_driver_class(found.driver)
    print(
        f"{found.driver} ({driver_class.label}) answered at {found.host}: "
        f"{found.model} [{found.id}]"
    )
    if found.driver == "homeassistant":
        print("Home Assistant is excluded from capture because its token is secret.")
    return 0


async def command_capture(
    host: str,
    driver_key: str | None,
    *,
    write: bool,
    power: bool,
    yes: bool,
    redact: bool,
    output: str,
) -> int:
    if power and not write:
        raise ValueError("--power requires --write.")
    if yes and not write:
        raise ValueError("--yes is only valid with --write.")
    if driver_key == "homeassistant":
        print("Home Assistant is excluded from capture because its token is secret.")
        return 1
    if write:
        if power:
            print("WARNING: --power will put the receiver in standby and turn it back on.")
        if not yes:
            try:
                answer = input(
                    "This will briefly change receiver state and restore it. Continue? [y/N] "
                ).strip().lower()
            except EOFError:
                answer = ""
            if answer not in {"y", "yes"}:
                print("Cancelled; no state-changing commands were sent.")
                return 1
    found = await _best_found(host, driver_key)
    if found is None:
        print(f"No supported receiver answered at {host}.")
        return 1
    if found.driver == "homeassistant":
        print("Home Assistant is excluded from capture because its token is secret.")
        return 1
    document = await capture_found(
        found,
        redact=redact,
        write=write,
        power=power,
        include_discovery=True,
    )
    path = _write_json(document, output)
    print(
        f"Captured {found.driver} ({found.model}): "
        f"{len(document['connections'])} TCP connection(s), "
        f"{sum(len(c['exchanges']) for c in document['connections'])} exchange chunk(s)."
    )
    if document.get("write_restore_errors"):
        print("Warning: state restoration was not fully confirmed; review write_restore_errors.")
    if not redact:
        print("Warning: redaction is disabled; review this file before sharing it.")
    print(f"Attach {path} to a GitHub \"Receiver report\" issue.")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)
    discover_parser = subparsers.add_parser("discover", help="discover receivers and save raw replies")
    discover_parser.add_argument("--timeout", type=float, default=3.0)
    discover_parser.add_argument("--out", default="avr_probe_discover.json")

    probe_parser = subparsers.add_parser("probe", help="check which driver answers at HOST")
    probe_parser.add_argument("host")

    capture_parser = subparsers.add_parser("capture", help="record a receiver through a local TCP proxy")
    capture_parser.add_argument("host")
    capture_parser.add_argument(
        "--driver",
        choices=[driver.key for driver in REGISTRY],
        help="force a driver instead of probing the registry",
    )
    capture_parser.add_argument("--write", action="store_true", help="run confirmed reversible state checks")
    capture_parser.add_argument("--yes", action="store_true", help="skip the interactive --write confirmation")
    capture_parser.add_argument("--power", action="store_true", help="also test standby/on (requires --write)")
    capture_parser.add_argument("--no-redact", action="store_true", help="disable default privacy redaction")
    capture_parser.add_argument("--out", default=DEFAULT_CAPTURE_OUT)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "discover":
            return asyncio.run(command_discover(args.timeout, args.out))
        if args.command == "probe":
            return asyncio.run(command_probe(args.host))
        if args.command == "capture":
            return asyncio.run(
                command_capture(
                    args.host,
                    args.driver,
                    write=args.write,
                    power=args.power,
                    yes=args.yes,
                    redact=not args.no_redact,
                    output=args.out,
                )
            )
    except KeyboardInterrupt:
        print("Cancelled.")
        return 130
    except Exception as exc:
        print(f"avr_probe: {exc}", file=sys.stderr)
        return 1
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
