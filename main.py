import asyncio
import json
import os
import re
import struct

import decky

EISCP_PORT = 60128          # Onkyo/Pioneer eISCP (most recent Pioneer VSX models)
TELNET_PORTS = (23, 8102)   # older Pioneer text protocol
# Slider cap defaults (raw receiver units). Override in the plugin UI.
DEFAULT_MAX = {"eiscp": 100, "telnet": 161}
# Raw value that equals 0 dB (0.5 dB per step). eISCP: measured on a VSX-935
# (raw 48 = -58 dB, raw 50 = -57 dB), so 0 dB is raw 164.
ZERO_DB = {"eiscp": 164, "telnet": 161}
SETTINGS = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "settings.json")


def _load() -> dict:
    try:
        with open(SETTINGS) as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    with open(SETTINGS, "w") as f:
        json.dump(data, f)


async def _telnet(host: str, port: int, cmd: str, pattern: str):
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 2)
    try:
        writer.write((cmd + "\r\n").encode())
        await writer.drain()

        async def wait():
            while True:
                line = await reader.readline()
                if not line:
                    raise RuntimeError("AVR closed the connection")
                m = re.search(pattern, line.decode(errors="ignore"))
                if m:
                    return m

        return await asyncio.wait_for(wait(), 2)
    finally:
        writer.close()


async def _eiscp(host: str, port: int, cmd: str, pattern: str):
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 2)
    try:
        data = f"!1{cmd}\r".encode()
        writer.write(b"ISCP" + struct.pack(">IIB3x", 16, len(data), 1) + data)
        await writer.drain()

        async def wait():
            while True:
                hdr = await reader.readexactly(16)
                if hdr[:4] != b"ISCP":
                    raise RuntimeError("Unexpected reply (not eISCP)")
                hsize, dsize = struct.unpack(">II", hdr[4:12])
                if hsize > 16:
                    await reader.readexactly(hsize - 16)
                payload = (await reader.readexactly(dsize)).decode(errors="ignore")
                m = re.search(pattern, payload)
                if m:
                    return m

        return await asyncio.wait_for(wait(), 2)
    finally:
        writer.close()


async def _request(tel: tuple, eis: tuple):
    """Run a command on whichever protocol the AVR answers. Returns (proto, match)."""
    cfg = _load()
    host = cfg.get("host")
    if not host:
        raise RuntimeError("Set the AVR IP address first")
    ports = [EISCP_PORT, *TELNET_PORTS]
    if cfg.get("port") in ports:
        ports.remove(cfg["port"])
        ports.insert(0, cfg["port"])

    last_err = None
    for port in ports:
        proto = "eiscp" if port == EISCP_PORT else "telnet"
        cmd, pattern = eis if proto == "eiscp" else tel
        try:
            if proto == "eiscp":
                m = await _eiscp(host, port, cmd, pattern)
            else:
                m = await _telnet(host, port, cmd, pattern)
        except Exception as e:
            last_err = e
            continue
        if cfg.get("port") != port or cfg.get("proto") != proto:
            cfg["port"], cfg["proto"] = port, proto
            _save(cfg)
        return proto, m
    raise RuntimeError(f"Can't reach AVR (tried ports 60128, 23, 8102): {last_err}")


def _cap() -> int:
    cfg = _load()
    return int(cfg.get("max") or DEFAULT_MAX.get(cfg.get("proto", "eiscp"), 100))


def _vol(proto: str, m) -> int:
    return int(m.group(1), 16) if proto == "eiscp" else int(m.group(1))


def _muted(proto: str, m) -> bool:
    return m.group(1) == ("01" if proto == "eiscp" else "0")


class Plugin:
    async def get_host(self) -> str:
        return _load().get("host", "")

    async def set_host(self, host: str) -> None:
        cfg = _load()
        cfg["host"] = host.strip()
        cfg.pop("port", None)
        cfg.pop("proto", None)
        _save(cfg)

    async def set_max(self, value: int) -> None:
        cfg = _load()
        cfg["max"] = max(1, min(185, int(value)))
        _save(cfg)

    async def get_state(self) -> dict:
        try:
            proto, v = await _request(
                ("?V", r"VOL(\d+)"), ("MVLQSTN", r"MVL([0-9A-F]{2})")
            )
            proto, m = await _request(
                ("?M", r"MUT([01])"), ("AMTQSTN", r"AMT([0-9A-F]{2})")
            )
            return {
                "ok": True,
                "volume": _vol(proto, v),
                "muted": _muted(proto, m),
                "max": _cap(),
                "zero": ZERO_DB[proto],
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def set_volume(self, vol: int) -> dict:
        try:
            vol = max(0, min(_cap(), int(vol)))
            proto, v = await _request(
                (f"{vol:03d}VL", r"VOL(\d+)"),
                (f"MVL{vol:02X}", r"MVL([0-9A-F]{2})"),
            )
            return {"ok": True, "volume": _vol(proto, v)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def set_mute(self, muted: bool) -> dict:
        try:
            proto, m = await _request(
                ("MO" if muted else "MF", r"MUT([01])"),
                ("AMT01" if muted else "AMT00", r"AMT([0-9A-F]{2})"),
            )
            return {"ok": True, "muted": _muted(proto, m)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def _main(self):
        decky.logger.info("Pioneer Volume loaded")

    async def _unload(self):
        decky.logger.info("Pioneer Volume unloaded")
