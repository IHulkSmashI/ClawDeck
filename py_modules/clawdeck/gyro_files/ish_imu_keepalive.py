#!/usr/bin/env python3
"""
ISH IMU keep-alive for the MSI Claw 8 EX AI+ (Linux), v4.

The kernel's gyro_3d / accel_3d IIO drivers switch the sensors on, but on this
hub the power state / report interval end up wrong.  This service watches the
IIO buffers: when a sensor's buffer gets enabled (e.g. by InputPlumber) it waits
a moment and writes a clean standard HID-sensor config (reporting=all, power=D0,
interval=10 ms, change sensitivity=0) over /dev/hidraw.  A cheap watchdog then
counts the sensor's input reports every few seconds and rewrites the config if
the data stops.  It never reads feature reports back (this hub returns corrupted
data for them while streaming) and never sends vendor commands.  Needs root.
"""
import fcntl, glob, os, select, sys, time

# ---- HID descriptor parsing -------------------------------------------------
def parse_rdesc(d):
    """Return {(kind, report_id): [field,...]} with bit offsets (after the ID byte)."""
    g = dict(page=0, lmin=0, lmax=0, size=0, count=0, id=0)
    gstack, loc = [], dict(usages=[], umin=None, umax=None)
    offs, out = {}, {}
    i = 0
    def val(v, signed=False):
        return int.from_bytes(v, 'little', signed=signed) if v else 0
    while i < len(d):
        b = d[i]
        if b == 0xFE:
            i += 3 + d[i + 1]; continue
        sz = [0, 1, 2, 4][b & 3]; t = (b >> 2) & 3; tag = b >> 4
        v = d[i + 1:i + 1 + sz]; i += 1 + sz
        if t == 1:
            if tag == 0: g['page'] = val(v)
            elif tag == 1: g['lmin'] = val(v, True)
            elif tag == 2: g['lmax'] = val(v, g['lmin'] < 0)
            elif tag == 7: g['size'] = val(v)
            elif tag == 8: g['id'] = val(v)
            elif tag == 9: g['count'] = val(v)
            elif tag == 10: gstack.append(dict(g))
            elif tag == 11: g = gstack.pop()
        elif t == 2:
            if tag == 0: loc['usages'].append(val(v) & 0xFFFF)
            elif tag == 1: loc['umin'] = val(v)
            elif tag == 2: loc['umax'] = val(v)
        elif t == 0:
            if tag in (8, 9, 11):
                kind = {8: 'in', 9: 'out', 11: 'feat'}[tag]
                key = (kind, g['id'])
                pos = offs.get(key, 0)
                us = list(loc['usages'])
                if loc['umin'] is not None:
                    us += list(range(loc['umin'], loc['umax'] + 1))
                if not (val(v) & 1):  # not constant
                    out.setdefault(key, []).append(dict(
                        usages=us, off=pos, size=g['size'], count=g['count'],
                        lmin=g['lmin']))
                offs[key] = pos + g['size'] * g['count']
            if tag in (8, 9, 10, 11, 12):
                loc = dict(usages=[], umin=None, umax=None)
    return out, offs

def find(fields, usage):
    for f in fields:
        if usage in f['usages']:
            return f
    return None

def getbits(buf, f, idx=0, signed=False):
    """buf includes the report-ID byte."""
    start = 8 + f['off'] + idx * f['size']
    n = int.from_bytes(buf, 'little')
    x = (n >> start) & ((1 << f['size']) - 1)
    if signed and x >= 1 << (f['size'] - 1):
        x -= 1 << f['size']
    return x

def setbits(buf, f, value, idx=0):
    start = 8 + f['off'] + idx * f['size']
    n = int.from_bytes(buf, 'little')
    mask = ((1 << f['size']) - 1) << start
    n = (n & ~mask) | ((value & ((1 << f['size']) - 1)) << start)
    return bytearray(n.to_bytes(len(buf), 'little'))

# ---- hidraw ioctls ----------------------------------------------------------
def _IOC(d, t, nr, sz): return (d << 30) | (sz << 16) | (ord(t) << 8) | nr
def HIDIOCSFEATURE(n): return _IOC(3, 'H', 0x06, n)
def HIDIOCGFEATURE(n): return _IOC(3, 'H', 0x07, n)

