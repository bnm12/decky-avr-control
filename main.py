import asyncio
import json
import os
import re
import socket
import struct
import time

import decky

EISCP_PORT = 60128          # Onkyo/Pioneer eISCP (most recent Pioneer VSX models)
TELNET_PORTS = (23, 8102)   # older Pioneer text protocol
# Slider cap defaults (raw receiver units). Override in the plugin UI.
DEFAULT_MAX = {"eiscp": 100, "telnet": 161}
# Raw value that equals 0 dB (0.5 dB per step). eISCP: measured on a VSX-935
# (raw 48 = -58 dB, raw 50 = -57 dB), so 0 dB is raw 164.
ZERO_DB = {"eiscp": 164, "telnet": 161}
SETTINGS = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "settings.json")

# Test hooks for discovery.
DISCOVERY_TARGETS = None
DISCOVERY_BIND_PORT = EISCP_PORT

_last_rediscover = 0.0


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


def _packet(msg: str) -> bytes:
    data = msg.encode()
    return b"ISCP" + struct.pack(">IIB3x", 16, len(data), 1) + data


# ---------------------------------------------------------------- discovery

_DISCOVERY_RE = re.compile(r"ECN([^/\x19\r\n]+)/(\d+)/([^/]*)/([0-9A-Fa-f]+)")


def _local_prefix():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))  # UDP connect: sends nothing, just picks a route
        ip = s.getsockname()[0]
        s.close()
        return ip.rsplit(".", 1)[0]
    except Exception:
        return None


def _discover_blocking(timeout: float = 2.5) -> list:
    """Broadcast the Onkyo ('x') and Pioneer ('p') eISCP discovery probes."""
    targets = DISCOVERY_TARGETS
    if targets is None:
        targets = [("255.255.255.255", EISCP_PORT)]
        prefix = _local_prefix()
        if prefix:
            targets.append((prefix + ".255", EISCP_PORT))

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            # Using 60128 as source port covers receivers that reply to it.
            sock.bind(("", DISCOVERY_BIND_PORT))
        except OSError:
            sock.bind(("", 0))
        sock.settimeout(0.3)

        probes = [_packet("!xECNQSTN\r"), _packet("!pECNQSTN\r")]
        found = {}
        end = time.monotonic() + timeout
        last_send = 0.0
        while time.monotonic() < end:
            if time.monotonic() - last_send >= 1.0:  # UDP is lossy: resend
                for t in targets:
                    for p in probes:
                        try:
                            sock.sendto(p, t)
                        except OSError:
                            pass
                last_send = time.monotonic()
            try:
                data, addr = sock.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                break
            if data[:4] != b"ISCP":
                continue
            m = _DISCOVERY_RE.search(data[16:].decode(errors="ignore"))
            if m:
                ident = m.group(4).upper()
                found[ident] = {"ip": addr[0], "model": m.group(1), "id": ident}
        return list(found.values())
    finally:
        sock.close()


async def _discover(timeout: float = 2.5) -> list:
    return await asyncio.get_running_loop().run_in_executor(
        None, _discover_blocking, timeout
    )


# ----------------------------------------------------------------- transport


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
        writer.write(_packet(f"!1{cmd}\r"))
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


async def _try_ports(cfg: dict, tel: tuple, eis: tuple):
    host = cfg["host"]
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


async def _request(tel: tuple, eis: tuple):
    """Run a command on whichever protocol the AVR answers. Returns (proto, match).

    If the AVR can't be reached and we know its ID, look for it on the network
    again (its IP may have changed) and retry once.
    """
    global _last_rediscover
    cfg = _load()
    if not cfg.get("host"):
        raise RuntimeError("Set the AVR IP address first, or tap Search network")
    try:
        return await _try_ports(cfg, tel, eis)
    except Exception:
        ident = cfg.get("id")
        if ident and time.monotonic() - _last_rediscover > 30:
            _last_rediscover = time.monotonic()
            for r in await _discover():
                if r["id"] == ident and r["ip"] != cfg["host"]:
                    decky.logger.info(f"AVR moved {cfg['host']} -> {r['ip']}")
                    cfg["host"] = r["ip"]
                    cfg.pop("port", None)
                    cfg.pop("proto", None)
                    _save(cfg)
                    return await _try_ports(cfg, tel, eis)
        raise


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
        """Manual IP entry. Forgets any discovered receiver ID."""
        cfg = _load()
        cfg["host"] = host.strip()
        for k in ("port", "proto", "id"):
            cfg.pop(k, None)
        _save(cfg)

    async def select_receiver(self, ip: str, ident: str) -> None:
        cfg = _load()
        cfg["host"], cfg["id"] = ip, ident.upper()
        for k in ("port", "proto"):
            cfg.pop(k, None)
        _save(cfg)

    async def discover(self) -> list:
        try:
            return await _discover()
        except Exception as e:
            decky.logger.error(f"Discovery failed: {e}")
            return []

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
