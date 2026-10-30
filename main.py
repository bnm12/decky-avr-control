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


async def _read_eiscp(reader) -> str:
    hdr = await reader.readexactly(16)
    if hdr[:4] != b"ISCP":
        raise RuntimeError("Unexpected reply (not eISCP)")
    hsize, dsize = struct.unpack(">II", hdr[4:12])
    if hsize > 16:
        await reader.readexactly(hsize - 16)
    return (await reader.readexactly(dsize)).decode(errors="ignore")


async def _eiscp(host: str, port: int, cmd: str, pattern: str):
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 2)
    try:
        writer.write(_packet(f"!1{cmd}\r"))
        await writer.drain()

        async def wait():
            while True:
                m = re.search(pattern, await _read_eiscp(reader))
                if m:
                    return m

        return await asyncio.wait_for(wait(), 2)
    finally:
        writer.close()


async def _eiscp_multi(host: str, port: int, queries: list):
    """Several queries over ONE connection.

    queries: [(cmd, pattern, required)]. Returns a list of matches (None for
    optional queries the AVR didn't answer within a short grace period).
    """
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 2)
    try:
        for cmd, _, _ in queries:
            writer.write(_packet(f"!1{cmd}\r"))
        await writer.drain()
        rx = [re.compile(p) for _, p, _ in queries]
        res = [None] * len(queries)

        async def pump(want):
            while any(res[i] is None for i in want):
                payload = await _read_eiscp(reader)
                for i, r in enumerate(rx):
                    if res[i] is None:
                        res[i] = r.search(payload)

        required = [i for i, q in enumerate(queries) if q[2]]
        optional = [i for i, q in enumerate(queries) if not q[2]]
        await asyncio.wait_for(pump(required), 2)
        if optional:
            try:
                await asyncio.wait_for(pump(optional), 0.6)
            except Exception:
                pass
        return res
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


MODE_NAMES = {"00": "Stereo", "01": "Direct", "0C": "All Channel Stereo", "40": "Straight Decode"}

_current_game = None        # {"appid": int, "name": str} while a game is running
_suppress_save_until = 0.0  # don't overwrite a game profile right after applying it


def _base_cap(cfg: dict) -> int:
    return int(cfg.get("max") or DEFAULT_MAX.get(cfg.get("proto", "eiscp"), 100))


def _late_cap(cfg: dict) -> int:
    # default: 50 dB below 0 dB
    return int(cfg.get("late_max") or ZERO_DB[cfg.get("proto", "eiscp")] - 100)


def _late_active(cfg: dict) -> bool:
    return bool(cfg.get("late", {}).get("active"))


def _cap() -> int:
    cfg = _load()
    base = _base_cap(cfg)
    return min(base, _late_cap(cfg)) if _late_active(cfg) else base


def _vol(proto: str, m) -> int:
    return int(m.group(1), 16) if proto == "eiscp" else int(m.group(1))


def _muted(proto: str, m) -> bool:
    return m.group(1) == ("01" if proto == "eiscp" else "0")


def _mode_label(mode):
    if not mode:
        return None
    name = MODE_NAMES.get(mode.upper())
    return f"{name} ({mode})" if name else f"Mode {mode}"


async def _status() -> dict:
    """Volume, mute and listening mode. One eISCP connection where possible."""
    cfg = _load()
    if not cfg.get("host"):
        raise RuntimeError("Set the AVR IP address first, or tap Search network")
    if cfg.get("proto") != "telnet":
        try:
            vol, mute, mode = await _eiscp_multi(
                cfg["host"],
                EISCP_PORT,
                [
                    ("MVLQSTN", r"MVL([0-9A-F]{2})", True),
                    ("AMTQSTN", r"AMT([0-9A-F]{2})", True),
                    ("LMDQSTN", r"LMD([0-9A-F]{2,4})", False),
                ],
            )
            if cfg.get("port") != EISCP_PORT or cfg.get("proto") != "eiscp":
                cfg["port"], cfg["proto"] = EISCP_PORT, "eiscp"
                _save(cfg)
            return {
                "proto": "eiscp",
                "volume": int(vol.group(1), 16),
                "muted": mute.group(1) == "01",
                "mode": mode.group(1).upper() if mode else None,
            }
        except Exception:
            pass  # fall through to the generic path (telnet, rediscovery)
    proto, v = await _request(("?V", r"VOL(\d+)"), ("MVLQSTN", r"MVL([0-9A-F]{2})"))
    proto, m = await _request(("?M", r"MUT([01])"), ("AMTQSTN", r"AMT([0-9A-F]{2})"))
    return {"proto": proto, "volume": _vol(proto, v), "muted": _muted(proto, m), "mode": None}


async def _set_mode(code: str) -> None:
    cfg = _load()
    await _eiscp(cfg["host"], EISCP_PORT, f"LMD{code}", r"LMD([0-9A-F]{2,4})")


def _remember(volume=None, mode=None) -> None:
    """Keep the running game's profile in step, for the things it remembers."""
    if not _current_game or time.monotonic() < _suppress_save_until:
        return
    cfg = _load()
    p = cfg.get("games", {}).get(str(_current_game["appid"]))
    if not p:
        return
    if _late_active(cfg):  # late-night volume must not leak into profiles
        volume = None
    changed = False
    if volume is not None and p.get("volume") is not None and p["volume"] != volume:
        p["volume"], changed = volume, True
    if mode is not None and p.get("mode") is not None and p["mode"] != mode:
        p["mode"], changed = mode, True
    if changed:
        _save(cfg)


