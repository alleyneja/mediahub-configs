#!/usr/bin/env python3
"""Hold the Xbox (Guide) button for HOLD seconds to close an emulator that has no
pad-button quit of its own.

Dolphin gets this from `General/Stop = Guide` in its Hotkeys.ini. RPCS3 has no equivalent, and
the stream has no keyboard, so a controller-only user was stuck inside it (2026-09-20). A short
tap still reaches the game (RPCS3 maps Guide to the PS button); only a long hold closes it.

The Sunshine pad only exists while a Moonlight client is connected and gets a new /dev/input
number each time, so the device is found by NAME and re-found after every disconnect.
Needs: python3-evdev.
"""
import select, subprocess, time
import evdev
from evdev import ecodes

PAD_NAME = "Sunshine X-Box One (virtual) pad"
HOLD = 1.5
TARGETS = ["rpcs3"]          # exact process names; add emulators that lack their own quit hotkey


def find_pad():
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except OSError:
            continue
        if dev.name == PAD_NAME:
            return dev
        dev.close()
    return None


def close_targets():
    for name in TARGETS:
        if subprocess.run(["pgrep", "-x", name], capture_output=True).returncode == 0:
            subprocess.run(["pkill", "-TERM", "-x", name])
            print(f"guide held {HOLD}s: closed {name}", flush=True)


def watch(dev):
    down_since = None
    while True:
        r, _, _ = select.select([dev.fd], [], [], 0.2)
        if r:
            for ev in dev.read():
                if ev.type == ecodes.EV_KEY and ev.code == ecodes.BTN_MODE:
                    down_since = time.monotonic() if ev.value == 1 else None
        if down_since is not None and time.monotonic() - down_since >= HOLD:
            close_targets()
            down_since = None


def main():
    print(f"waiting for '{PAD_NAME}'", flush=True)
    while True:
        dev = find_pad()
        if dev is None:
            time.sleep(2)
            continue
        print(f"pad found: {dev.path}", flush=True)
        try:
            watch(dev)
        except OSError:                       # client disconnected, device vanished
            print("pad gone", flush=True)


if __name__ == "__main__":
    main()
