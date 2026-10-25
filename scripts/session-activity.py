#!/usr/bin/env python3
"""Is anyone using THIS machine for something a reboot would interrupt? (issue #42)

maintenance-window.sh already refuses to reboot while someone is watching Plex. This covers the two other things people
do on these machines:

  Sunshine/Moonlight   GET http://127.0.0.1:47989/serverinfo -> <state>SUNSHINE_SERVER_BUSY|FREE</state>. This is the
                       unauthenticated status call Moonlight itself uses; BUSY means a streaming session is live.
  Minecraft            player count from each server's own status ping: Java servers over TCP (Server List Ping),
                       Bedrock servers over UDP (RakNet unconnected ping). Only r9 runs Pterodactyl, but the ports are
                       simply closed elsewhere, which reads as "no players".

Prints nothing and exits 0 when idle. When busy, prints a one-line reason (e.g. "Minecraft: 2 player(s) on :25501")
and exits 1. If a service answers but its state cannot be read, prints "<thing> unknown" and exits 2: the caller treats
that like Plex "unknown" (don't reboot on a guess).

  session-activity.py             check this machine
"""
import json, re, socket, struct, sys, urllib.request

SUNSHINE = "http://127.0.0.1:47989/serverinfo"
JAVA_PORTS = [25500]
BEDROCK_PORTS = [25501, 25502]


def sunshine():
    try:
        body = urllib.request.urlopen(SUNSHINE, timeout=5).read().decode(errors="replace")
    except OSError:
        return None  # not running here
    m = re.search(r"<state>([^<]*)</state>", body)
    if not m:
        return "Sunshine unknown"
    return "Sunshine: streaming session active" if m.group(1) == "SUNSHINE_SERVER_BUSY" else None


def varint(n):
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def read_varint(sock):
    n = shift = 0
    while True:
        b = sock.recv(1)
        if not b:
            raise OSError("closed")
        n |= (b[0] & 0x7F) << shift
        if not b[0] & 0x80:
            return n
        shift += 7


def java_players(port):
    """players.online from a Java server, None if nothing is listening."""
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=5)
    except OSError:
        return None
    with s:
        host = b"127.0.0.1"
        hs = b"\x00" + varint(767) + varint(len(host)) + host + struct.pack(">H", port) + varint(1)
        s.sendall(varint(len(hs)) + hs + b"\x01\x00")
        read_varint(s)  # packet length
        if read_varint(s) != 0:
            raise OSError("unexpected packet")
        n = read_varint(s)
        data = b""
        while len(data) < n:
            chunk = s.recv(n - len(data))
            if not chunk:
                raise OSError("short read")
            data += chunk
    return int(json.loads(data)["players"]["online"])


MAGIC = bytes.fromhex("00ffff00fefefefefdfdfdfd12345678")


def bedrock_players(port):
    """Online players from a Bedrock server, None if it does not answer (stopped)."""
    ping = b"\x01" + struct.pack(">Q", 0) + MAGIC + struct.pack(">Q", 2)
    for _ in range(3):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1.5)
        try:
            s.sendto(ping, ("127.0.0.1", port))
            data, _ = s.recvfrom(2048)
        except OSError:
            continue
        finally:
            s.close()
        if data[:1] == b"\x1c":
            fields = data[35:].decode(errors="replace").split(";")  # 1+8+8+16 header, then 2-byte length prefix
            return int(fields[4])
        raise OSError("unexpected reply")
    return None


def main():
    busy, unknown = [], []
    s = sunshine()
    if s:
        (unknown if s.endswith("unknown") else busy).append(s)
    for kind, ports, fn in (("Java", JAVA_PORTS, java_players), ("Bedrock", BEDROCK_PORTS, bedrock_players)):
        for p in ports:
            try:
                n = fn(p)
            except Exception:
                unknown.append(f"Minecraft {kind} :{p} unknown")
                continue
            if n:
                busy.append(f"Minecraft: {n} player(s) on :{p}")
    if busy:
        print("; ".join(busy))
        return 1
    if unknown:
        print("; ".join(unknown))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
