"""
holdtest.py -- does holding the button give ONE press+release, or repeats?

This decides whether push-to-talk is viable at all.

    one down ... long gap ... one up      -> the design works as intended
    many rapid down/up pairs while held   -> holding would start and stop
                                             recording over and over, and the
                                             events need debouncing

Diverts 0xC4, records every genuine (sw == 0x0) event, then groups them into
press/release cycles and prints each hold duration.

Logs to holdtest.log.
"""
from __future__ import annotations

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

LOG = os.path.join(HERE, "holdtest.log")
_logf = open(LOG, "w", encoding="utf-8", buffering=1)


def log(msg: str = "") -> None:
    print(msg, flush=True)
    _logf.write(msg + "\n")


k = ctypes.WinDLL("kernel32", use_last_error=True)
k.ReadFile.restype = ctypes.c_bool
k.ReadFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                       ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]

TARGET = 0x00C4
SECONDS = 240


def main():
    h, path, caps, hr, hq = mv.find_mouse_handle()
    log(f"device : ...{path[-58:]}")
    dev = mv.HidppDevice(h, 0, hr, None, hq)
    dev.feat = dev.get_feature_index(mv.FEAT_REPROG_CONTROLS_V4)
    log(f"0x1b04 at feature index {dev.feat}")

    q: "queue.Queue[bytes]" = queue.Queue()

    def reader():
        while True:
            buf = ctypes.create_string_buffer(64)
            n = ctypes.c_uint32(0)
            if k.ReadFile(ctypes.c_void_p(hr), buf, 64,
                          ctypes.byref(n), None) and n.value:
                q.put(bytes(buf.raw[:n.value]))

    threading.Thread(target=reader, daemon=True).start()
    time.sleep(0.6)
    while not q.empty():
        q.get_nowait()

    edges = []          # list of (t, kind)
    held = False

    try:
        dev.set_cid_reporting(TARGET, diverted=True, analytics=True)
        log(f"diverted = {dev.get_cid_reporting(TARGET)['diverted']}")
        time.sleep(0.4)
        while not q.empty():
            q.get_nowait()

        log()
        log("=" * 66)
        log("   PRESS AND HOLD the wheel-rear button for a FULL 3 SECONDS,")
        log("   then let go.  Count 'one-two-three' in your head while held.")
        log()
        log("   Do that 3 times, with a pause between each.")
        log("=" * 66)
        log()

        t0 = time.time()
        while time.time() - t0 < SECONDS:
            while not q.empty():
                d = q.get_nowait()
                ev = mv.HidppDevice.parse_event(d, dev.feat, TARGET)
                if not ev:
                    continue
                items = ev[1] if ev[0] == "multi" else [ev]
                for kind, cid in items:
                    if kind == "down" and not held:
                        held = True
                        edges.append((time.time() - t0, "down"))
                        log(f"  [{time.time()-t0:6.2f}s] DOWN")
                    elif kind == "up" and held:
                        held = False
                        edges.append((time.time() - t0, "up"))
                        log(f"  [{time.time()-t0:6.2f}s] UP")
            time.sleep(0.03)

        log()
        log("-" * 66)
        cycles = []
        i = 0
        while i < len(edges):
            if edges[i][1] == "down":
                if i + 1 < len(edges) and edges[i + 1][1] == "up":
                    cycles.append((edges[i][0], edges[i + 1][0]))
                    i += 2
                else:
                    cycles.append((edges[i][0], None))
                    i += 1
            else:
                i += 1

        log(f"edge events = {len(edges)}   press/release cycles = {len(cycles)}")
        log()
        log(f"  {'#':>3}  {'held for':>10}   start")
        for n, (a, b) in enumerate(cycles, 1):
            dur = f"{b - a:.2f}s" if b is not None else "still held"
            log(f"  {n:>3}  {dur:>10}   {a:6.2f}s")
        log()

        holds = [b - a for a, b in cycles if b is not None]
        if not holds:
            log("No complete press/release cycle captured.")
        else:
            long_holds = [x for x in holds if x >= 1.0]
            short = [x for x in holds if x < 1.0]
            log(f"long holds (>=1s): {len(long_holds)}   short taps (<1s): {len(short)}")
            if long_holds and not short:
                log("  ==> Holding gives ONE down ... ONE up. Push-to-talk works.")
            elif short and not long_holds:
                log("  ==> Every press was short even though you held it:")
                log("      the button AUTO-REPEATS. Debouncing is required.")
            else:
                log("  ==> Mixed. Look at the durations above.")
        log("-" * 66)

    except KeyboardInterrupt:
        log("  interrupted")
    finally:
        try:
            dev.set_cid_reporting(TARGET, diverted=False, analytics=False)
            log("diversion cleared")
        except Exception:
            pass
        _logf.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
