#!/usr/bin/env python3
"""Collect the listening-mode codes your Pioneer/Onkyo receiver uses (eISCP, port 60128).

Usage:
    python3 collect_modes.py 192.168.1.229
    python3 collect_modes.py --discover          # find the receiver by broadcast

Then change the listening mode with the remote or the Pioneer app. Every time the
receiver reports a code you haven't recorded, the script asks what the receiver's
display shows for that mode. Press Ctrl+C when done. Results are saved to
listening_modes.json after every answer, so nothing is lost.

Only the standard library is needed. Close the Pioneer app first: eISCP receivers
usually accept one connection at a time. This script only reads the mode; it never
changes it.
"""
import argparse
import json
import os
import re
import socket
import struct
import sys
import time

PORT = 60128
MODE_RX = re.compile(r"LMD([0-9A-F]{2,4})")
DISCOVERY_RX = re.compile(r"ECN([^/\x19\r\n]+)/(\d+)/([^/]*)/([0-9A-Fa-f]+)")


def packet(msg: str) -> bytes:
    data = msg.encode()
    return b"ISCP" + struct.pack(">IIB3x", 16, len(data), 1) + data


def recv_exact(sock, n, allow_idle=False):
    """Read n bytes. Returns None if allow_idle and nothing arrived in time."""
    buf, started = b"", time.monotonic()
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except socket.timeout:
            if allow_idle and not buf:
                return None
            if time.monotonic() - started > 10:
                raise ConnectionError("receiver stopped mid-message")
            continue
        if not chunk:
            raise ConnectionError("receiver closed the connection")
        buf += chunk
    return buf


def read_message(sock):
    hdr = recv_exact(sock, 16, allow_idle=True)
    if hdr is None:
        return None
    if hdr[:4] != b"ISCP":
        raise ConnectionError("unexpected data (not eISCP)")
    hsize, dsize = struct.unpack(">II", hdr[4:12])
    if hsize > 16:
        recv_exact(sock, hsize - 16)
    return recv_exact(sock, dsize).decode(errors="ignore")


def discover(timeout=3.0):
    """UDP broadcast probe, like the Pioneer/Onkyo apps. Won't work from WSL2."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(0.3)
    end, last, found = time.monotonic() + timeout, 0.0, {}
    while time.monotonic() < end:
        if time.monotonic() - last > 1.0:
            for probe in ("!xECNQSTN\r", "!pECNQSTN\r"):
                try:
                    s.sendto(packet(probe), ("255.255.255.255", PORT))
                except OSError:
                    pass
            last = time.monotonic()
        try:
            data, addr = s.recvfrom(1024)
        except socket.timeout:
            continue
        m = DISCOVERY_RX.search(data[16:].decode(errors="ignore"))
        if m:
            found[addr[0]] = m.group(1)
    s.close()
    return found


def ask(prompt):
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("host", nargs="?", help="receiver IP address")
    ap.add_argument("--discover", action="store_true", help="find the receiver by broadcast")
    ap.add_argument("-o", "--output", default="listening_modes.json")
    args = ap.parse_args()

    host = args.host
    if not host:
        if not args.discover:
            ap.error("give the receiver's IP address, or use --discover")
        found = discover()
        if not found:
            sys.exit("No receiver answered. Give its IP address instead.")
        host, model = next(iter(found.items()))
        print(f"Found {model} at {host}")

    modes = {}
    if os.path.exists(args.output):
        try:
            old = json.load(open(args.output))
            if old.get("host") == host:
                modes = old.get("modes", {})
                print(f"Continuing from {args.output} ({len(modes)} modes already recorded)")
        except Exception:
            pass

    def save():
        with open(args.output, "w") as f:
            json.dump({"host": host, "modes": modes}, f, indent=2, sort_keys=True)

    try:
        sock = socket.create_connection((host, PORT), timeout=5)
    except OSError as e:
        sys.exit(f"Can't connect to {host}:{PORT} ({e}). Close the Pioneer app and check the IP.")
    sock.settimeout(1.0)

    print(
        "\nConnected. Now change the listening mode with the remote or the Pioneer app,\n"
        "one mode at a time. Each new mode asks for its name; Ctrl+C when finished.\n"
    )
    last_code, last_query = None, 0.0
    try:
        while True:
            now = time.monotonic()
            if now - last_query > 1.5:  # in case the receiver doesn't push changes
                sock.sendall(packet("!1LMDQSTN\r"))
                last_query = now
            msg = read_message(sock)
            if msg is None:
                continue
            m = MODE_RX.search(msg)
            if not m or m.group(1).upper() == last_code:
                continue
            code = last_code = m.group(1).upper()
            if modes.get(code):
                print(f"  {code}: {modes[code]} (already recorded)")
                continue
            modes[code] = ask(f"New listening mode, code {code}. What does the receiver show? (Enter to skip) > ")
            save()
    except KeyboardInterrupt:
        print()
    except ConnectionError as e:
        print(f"\nStopped: {e}")
    finally:
        sock.close()

    print(f"\nRecorded {len(modes)} listening mode(s), saved to {args.output}:\n")
    for code, name in sorted(modes.items()):
        print(f"  {code}  {name or '(unnamed)'}")
    print("\nPaste this list into the chat and the plugin can offer a mode picker.")


if __name__ == "__main__":
    main()
