#!/usr/bin/env python3
"""
Read the raw IMU of the MSI Claw 8 EX AI+ from the Intel ISH sensor hub on Linux.

The hub (8087:0AC2) exposes standard HID-sensor feature/input reports, but the
IMU is in *custom* sensors (usage 0x2000e1), which no stock Linux driver binds.
This script talks to the hub through /dev/hidraw using only the standard HID
sensor "reporting state / power state / report interval" feature reports.
It sends NO vendor commands.

Usage (needs root for /dev/hidraw*):
    sudo python3 ish_sensor_probe.py --dry-run       # parse descriptor only
    sudo python3 ish_sensor_probe.py                 # enable + print live data
    sudo python3 ish_sensor_probe.py --seconds 30 --interval 10
"""
import argparse, fcntl, glob, os, select, struct, sys, time

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

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--rdesc', help='descriptor file (default: read from sysfs)')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--seconds', type=float, default=20)
    ap.add_argument('--interval', type=int, default=10, help='report interval ms')
    a = ap.parse_args()

    dev, rpath = find_hidraw()
    rpath = a.rdesc or rpath
    if not rpath:
        sys.exit('ISH sensor hub (8087:0AC2) hidraw node not found')
    layout, sizes = parse_rdesc(open(rpath, 'rb').read())
    ids = sorted({i for (k, i) in layout if k == 'in'})
    print(f'hidraw: {dev}   sensor report ids: {ids}')

    # field usages (page 0x20): 0x0840 reporting state sel, 0x0850 power state sel,
    # 0x030e report interval, 0x0544..0x0546 custom data 1..3
    for rid in ids:
        feat = layout.get(('feat', rid), [])
        inp = layout.get(('in', rid), [])
        d = [f"{u:#06x}" for f in inp for u in f['usages'][:1]]
        print(f'  id {rid}: feature={sizes[("feat", rid)]//8+1}B  input={sizes[("in", rid)]//8+1}B  in-fields={d}')
    if a.dry_run:
        return
    if not dev:
        sys.exit('no device')

    fd = os.open(dev, os.O_RDWR)
    for rid in ids:
        feat = layout[('feat', rid)]
        n = sizes[('feat', rid)] // 8 + 1
        buf = bytearray(n); buf[0] = rid
        try:
            fcntl.ioctl(fd, HIDIOCGFEATURE(n), buf)
        except OSError as e:
            print(f'  id {rid}: get_feature failed: {e}')
        buf = bytearray(buf)
        rs, ps, ri = find(feat, 0x0841), find(feat, 0x0851), find(feat, 0x030E)
        if rs: buf = setbits(buf, rs, 2)      # 0x841 = report all events
        if ps: buf = setbits(buf, ps, 2)      # 0x851 = D0 full power
        if ri: buf = setbits(buf, ri, a.interval)
        buf[0] = rid
        try:
            fcntl.ioctl(fd, HIDIOCSFEATURE(n), bytes(buf))
            print(f'  id {rid}: enabled (interval {a.interval} ms)')
        except OSError as e:
            print(f'  id {rid}: set_feature failed: {e}')

    print('\nReading... move/rotate the device. Cols: report id, X Y Z (int32 raw)\n')
    end = time.time() + a.seconds
    last = {}
    cnt = {}
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.5)
        if not r: continue
        data = os.read(fd, 256)
        rid = data[0]
        cnt[rid] = cnt.get(rid, 0) + 1
        inp = layout.get(('in', rid))
        if not inp: continue
        pad = bytes(data) + bytes(max(0, sizes[('in', rid)] // 8 + 1 - len(data)))
        xyz = []
        for u in (0x0544, 0x0545, 0x0546):
            f = find(inp, u)
            if f: xyz.append(getbits(pad, f, signed=True))
        now = time.time()
        if now - last.get(rid, 0) > 0.1:
            last[rid] = now
            print(f'id{rid} n={cnt[rid]:5d} {xyz}  raw={data.hex()[:60]}')
    print('\nreport counts:', cnt)

if __name__ == '__main__':
    main()