def find_hidraw():
    for p in sorted(glob.glob('/sys/class/hidraw/hidraw*')):
        try:
            ue = open(p + '/device/uevent').read().upper()
        except OSError:
            continue
        if '8087' in ue and '0AC2' in ue:
            return '/dev/' + os.path.basename(p), p + '/device/report_descriptor'
    return None, None


SENSORS = {'accel_3d': 1, 'gyro_3d': 2}   # IIO name -> HID report id
INTERVAL_MS = 10                          # 100 Hz
WATCH_EVERY = 5.0                         # seconds between data-flow checks
WATCH_WINDOW = 0.3                        # seconds spent counting reports per check

def iio_enabled(name):
    for d in glob.glob('/sys/bus/iio/devices/iio:device*'):
        try:
            if open(d + '/name').read().strip() == name:
                return open(d + '/buffer/enable').read().strip() == '1'
        except OSError:
            pass
    return False

def build_report(layout, sizes, rid):
    """A clean feature report built from scratch. GET_FEATURE on this hub returns
    corrupted data while it is streaming, so we never read-modify-write."""
    feat = layout[('feat', rid)]
    n = sizes[('feat', rid)] // 8 + 1
    buf = bytearray(n)
    buf = setbits(buf, find(feat, 0x0841), 2)             # report all events
    buf = setbits(buf, find(feat, 0x0851), 2)             # D0 full power
    buf = setbits(buf, find(feat, 0x030E), INTERVAL_MS)   # report interval
    f = find(feat, 0x1544)
    if f: buf = setbits(buf, f, 0)                        # change sensitivity: report everything
    buf[0] = rid
    return bytes(buf)

def write_clean(fd, layout, sizes, rid):
    fcntl.ioctl(fd, HIDIOCSFEATURE(len(build_report(layout, sizes, rid))),
                build_report(layout, sizes, rid))

def count_reports(fd, window):
    """Count input reports per id seen on hidraw during `window` seconds (reliable)."""
    counts = {}
    end = time.time() + window
    while True:
        left = end - time.time()
        if left <= 0:
            break
        r, _, _ = select.select([fd], [], [], left)
        if not r:
            break
        try:
            d = os.read(fd, 256)
        except BlockingIOError:
            continue
        if d:
            counts[d[0]] = counts.get(d[0], 0) + 1
    return counts

def main():
    prev = {name: False for name in SENSORS}
    last_watch = 0.0
    while True:
        dev, rpath = find_hidraw()
        if not dev:
            time.sleep(5); continue
        try:
            layout, sizes = parse_rdesc(open(rpath, 'rb').read())
            fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
        except OSError:
            time.sleep(5); continue
        try:
            while True:
                now = time.time()
                for name, rid in SENSORS.items():
                    en = iio_enabled(name)
                    if en and not prev[name]:
                        time.sleep(1.0)           # let the kernel finish its own setup
                        write_clean(fd, layout, sizes, rid)
                        print(f'{name}: wrote clean config (buffer enabled)', flush=True)
                    prev[name] = en
                if now - last_watch >= WATCH_EVERY:
                    last_watch = now
                    active = [(n, r) for n, r in SENSORS.items() if iio_enabled(n)]
                    if active:
                        seen = count_reports(fd, WATCH_WINDOW)
                        for name, rid in active:
                            if seen.get(rid, 0) == 0:
                                write_clean(fd, layout, sizes, rid)
                                print(f'{name}: no data flowing, rewrote clean config', flush=True)
                time.sleep(0.5)
        except OSError as e:
            print('hub error, reconnecting:', e, flush=True)
            try: os.close(fd)
            except OSError: pass
            time.sleep(3)

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--selftest':
        rd = open(sys.argv[2], 'rb').read()
        layout, sizes = parse_rdesc(rd)
        for rid in (1, 2):
            b = build_report(layout, sizes, rid)
            feat = layout[('feat', rid)]
            got = {hex(u): getbits(b, find(feat, u)) for u in (0x0841, 0x0851, 0x030E, 0x1544, 0x0304, 0x031A)}
            nz = [i for i, x in enumerate(b) if x]
            print(rid, len(b), 'bytes; fields', got, '; nonzero byte offsets', nz)
        sys.exit(0)
    main()
