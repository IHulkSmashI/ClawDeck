#!/usr/bin/env python3
"""Keeps the gyro bias correction of the HID-BPF program up to date.

The BPF program (MSI__Claw-ISH-IMU.bpf.o, v2) subtracts a per-axis offset from every gyro report.
This service:
  * finds the program's config map in the kernel (by its magic number 'GOFF'),
  * applies the last saved offsets right away (state: /var/lib/ish-gyro-bias/offsets.json),
  * watches the (already corrected) gyro stream; whenever the device is still it nudges the offsets so
    the resting reading converges to 0, and saves them.
It only reads the sensor and writes the offsets into the BPF config; it sends NOTHING to the hub or controller.

Usage:  sudo python3 ish_gyro_bias.py            (run as a service)
        sudo python3 ish_gyro_bias.py --show     (print current offsets, then exit)
        sudo python3 ish_gyro_bias.py --set X Y Z   (set offsets in deg/s, then exit)
Needs ish_sensor_probe.py in the same folder (installed next to it)."""
import argparse, ctypes, json, os, select, statistics as st, struct, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MAGIC = 0x474F4646          # 'GOFF'
SCALE = 1e-5                # raw units -> deg/s
STATE = '/var/lib/ish-gyro-bias/offsets.json'
CLAMP = 200000              # +-2.0 deg/s maximum offset
WINDOW = 2.0                # seconds per stillness window
ALPHA = 0.25                # fraction of the residual removed per still window
STILL_STD = 80000           # < 0.8 deg/s noise
STILL_MEAN = 150000         # residual < 1.5 deg/s (otherwise the device is being moved)
CAND = (0x0457, 0x0458, 0x0459)

# ---- minimal bpf() syscall wrapper ------------------------------------------------------------
libc = ctypes.CDLL(None, use_errno=True)
SYS_BPF = {'x86_64': 321, 'aarch64': 280}[os.uname().machine]
BPF_MAP_LOOKUP_ELEM, BPF_MAP_UPDATE_ELEM, BPF_MAP_GET_NEXT_ID, BPF_MAP_GET_FD_BY_ID, BPF_OBJ_GET_INFO_BY_FD = 1, 2, 12, 14, 15

def bpf(cmd, attr):
    buf = ctypes.create_string_buffer(bytes(attr), 128)
    r = libc.syscall(SYS_BPF, cmd, buf, 128)
    if r < 0:
        e = ctypes.get_errno(); raise OSError(e, os.strerror(e))
    return r, buf

def map_fds():
    cur = 0
    while True:
        try: _, b = bpf(BPF_MAP_GET_NEXT_ID, struct.pack('III', cur, 0, 0))
        except OSError: return
        cur = struct.unpack_from('I', b, 4)[0]
        try: fd, _ = bpf(BPF_MAP_GET_FD_BY_ID, struct.pack('III', cur, 0, 0))
        except OSError: continue
        yield fd

def map_info(fd):
    info = ctypes.create_string_buffer(128)
    attr = struct.pack('IIQ', fd, 128, ctypes.addressof(info))
    bpf(BPF_OBJ_GET_INFO_BY_FD, attr)
    typ, mid, ksz, vsz, mx = struct.unpack_from('5I', info.raw, 0)
    return typ, ksz, vsz, mx

def lookup(fd, vsz):
    key = ctypes.c_uint32(0); val = ctypes.create_string_buffer(vsz)
    bpf(BPF_MAP_LOOKUP_ELEM, struct.pack('IIQQQ', fd, 0, ctypes.addressof(key), ctypes.addressof(val), 0))
    return val.raw

def update(fd, value):
    key = ctypes.c_uint32(0); val = ctypes.create_string_buffer(value, len(value))
    bpf(BPF_MAP_UPDATE_ELEM, struct.pack('IIQQQ', fd, 0, ctypes.addressof(key), ctypes.addressof(val), 0))

def find_cfg_map():
    """Return (fd, value_size) of the BPF program's config map, or None."""
    found = None
    for fd in map_fds():
        try:
            typ, ksz, vsz, mx = map_info(fd)
            if typ == 2 and ksz == 4 and mx == 1 and vsz >= 20:       # BPF_MAP_TYPE_ARRAY, 1 entry
                if struct.unpack_from('I', lookup(fd, vsz), 0)[0] == MAGIC:
                    found = (fd, vsz); continue
        except OSError:
            pass
        os.close(fd)
    return found

