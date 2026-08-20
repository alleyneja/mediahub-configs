#!/usr/bin/env python3
"""A virtual Xbox pad on /dev/uinput, for testing emulator mappings headlessly.

    ./arcade-vpad.py a b start                 # tap buttons
    ./arcade-vpad.py --hold 1.5 left           # hold the left stick left
    ./arcade-vpad.py lstick:-1,0 a lstick:0,0  # explicit stick positions

**It must present the same identity as the pad the mapping was written against.**
Dolphin binds by device string -- `SDL/0/Xbox One S Controller` -- and on
2026-08-17 a synthetic pad with a different name produced a false "verified"
result. So this reports name "Xbox One S Controller" and Microsoft's
045e:0b12, and SDL enumerates it exactly like the real thing.

Only run it when the real pad is absent (no Moonlight session), or the indexes
shift and you are testing the wrong device.

`jay` is in the `input` group, so no sudo is needed.
"""
import fcntl, os, struct, sys, time

UI_DEV_CREATE, UI_DEV_DESTROY = 0x5501, 0x5502
UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_ABSBIT = 0x40045564, 0x40045565, 0x40045567
EV_SYN, EV_KEY, EV_ABS, SYN_REPORT = 0x00, 0x01, 0x03, 0x00

BTN = {  # xbox face/shoulder/etc -> evdev code
    'a': 0x130, 'b': 0x131, 'x': 0x133, 'y': 0x134,
    'lb': 0x136, 'rb': 0x137,
    'back': 0x13a, 'start': 0x13b, 'guide': 0x13c,
    'lthumb': 0x13d, 'rthumb': 0x13e,
}
ABS = {  # name -> (code, min, max)
    'lx': (0x00, -32768, 32767), 'ly': (0x01, -32768, 32767),
    'lt': (0x02, 0, 255),
    'rx': (0x03, -32768, 32767), 'ry': (0x04, -32768, 32767),
    'rt': (0x05, 0, 255),
    'hx': (0x10, -1, 1), 'hy': (0x11, -1, 1),
}
# convenience directions -> (axis, value)
DIRS = {
    'left': ('lx', -32000), 'right': ('lx', 32000),
    'up': ('ly', -32000), 'down': ('ly', 32000),
    'dpleft': ('hx', -1), 'dpright': ('hx', 1),
    'dpup': ('hy', -1), 'dpdown': ('hy', 1),
}


def emit(fd, etype, code, value):
    os.write(fd, struct.pack('llHHi', 0, 0, etype, code, value))


def sync(fd):
    emit(fd, EV_SYN, SYN_REPORT, 0)


def create():
    fd = os.open('/dev/uinput', os.O_WRONLY | os.O_NONBLOCK)
    fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
    fcntl.ioctl(fd, UI_SET_EVBIT, EV_ABS)
    for code in BTN.values():
        fcntl.ioctl(fd, UI_SET_KEYBIT, code)
    for code, _, _ in ABS.values():
        fcntl.ioctl(fd, UI_SET_ABSBIT, code)

    absmin = [0] * 64
    absmax = [0] * 64
    for code, lo, hi in ABS.values():
        absmin[code], absmax[code] = lo, hi

    payload = struct.pack('80sHHHHi', b'Xbox One S Controller',
                          0x03, 0x045e, 0x0b12, 0x0001, 0)
    payload += struct.pack('64i', *absmax)      # absmax
    payload += struct.pack('64i', *absmin)      # absmin
    payload += struct.pack('64i', *([0] * 64))  # absfuzz
    payload += struct.pack('64i', *([0] * 64))  # absflat
    os.write(fd, payload)
    fcntl.ioctl(fd, UI_DEV_CREATE)
    time.sleep(1.2)   # let udev/SDL notice it
    for name in ('lx', 'ly', 'rx', 'ry'):       # centre the sticks
        emit(fd, EV_ABS, ABS[name][0], 0)
    sync(fd)
    return fd


def main(argv):
    hold = 0.12
    if argv and argv[0] == '--hold':
        hold, argv = float(argv[1]), argv[2:]
    if not argv:
        sys.exit('usage: arcade-vpad.py [--hold S] TOKEN...\n'
                 'buttons: %s\ndirections: %s\nexplicit: lstick:X,Y rstick:X,Y '
                 '(-1..1)\nother: wait:SECONDS'
                 % (' '.join(BTN), ' '.join(DIRS)))

    fd = create()
    try:
        for tok in argv:
            tok = tok.lower()
            if tok.startswith('wait:'):
                time.sleep(float(tok[5:])); print('waited', tok[5:]); continue

            if tok.startswith(('lstick:', 'rstick:')):
                pre = 'l' if tok[0] == 'l' else 'r'
                xs, ys = tok.split(':', 1)[1].split(',')
                for axis, frac in ((pre + 'x', float(xs)), (pre + 'y', float(ys))):
                    code, lo, hi = ABS[axis]
                    emit(fd, EV_ABS, code, int(frac * (hi if frac >= 0 else -lo)))
                sync(fd); print('stick', tok); time.sleep(hold); continue

            if tok in DIRS:
                axis, val = DIRS[tok]
                emit(fd, EV_ABS, ABS[axis][0], val); sync(fd)
                time.sleep(hold)
                emit(fd, EV_ABS, ABS[axis][0], 0); sync(fd)
                print('direction', tok); time.sleep(0.15); continue

            if tok in BTN:
                emit(fd, EV_KEY, BTN[tok], 1); sync(fd)
                time.sleep(hold)
                emit(fd, EV_KEY, BTN[tok], 0); sync(fd)
                print('button', tok); time.sleep(0.15); continue

            print('unknown token:', tok, file=sys.stderr)
        time.sleep(0.4)
    finally:
        fcntl.ioctl(fd, UI_DEV_DESTROY)
        os.close(fd)


if __name__ == '__main__':
    main(sys.argv[1:])
