import asyncio
import json
import os
import re

import decky

PORTS = (23, 8102)
MAX_VOL = 161  # 0 dB. Raise up to 185 (+12 dB) if you really want.
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


async def _send(cmd: str, pattern: str):
    """Send one command, return the first response line matching pattern."""
    cfg = _load()
    host = cfg.get("host")
    if not host:
        raise RuntimeError("Set the AVR IP address first")
    ports = list(PORTS)
    if cfg.get("port") in ports:
        ports.remove(cfg["port"])
        ports.insert(0, cfg["port"])

    last_err = None
    for port in ports:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), 2
            )
        except Exception as e:
            last_err = e
            continue
        try:
            writer.write((cmd + "\r\n").encode())
            await writer.drain()

            async def wait_match():
                while True:
                    line = await reader.readline()
                    if not line:
                        return None
                    m = re.search(pattern, line.decode(errors="ignore"))
                    if m:
                        return m

            m = await asyncio.wait_for(wait_match(), 2)
            if m is None:
                raise RuntimeError("AVR closed the connection")
            if cfg.get("port") != port:
                cfg["port"] = port
                _save(cfg)
            return m
        except Exception as e:
            last_err = e
        finally:
            writer.close()
    raise RuntimeError(f"Can't reach AVR: {last_err}")


class Plugin:
    async def get_host(self) -> str:
        return _load().get("host", "")

    async def set_host(self, host: str) -> None:
        cfg = _load()
        cfg["host"] = host.strip()
        cfg.pop("port", None)
        _save(cfg)

    async def get_state(self) -> dict:
        try:
            v = await _send("?V", r"VOL(\d+)")
            m = await _send("?M", r"MUT([01])")
            return {"ok": True, "volume": int(v.group(1)), "muted": m.group(1) == "0",
                    "max": MAX_VOL}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def set_volume(self, vol: int) -> dict:
        try:
            vol = max(0, min(MAX_VOL, int(vol)))
            v = await _send(f"{vol:03d}VL", r"VOL(\d+)")
            return {"ok": True, "volume": int(v.group(1))}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def set_mute(self, muted: bool) -> dict:
        try:
            m = await _send("MO" if muted else "MF", r"MUT([01])")
            return {"ok": True, "muted": m.group(1) == "0"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def _main(self):
        decky.logger.info("Pioneer Volume loaded")

    async def _unload(self):
        decky.logger.info("Pioneer Volume unloaded")
