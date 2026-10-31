"""Small stdlib SSDP/UPnP discovery helper for network AVR drivers."""

from __future__ import annotations

import asyncio
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

SSDP_ADDRESS = ("239.255.255.250", 1900)


class _SearchProtocol(asyncio.DatagramProtocol):
    def __init__(self):
        self.responses: list[dict[str, str]] = []
        self.seen: set[tuple[str, str, str, str, str]] = set()

    def datagram_received(self, data: bytes, addr) -> None:
        text = data.decode("iso-8859-1", errors="ignore")
        headers: dict[str, str] = {}
        for line in text.splitlines()[1:]:
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
        item = {
            "location": headers.get("location", ""),
            "usn": headers.get("usn", ""),
            "server": headers.get("server", ""),
            "st": headers.get("st", ""),
            "ip": addr[0],
        }
        signature = (
            item["location"],
            item["usn"],
            item["server"],
            item["st"],
            item["ip"],
        )
        if any(signature) and signature not in self.seen:
            self.seen.add(signature)
            self.responses.append(item)


async def search(st: str, timeout: float) -> list[dict]:
    """Send an M-SEARCH and return unique response headers and sender addresses."""
    loop = asyncio.get_running_loop()
    protocol = _SearchProtocol()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: protocol, local_addr=("0.0.0.0", 0), allow_broadcast=True
    )
    request = (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {SSDP_ADDRESS[0]}:{SSDP_ADDRESS[1]}\r\n"
        'MAN: "ssdp:discover"\r\n'
        f"MX: {max(1, int(timeout))}\r\n"
        f"ST: {st}\r\n\r\n"
    ).encode("ascii")
    try:
        transport.sendto(request, SSDP_ADDRESS)
        await asyncio.sleep(max(0.0, timeout))
        return list(protocol.responses)
    finally:
        transport.close()


async def search_host(
    host: str, st: str, timeout: float, port: int = 1900
) -> list[dict]:
    """Send an M-SEARCH directly to one host (useful when probing a typed IP)."""
    loop = asyncio.get_running_loop()
    protocol = _SearchProtocol()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: protocol, local_addr=("0.0.0.0", 0)
    )
    request = (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {host}:{port}\r\n"
        'MAN: "ssdp:discover"\r\n'
        f"MX: {max(1, int(timeout))}\r\n"
        f"ST: {st}\r\n\r\n"
    ).encode("ascii")
    try:
        transport.sendto(request, (host, port))
        await asyncio.sleep(max(0.0, timeout))
        return list(protocol.responses)
    finally:
        transport.close()


def _fetch(location: str) -> dict[str, Any]:
    request = urllib.request.Request(location, headers={"User-Agent": "AVR Control"})
    with urllib.request.urlopen(request, timeout=5.0) as response:
        raw_xml = response.read().decode("utf-8", errors="replace")
    root = ET.fromstring(raw_xml)
    values = {}
    wanted = {"friendlyName", "manufacturer", "modelName", "UDN"}
    for element in root.iter():
        local_name = element.tag.rsplit("}", 1)[-1]
        if local_name in wanted and local_name not in values:
            values[local_name] = (element.text or "").strip()
    return {
        "friendlyName": values.get("friendlyName", ""),
        "manufacturer": values.get("manufacturer", ""),
        "modelName": values.get("modelName", ""),
        "UDN": values.get("UDN", ""),
        "raw_xml": raw_xml,
    }


async def fetch_description(location: str) -> dict:
    """Fetch and parse the UPnP device description at an SSDP LOCATION."""
    return await asyncio.get_running_loop().run_in_executor(None, _fetch, location)
