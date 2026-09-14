"""
listen.py -- settle whether diverting 0xC4 yields GENUINE notifications.

Every HID++ packet carries a software-id nibble in byte 3:
    sw == 0xA  -> echo of a request WE sent        (ignore)
    sw == 0x0  -> genuine unsolicited notification <- what we need

Friday's "0 packets" results (pure.py / alldivert.py / mousecol.py) were
collected AFTER the user had left the desk. Those tests needed a physical
button press, so they are INCONCLUSIVE. This re-runs the decisive test.

Logs to listen.log so the result can be read back later.

Usage:
    python listen.py                 240s, stop early after 5 real events
    python listen.py --analytics     also enable analytics events
    python listen.py --seconds 60 --need 3
"""
from __future__ import annotations

import argparse
import ctypes
import os
import queue
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hidpp            # noqa: E402
import mxvoice as mv    # noqa: E402

LOG = os.path.join(HERE, "listen.log")
_logf = open(LOG, "w", encoding="utf-8", buffering=1)


def log(msg: str = "") -> None:
    print(msg, flush=True)
    _logf.write(msg + "\n")


k = ctypes.WinDLL("kernel32", use_last_error=True)
k.ReadFile.restype = ctypes.c_bool
k.ReadFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                       ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=240)
    ap.add_argument("--need", type=int, default=5)
    ap.add_argument("--analytics", action="store_true")
    args = ap.parse_args(argv)

    h, path, caps, hr, hq = mv.find_mouse_handle()
    log(f"device : ...{path[-60:]}")
    log(f"caps   : usage {caps.UsagePage:#06x} in={caps.InputReportByteLength}")

    dev = mv.HidppDevice(h, 0, hr, None, hq)
    dev.feat = dev.get_feature_index(mv.FEAT_REPROG_CONTROLS_V4)
    log(f"0x1b04 at feature index {dev.feat}")

    # Blocking reader on hr. hr and hq are separate handles on the SAME
    # collection, so replies are broadcast to both -- we simply classify.
    q: "queue.Queue[bytes]" = queue.Queue()

    def reader():
        while True:
            buf = ctypes.create_string_buffer(64)
            n = ctypes.c_uint32(0)
            ok = k.ReadFile(ctypes.c_void_p(hr), buf, 64,
                            ctypes.byref(n), None)
            if ok and n.value:
                q.put(bytes(buf.raw[:n.value]))

    threading.Thread(target=reader, daemon=True).start()
    time.sleep(0.6)
    while not q.empty():
        q.get_nowait()

    kw = {"diverted": True}
    if args.analytics:
        kw["analytics"] = True
    dev.set_cid_reporting(0xC4, **kw)
    st = dev.get_cid_reporting(0xC4)
    log(f"diverted = {st['diverted']}   analytics = {st['analytics']}")
    time.sleep(0.4)
    while not q.empty():
        q.get_nowait()

    log()
    log("=" * 64)
    log("   >>>  PRESS THE WHEEL-REAR BUTTON NOW  <<<")
    log("   The button BEHIND the scroll wheel. Press it 5 times,")
    log("   slowly, about 2 seconds apart.")
    log("=" * 64)
    log()

    real = 0
    echoes = 0
    t0 = time.time()
    next_poll = t0 + 3.0
    last_note = t0

    try:
        while time.time() - t0 < args.seconds and real < args.need:
            while not q.empty():
                d = q.get_nowait()
                for i in range(0, max(1, len(d) - 19), 20):
                    p = d[i:i + 20]
                    if len(p) < 6:
                        continue
                    fn = (p[3] >> 4) & 0x0F
                    sw = p[3] & 0x0F
                    cid = (p[4] << 8) | p[5]
                    if sw == 0x0A:
                        echoes += 1
                        continue
                    real += 1
                    log(f"  [{time.time()-t0:6.1f}s] *** REAL EVENT ***  "
                        f"{p.hex(' ')}")
                    log(f"            fn={fn} sw={sw:#x} cid={cid:#06x}")

            now = time.time()
            if now >= next_poll:
                next_poll = now + 3.0
                try:
                    rows = dev.get_count()
                    log(f"  [{now-t0:6.1f}s] keepalive ok rows={rows} "
                        f"real={real} echoes={echoes}")
                except Exception as e:
                    log(f"  [{now-t0:6.1f}s] keepalive ERR {e}")

            if now - last_note > 20:
                last_note = now
                log(f"  [{now-t0:6.1f}s] ... waiting (real={real})")
            time.sleep(0.05)
    except KeyboardInterrupt:
        log("  interrupted")

    log()
    log("-" * 64)
    log(f"RESULT: genuine (sw==0) events = {real}    echoes ignored = {echoes}")
    if real:
        log("  ==> NOTIFICATIONS DO ARRIVE. The HID++ path is viable;")
        log("      mxvoice.py only needs to filter on sw==0.")
    else:
        log("  ==> No genuine notification seen.")
        log("      If you DID press the button during the window above,")
        log("      this confirms divert is a dead end on this transport.")
    log("-" * 64)

    dev.set_cid_reporting(0xC4, diverted=False)
    log("diversion cleared")
    _logf.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
