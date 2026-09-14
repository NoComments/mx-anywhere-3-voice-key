"""
verify.py -- two-button control experiment that gives an unambiguous answer.

Diverts BOTH:
  0x52  middle click      -- a control we KNOW reaches Windows normally
  0xC4  wheel mode-shift  -- the button you actually want

Classification is by the software-id nibble in byte 3:
    sw == 0xA -> echo of a request we sent   (ignore)
    sw == 0x0 -> genuine unsolicited notification

Interpretation:
   0x52 real, 0xC4 none  -> channel WORKS; wheel-rear button does not report
   0x52 real, 0xC4 real  -> SUCCESS
   neither real          -> notification channel blocked on this transport

Logs to verify.log continuously so progress can be read while running.
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

LOG = os.path.join(HERE, "verify.log")
_logf = open(LOG, "w", encoding="utf-8", buffering=1)


def log(msg: str = "") -> None:
    print(msg, flush=True)
    _logf.write(msg + "\n")


k = ctypes.WinDLL("kernel32", use_last_error=True)
k.ReadFile.restype = ctypes.c_bool
k.ReadFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                       ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]

MIDDLE = 0x0052
TARGET = 0x00C4
SECONDS = 300


def main():
    h, path, caps, hr, hq = mv.find_mouse_handle()
    log(f"device : ...{path[-58:]}")
    log(f"caps   : usage {caps.UsagePage:#06x} in={caps.InputReportByteLength}")

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

    real = {MIDDLE: 0, TARGET: 0}
    echoes = 0

    try:
        # Divert both. 0x52 will stop behaving as a normal middle click while
        # this test runs -- that is expected and is reverted at the end.
        dev.set_cid_reporting(MIDDLE, diverted=True)
        dev.set_cid_reporting(TARGET, diverted=True, analytics=True)
        s1 = dev.get_cid_reporting(MIDDLE)
        s2 = dev.get_cid_reporting(TARGET)
        log(f"diverted 0x52 middle : {s1['diverted']}")
        log(f"diverted 0xc4 target : {s2['diverted']}  analytics={s2['analytics']}")
        time.sleep(0.4)
        while not q.empty():
            q.get_nowait()

        log()
        log("=" * 66)
        log("   PLEASE DO BOTH, IN THIS ORDER:")
        log()
        log("     1) MIDDLE-CLICK 3 times   (press the scroll wheel down)")
        log("     2) Then press the WHEEL-REAR button 3 times")
        log("        (the button behind the scroll wheel)")
        log()
        log("   Take your time. There are 5 minutes.")
        log("=" * 66)
        log()

        t0 = time.time()
        next_poll = t0 + 3.0
        last_note = t0
        while time.time() - t0 < SECONDS:
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
                    label = {MIDDLE: "MIDDLE(0x52)",
                             TARGET: "WHEEL-REAR(0xc4)"}.get(cid, f"0x{cid:04x}")
                    real[cid] = real.get(cid, 0) + 1
                    log(f"  [{time.time()-t0:6.1f}s] *** REAL *** {label} "
                        f"fn={fn} sw={sw:#x}")
                    log(f"            {p.hex(' ')}")

            now = time.time()
            if now >= next_poll:
                next_poll = now + 3.0
                try:
                    dev.get_count()
                except Exception as e:
                    log(f"  [{now-t0:6.1f}s] keepalive ERR {e}")
            if now - last_note > 30:
                last_note = now
                log(f"  [{now-t0:6.1f}s] running... middle={real[MIDDLE]} "
                    f"wheel-rear={real[TARGET]} echoes={echoes}")
            time.sleep(0.05)

    except KeyboardInterrupt:
        log("  interrupted")
    finally:
        log()
        log("-" * 66)
        m, t = real[MIDDLE], real[TARGET]
        log(f"RESULT: middle(0x52) real={m}   wheel-rear(0xc4) real={t}"
            f"   echoes={echoes}")
        if t and m:
            log("  ==> BOTH WORK. The wheel-rear button IS usable.")
        elif m and not t:
            log("  ==> Channel works (middle reported) but the wheel-rear button")
            log("      does NOT report. Dead end for that specific button.")
        elif not m and not t:
            log("  ==> NEITHER reported. The notification channel is blocked")
            log("      on this transport -- but check you actually pressed!")
        log("-" * 66)
        for cid in (MIDDLE, TARGET):
            try:
                dev.set_cid_reporting(cid, diverted=False, analytics=False)
            except Exception:
                pass
        log("diversions cleared -- buttons back to normal")
        _logf.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
