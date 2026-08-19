#!/usr/bin/env python3
"""Inject keystrokes into the headless :1 session via /dev/uinput.

There is no xdotool on this host and X on :1 has no real input devices, so the
only way to drive ES-DE without a human at the projector is a synthetic kernel
keyboard. X picks it up through udev hotplug (AutoAddDevices is on by default).

    ./arcade-keys.py escape down down enter
    ./arcade-keys.py --hold 0.6 right right

`jay` is in the `input` group, so no sudo is needed. The device is destroyed on
exit; nothing persists.
"""
import fcntl, os, struct, sys, time

UI_DEV_CREATE, UI_DEV_DESTROY = 0x5501, 0x5502
UI_SET_EVBIT, UI_SET_KEYBIT = 0x40045564, 0x40045565
EV_SYN, EV_KEY, SYN_REPORT = 0x00, 0x01, 0x00

KEYS = {
    'esc': 1, 'escape': 1, 'backspace': 14, 'tab': 15, 'enter': 28, 'space': 57,
    'up': 103, 'down': 108, 'left': 105, 'right': 106,
    'pageup': 104, 'pagedown': 109, 'home': 102, 'end': 107,
    'insert': 110, 'delete': 111, 'f1': 59, 'f4': 62,
    'a': 30, 'b': 48, 'x': 45, 'y': 21, 's': 31,
}


def emit(fd, etype, code, value):
    os.write(fd, struct.pack('llHHi', 0, 0, etype, code, value))


def main(argv):
    hold = 0.05
    if argv and argv[0] == '--hold':
        hold = float(argv[1])
        argv = argv[2:]
    names = [a.lower() for a in argv]
    if not names:
        sys.exit('usage: arcade-keys.py [--hold SECONDS] KEY [KEY ...]\nkeys: '
                 + ' '.join(sorted(KEYS)))
    unknown = [n for n in names if n not in KEYS]
    if unknown:
        sys.exit('unknown key(s): %s' % ', '.join(unknown))

    fd = os.open('/dev/uinput', os.O_WRONLY | os.O_NONBLOCK)
    try:
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
        for code in set(KEYS.values()):
            fcntl.ioctl(fd, UI_SET_KEYBIT, code)

        # legacy uinput_user_dev: name[80] + input_id + ff_effects_max + 4x64 abs arrays
        os.write(fd, struct.pack('80sHHHHi', b'arcade-virtual-keyboard',
                                 0x03, 0x1209, 0x0001, 0x0001, 0) + b'\0' * (4 * 64 * 4))
        fcntl.ioctl(fd, UI_DEV_CREATE)
        time.sleep(1.0)          # let udev/X notice the new device

        for name in names:
            code = KEYS[name]
            emit(fd, EV_KEY, code, 1); emit(fd, EV_SYN, SYN_REPORT, 0)
            time.sleep(hold)
            emit(fd, EV_KEY, code, 0); emit(fd, EV_SYN, SYN_REPORT, 0)
            time.sleep(0.25)
            print('sent %s' % name)
        time.sleep(0.4)
    finally:
        fcntl.ioctl(fd, UI_DEV_DESTROY)
        os.close(fd)


if __name__ == '__main__':
    main(sys.argv[1:])