def read_off(fd, vsz):
    v = lookup(fd, vsz)
    return list(struct.unpack_from('3i', v, 4))

def write_off(fd, vsz, off):
    v = bytearray(lookup(fd, vsz))
    struct.pack_into('3i', v, 4, *[int(round(max(-CLAMP, min(CLAMP, o)))) for o in off])
    update(fd, bytes(v))

# ---- state ------------------------------------------------------------------------------------
def load_state():
    try: return [int(x) for x in json.load(open(STATE))['off']]
    except Exception: return [0, 0, 0]

def save_state(off):
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        json.dump({'off': [int(o) for o in off], 'deg_s': [round(o * SCALE, 3) for o in off]}, open(STATE + '.tmp', 'w'))
        os.replace(STATE + '.tmp', STATE)
    except OSError as e:
        print('could not save state:', e, flush=True)

def fmt(off): return '  '.join(f'{"XYZ"[i]} {off[i] * SCALE:+.3f}' for i in range(3)) + ' deg/s'

# ---- gyro stream ------------------------------------------------------------------------------
def open_gyro():
    from ish_sensor_probe import parse_rdesc, find, getbits, find_hidraw
    dev, rpath = find_hidraw()
    if not dev: return None
    layout, sizes = parse_rdesc(open(rpath, 'rb').read())
    fields = [find(layout[('in', 2)], CAND[k]) or find(layout[('in', 2)], 0x0544 + k) for k in range(3)]
    need = sizes[('in', 2)] // 8 + 1
    fd = os.open(dev, os.O_RDONLY | os.O_NONBLOCK)
    def read_window(secs):
        out, end = [], time.monotonic() + secs
        while time.monotonic() < end:
            if not select.select([fd], [], [], 0.3)[0]: continue
            try: d = os.read(fd, 256)
            except BlockingIOError: continue
            if d and d[0] == 2:
                p = bytes(d) + bytes(max(0, need - len(d)))
                out.append([getbits(p, f, signed=True) for f in fields])
        return out
    return read_window

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--show', action='store_true')
    ap.add_argument('--set', nargs=3, type=float, metavar=('X', 'Y', 'Z'))
    a = ap.parse_args()

    if a.show or a.set:
        m = find_cfg_map() or sys.exit('BPF config map not found - is the v2 BPF program loaded?')
        if a.set:
            off = [round(v / SCALE) for v in a.set]; write_off(*m, off); save_state(off)
        print('offsets:', fmt(read_off(*m))); return

    off = load_state(); m = None; read_window = None; last_log = 0; last_saved = list(off)
    print('starting; saved offsets:', fmt(off), flush=True)
    while True:
        try:
            if m is None:
                m = find_cfg_map()
                if not m: time.sleep(5); continue
                write_off(*m, off); print('config map found, offsets applied:', fmt(off), flush=True)
            if read_window is None:
                read_window = open_gyro()
                if read_window is None: time.sleep(5); continue
            cur = read_off(*m)                                   # BPF reloaded -> map is new and zeroed
            if cur != [int(round(max(-CLAMP, min(CLAMP, o)))) for o in off]:
                write_off(*m, off); print('offsets re-applied:', fmt(off), flush=True)
            w = read_window(WINDOW)
            if len(w) < 100: continue                            # gyro not streaming
            means = [st.fmean(s[k] for s in w) for k in range(3)]   # already corrected by the BPF program
            sds = [st.pstdev([s[k] for s in w]) for k in range(3)]
            if all(abs(means[k]) < STILL_MEAN and sds[k] < STILL_STD for k in range(3)):
                off = [max(-CLAMP, min(CLAMP, off[k] + ALPHA * means[k])) for k in range(3)]
                write_off(*m, off)
                if time.monotonic() - last_log > 30:
                    print('still: residual', fmt([round(x) for x in means]), '-> offsets', fmt(off), flush=True); last_log = time.monotonic()
                if max(abs(off[k] - last_saved[k]) for k in range(3)) > 1000:     # > 0.01 deg/s
                    save_state([round(o) for o in off]); last_saved = list(off)
        except OSError as e:
            print('error:', e, '- retrying', flush=True); m = None; read_window = None; time.sleep(3)

if __name__ == '__main__':
    main()
