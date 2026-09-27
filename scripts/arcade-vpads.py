#!/usr/bin/env python3
"""N virtual Xbox pads on /dev/uinput, driven per player, for testing 2-4 player mappings.

    ./arcade-vpads.py 4 &                      # create 4 pads, listen on the FIFO
    echo "2 a" > /tmp/arcade-vpads.fifo        # player 2 taps A
    echo "3 hold 1.5 right" > /tmp/arcade-vpads.fifo
    echo "quit" > /tmp/arcade-vpads.fifo       # destroy the pads

Commands: `<player> <token>` or `<player> hold <seconds> <token>`, tokens as in
arcade-vpad.py (buttons, directions, lstick:X,Y). `quit` removes the pads.

**Identity matters** (see arcade-vpad.py). `--identity sunshine` (default) copies
the pad Sunshine creates for a Moonlight client: 045e:02ea v0408, which SDL names
"Xbox One S Controller" and Ryujinx ids as `N-00000003-045e-0000-ea02-000008040000`.
`--identity wired` copies a Series controller plugged into r9 over USB under xone
(name "Microsoft Xbox Controller", 045e:0b12 v0517).

Pads are created in order, so pad 1 gets the lowest SDL index. Only run it with no
Moonlight session connected, or the indexes shift and you test the wrong device.
"""
import fcntl, os, struct, sys, time

UI_DEV_CREATE, UI_DEV_DESTROY = 0x5501, 0x5502
UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_ABSBIT = 0x40045564, 0x40045565, 0x40045567
EV_SYN, EV_KEY, EV_ABS, SYN_REPORT = 0x00, 0x01, 0x03, 0x00
FIFO = os.environ.get('ARCADE_VPADS_FIFO', '/tmp/arcade-vpads.fifo')  # one per running instance

IDENTITIES = {
    'sunshine': (b'Sunshine X-Box One (virtual) pad', 0x03, 0x045e, 0x02ea, 0x0408),
    'wired': (b'Microsoft Xbox Controller', 0x06, 0x045e, 0x0b12, 0x0517),
}
BTN = {
    'a': 0x130, 'b': 0x131, 'x': 0x133, 'y': 0x134,
    'lb': 0x136, 'rb': 0x137,
    'back': 0x13a, 'start': 0x13b, 'guide': 0x13c,
    'lthumb': 0x13d, 'rthumb': 0x13e,
}
ABS = {
    'lx': (0x00, -32768, 32767), 'ly': (0x01, -32768, 32767),
    'lt': (0x02, 0, 1023),
    'rx': (0x03, -32768, 32767), 'ry': (0x04, -32768, 32767),
    'rt': (0x05, 0, 1023),
    'hx': (0x10, -1, 1), 'hy': (0x11, -1, 1),
}
DIRS = {
    'left': ('lx', -32000), 'right': ('lx', 32000),
    'up': ('ly', -32000), 'down': ('ly', 32000),
    'dpleft': ('hx', -1), 'dpright': ('hx', 1),
    'dpup': ('hy', -1), 'dpdown': ('hy', 1),
    'lt': ('lt', 1023), 'rt': ('rt', 1023),
}


def emit(fd, etype, code, value):
    os.write(fd, struct.pack('llHHi', 0, 0, etype, code, value))
    if etype != EV_SYN:
        os.write(fd, struct.pack('llHHi', 0, 0, EV_SYN, SYN_REPORT, 0))


def create(identity):
    name, bus, vid, pid, ver = IDENTITIES[identity]
    fd = os.open('/dev/uinput', os.O_WRONLY | os.O_NONBLOCK)
    fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
    fcntl.ioctl(fd, UI_SET_EVBIT, EV_ABS)
    for code in BTN.values():
        fcntl.ioctl(fd, UI_SET_KEYBIT, code)
    for code, _, _ in ABS.values():
        fcntl.ioctl(fd, UI_SET_ABSBIT, code)
    absmin, absmax = [0] * 64, [0] * 64
    for code, lo, hi in ABS.values():
        absmin[code], absmax[code] = lo, hi
    payload = struct.pack('80sHHHHi', name, bus, vid, pid, ver, 0)
    payload += struct.pack('64i', *absmax) + struct.pack('64i', *absmin)
    payload += struct.pack('64i', *([0] * 64)) * 2
    os.write(fd, payload)
    fcntl.ioctl(fd, UI_DEV_CREATE)
    return fd


def act(fd, tok, hold):
    if tok.startswith(('lstick:', 'rstick:')):
        pre = tok[0]
        xs, ys = tok.split(':', 1)[1].split(',')
        for axis, frac in ((pre + 'x', float(xs)), (pre + 'y', float(ys))):
            code, lo, hi = ABS[axis]
            emit(fd, EV_ABS, code, int(frac * (hi if frac >= 0 else -lo)))
        return True
    if tok in DIRS:
        axis, val = DIRS[tok]
        emit(fd, EV_ABS, ABS[axis][0], val); time.sleep(hold)
        emit(fd, EV_ABS, ABS[axis][0], 0)
        return True
    if tok in BTN:
        emit(fd, EV_KEY, BTN[tok], 1); time.sleep(hold)
        emit(fd, EV_KEY, BTN[tok], 0)
        return True
    return False


def main(argv):
    identity = 'sunshine'
    if argv[:1] == ['--identity']:
        identity, argv = argv[1], argv[2:]
    n = int(argv[0]) if argv else 4
    pads = []
    for _ in range(n):
        pads.append(create(identity))
        time.sleep(0.6)   # distinct creation order -> stable SDL indexes
    for fd in pads:
        for axis in ('lx', 'ly', 'rx', 'ry'):
            emit(fd, EV_ABS, ABS[axis][0], 0)
    if os.path.exists(FIFO):
        os.unlink(FIFO)
    os.mkfifo(FIFO)
    print(f'{n} {identity} pads up; commands on {FIFO}', flush=True)
    try:
        while True:
            with open(FIFO) as f:
                for line in f:
                    parts = line.split()
                    if not parts:
                        continue
                    if parts[0] == 'quit':
                        return
                    player, rest = int(parts[0]), parts[1:]
                    hold = 0.12
                    if rest[:1] == ['hold']:
                        hold, rest = float(rest[1]), rest[2:]
                    for tok in rest:
                        ok = act(pads[player - 1], tok.lower(), hold)
                        print(f'p{player} {tok}' + ('' if ok else ' ?unknown'), flush=True)
                        time.sleep(0.1)
    finally:
        for fd in pads:
            fcntl.ioctl(fd, UI_DEV_DESTROY)
            os.close(fd)
        os.unlink(FIFO)


if __name__ == '__main__':
    main(sys.argv[1:])