async def _apply_profile(p: dict) -> None:
    vol = p.get("volume")
    if vol is not None:
        vol = max(0, min(_cap(), int(vol)))
        await _request(
            (f"{vol:03d}VL", r"VOL(\d+)"), (f"MVL{vol:02X}", r"MVL([0-9A-F]{2})")
        )
    if p.get("mode") and _load().get("proto") == "eiscp":
        try:
            await _set_mode(p["mode"])
        except Exception as e:
            decky.logger.error(f"Setting listening mode failed: {e}")


async def _reapply_mode(appid: int, mode) -> None:
    """AVRs sometimes switch mode once the game's audio starts; set it once more."""
    if not mode:
        return
    await asyncio.sleep(4)
    if not _current_game or _current_game["appid"] != appid:
        return
    try:
        st = await _status()
        if st.get("mode") and st["mode"] != mode:
            await _set_mode(mode)
    except Exception as e:
        decky.logger.error(f"Re-applying listening mode failed: {e}")


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
            st = await _status()
            _remember(volume=st["volume"], mode=st["mode"])
            cfg = _load()
            game = None
            if _current_game:
                prof = cfg.get("games", {}).get(str(_current_game["appid"]))
                prof = prof or {}
                game = {
                    **_current_game,
                    "volume": prof.get("volume") is not None,
                    "mode": prof.get("mode") is not None,
                }
            return {
                "ok": True,
                "volume": st["volume"],
                "muted": st["muted"],
                "max": _cap(),
                "cap": _base_cap(cfg),
                "late": _late_active(cfg),
                "late_cap": _late_cap(cfg),
                "zero": ZERO_DB[st["proto"]],
                "mode": _mode_label(st["mode"]),
                "game": game,
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
            _remember(volume=_vol(proto, v))
            return {"ok": True, "volume": _vol(proto, v)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ----------------------------------------------------- per-game profiles

    async def set_current_game(self, appid: int, name: str) -> None:
        """Tell the backend which game is running (does not touch the AVR)."""
        global _current_game
        _current_game = {"appid": int(appid), "name": name} if appid else None

    async def game_started(self, appid: int, name: str) -> dict:
        """A game launched: if it opted in, restore its volume and listening mode."""
        global _current_game, _suppress_save_until
        _current_game = {"appid": int(appid), "name": name}
        p = _load().get("games", {}).get(str(int(appid)))
        if not p:
            return {"applied": False}
        _suppress_save_until = time.monotonic() + 6
        try:
            await _apply_profile(p)
        except Exception as e:
            decky.logger.error(f"Applying game profile failed: {e}")
            return {"applied": False, "error": str(e)}
        asyncio.create_task(_reapply_mode(int(appid), p.get("mode")))
        return {"applied": True}

    async def game_stopped(self, appid: int) -> None:
        global _current_game
        if _current_game and _current_game["appid"] == int(appid):
            _current_game = None

    async def set_game_remember(self, what: str, enabled: bool) -> dict:
        """Opt the running game in or out of remembering its volume or listening mode."""
        if what not in ("volume", "mode"):
            return {"ok": False, "error": f"Unknown setting: {what}"}
        if not _current_game:
            return {"ok": False, "error": "No game is running"}
        key = str(_current_game["appid"])
        if enabled:
            try:
                st = await _status()
            except Exception as e:
                return {"ok": False, "error": str(e)}
            cfg = _load()
            if what == "volume":
                value = st["volume"]
                if _late_active(cfg):  # save the normal level, not the late-night one
                    value = cfg["late"].get("prev_volume", value)
            else:
                value = st["mode"]
                if not value:
                    return {"ok": False, "error": "Receiver doesn't report a listening mode"}
            p = cfg.setdefault("games", {}).setdefault(key, {})
            p["name"] = _current_game["name"]
            p[what] = value
        else:
            cfg = _load()
            games = cfg.setdefault("games", {})
            p = games.get(key)
            if p:
                p.pop(what, None)
                if p.get("volume") is None and p.get("mode") is None:
                    games.pop(key)
        _save(cfg)
        return {"ok": True}

    async def list_games(self) -> list:
        return [
            {
                "appid": int(k),
                "name": v.get("name", ""),
                "volume": v.get("volume"),
                "mode": v.get("mode"),
            }
            for k, v in _load().get("games", {}).items()
        ]

    async def forget_game(self, appid: int) -> None:
        cfg = _load()
        cfg.get("games", {}).pop(str(int(appid)), None)
        _save(cfg)

    # ------------------------------------------------------------ late night

    async def set_late_max(self, value: int) -> None:
        cfg = _load()
        cfg["late_max"] = max(1, min(185, int(value)))
        _save(cfg)

    async def set_late_night(self, on: bool) -> dict:
        """On: remember the volume, apply the lower cap. Off: restore both."""
        try:
            cfg = _load()
            active = _late_active(cfg)
            if on and not active:
                st = await _status()
                cfg = _load()
                cfg["late"] = {"active": True, "prev_volume": st["volume"]}
                _save(cfg)
                cap = _cap()
                if st["volume"] > cap:
                    await self.set_volume(cap)
            elif not on and active:
                prev = cfg["late"].get("prev_volume")
                cfg["late"] = {"active": False}
                _save(cfg)
                if prev is not None:
                    await self.set_volume(prev)
            return {"ok": True}
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
