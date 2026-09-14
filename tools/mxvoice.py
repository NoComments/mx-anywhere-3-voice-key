"""
mxvoice.py -- push-to-talk voice key for WeChat Input Method (微信输入法),
driven by the wheel Mode-Shift button on a Logitech MX Anywhere 3.

STATUS: WORKING over Bluetooth LE (verified 2026-09-14)
======================================================
An earlier note in this file claimed the HID++ path was blocked on BLE.
That was WRONG. It came from tests run while nobody was at the machine to
press the button, plus a misread of request echoes as button events. With a
real press, the device reports immediately:

  press   11 ff 09 00 00 c4 00 ...   fn=0  divertedButtons, cid 0x00c4
          11 ff 09 20 00 c4 01 ...   fn=2  cid 0x00c4, state 1
  release 11 ff 09 00 00 00 00 ...   fn=0  divertedButtons, no cid held
          11 ff 09 20 00 c4 00 ...   fn=2  cid 0x00c4, state 0

The one thing that must never be forgotten: replies to our OWN requests are
broadcast to every handle open on the same collection, and they look almost
identical to events. They are told apart by the software-id nibble in byte 3:

    sw == 0x0A  -> echo of our own request     -> DISCARD
    sw == 0x00  -> genuine device notification -> handle it

Getting that wrong is exactly how a poll reply was once mistaken for a
button press, which sent this whole effort down a false trail. parse_event()
now enforces the filter, so poll traffic can never masquerade as an edge.

The problem
-----------
The button behind the scroll wheel is HID++ control 0xC4 ("Smart Shift", also
called Wheel Mode-Shift). By default its press runs a purely local firmware
action and emits NO host-visible input event -- no mouse report, no keycode.
That is why a WH_MOUSE_LL hook can never see it, and why Logi Options+ can
only remap it by talking HID++ directly.

The fix
-------
Divert the control over HID++ 2.0. The device then stops performing the local
action and instead reports press/release to us:

    0x1b04 REPROG_CONTROLS_V4
      func 0  getCount                   -> rows in the control table
      func 1  getCidInfo(index)          -> row: cid/task/flags/...
      func 2  getCidReporting(cid)       -> current diversion state
      func 3  setCidReporting(cid, ...)  -> change diversion state

Unsolicited events -- the ones carrying sw nibble 0x0:
      func 0  divertedButtons  -> payload[0..8] = four big-endian CIDs, 0-padded;
                                  all zero means every control was released
      func 1  rawXY            -> two signed 16-bit deltas
      func 2  button state     -> payload[0:2] = cid, payload[2] = 1 press / 0 release

On each press edge we inject Ctrl+Alt down; on each release edge we inject
Ctrl+Alt up. WeChat IME's voice input is configured for Ctrl+Alt, giving
hold-to-talk / release-to-transcribe.

Requirements
------------
* Windows, Python 3.8+ (no third-party packages -- raw ctypes).
* Administrator rights: opening the HID++ vendor collection and diverting a
  control both need elevation.
* WeChat IME voice hotkey set to Ctrl+Alt (设置 -> 语音输入 -> 快捷键).

Usage
-----
    python mxvoice.py --probe      read the control table, change nothing
    python mxvoice.py              divert 0xC4 and map it to Ctrl+Alt
    python mxvoice.py --list       list Logitech HID interfaces
    python mxvoice.py --no-inject  divert and log events, inject nothing
"""
from __future__ import annotations

import argparse
import ctypes
import os
import signal
import sys
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hidpp


# --------------------------------------------------------------- constants --

LOGITECH_VID = 0x046D

# The MX Anywhere 3 presents different product ids depending on how it is
# connected, and the value changes when the user switches between Bluetooth
# and the Bolt receiver:
#   0x0B025 -- Bluetooth direct (transports=btle)
#   0x0B023 -- Bolt/Unifying receiver (transports=equad)
# Both are matched; anything else from Logitech that exposes 0x1b04 works too.
MX_ANYWHERE_3_PIDS = (0x0B025, 0x0B023)

FEAT_REPROG_CONTROLS_V4 = 0x1B04
ROOT_FEATURE_INDEX = 0x00
DEVICE_INDEX_DIRECT = 0xFF
SOFTWARE_ID = 0x0A

FN_GET_COUNT = 0
FN_GET_CID_INFO = 1
FN_GET_CID_REPORTING = 2
FN_SET_CID_REPORTING = 3

# Unsolicited event function ids, verified on an MX Anywhere 3 (pid b025,
# MPM24.01_B0015) with the wheel-rear button (CID 0xC4) diverted:
#
#   fn 0  divertedButtons  payload[0:8] = up to four big-endian CIDs, 0-padded.
#                          Pressed -> contains 0x00c4. All released -> all zero.
#   fn 1  rawXY            two signed 16-bit deltas (not used here)
#   fn 2  button state     payload[0:2] = cid, payload[2] = 1 press / 0 release
#
# A single physical press emits BOTH fn 0 and fn 2, so the event loop dedupes
# through its `held` flag rather than trusting one of them alone.
EVT_DIVERTED_BUTTONS = 0
EVT_RAW_XY = 1
EVT_BUTTON_STATE = 2

# CidFlags bits.
FLAG_MOUSE = 1 << 0
FLAG_FN_KEY = 1 << 1
FLAG_HOTKEY = 1 << 2
FLAG_FN_TOGGLE = 1 << 3
FLAG_REPROGRAMMABLE = 1 << 4
FLAG_DIVERTABLE = 1 << 5
FLAG_PERSISTENT_DIVERTABLE = 1 << 6
FLAG_VIRTUAL = 1 << 7
FLAG_RAW_XY = 1 << 8
FLAG_FORCE_RAW_XY = 1 << 9
FLAG_ANALYTICS = 1 << 10
FLAG_RAW_WHEEL = 1 << 11

FLAG_NAMES = {
    FLAG_MOUSE: "mouse",
    FLAG_FN_KEY: "fn-key",
    FLAG_HOTKEY: "hotkey",
    FLAG_FN_TOGGLE: "fn-toggle",
    FLAG_REPROGRAMMABLE: "reprogrammable",
    FLAG_DIVERTABLE: "divertable",
    FLAG_PERSISTENT_DIVERTABLE: "persistently-divertable",
    FLAG_VIRTUAL: "virtual-control",
    FLAG_RAW_XY: "raw-xy",
    FLAG_FORCE_RAW_XY: "force-raw-xy",
    FLAG_ANALYTICS: "analytics-events",
    FLAG_RAW_WHEEL: "raw-wheel",
}

TARGET_CID = 0x00C4      # wheel mode-shift -- the button behind the wheel
FALLBACK_CID = 0x00D7    # virtual gesture button

CID_NAMES = {
    0x0050: "left click",
    0x0051: "right click",
    0x0052: "middle click",
    0x0053: "wheel",
    0x0056: "wheel tilt",
    0x00C3: "app-switch gesture (MX Master only)",
    0x00C4: "wheel mode-shift  <-- TARGET",
    0x00D7: "virtual gesture button",
}

VK_CONTROL = 0x11
VK_MENU = 0x12           # Alt
HOLD_KEYS = [VK_CONTROL, VK_MENU]

# Tag put in dwExtraInfo on every key we synthesise, so our own input can be
# told apart from real user input (matters if a hook or the IME echoes back).
INJECT_TAG = 0x4D58_5601    # "MXV\1"


# ------------------------------------------------------ SendInput injection --

user32 = ctypes.WinDLL("user32", use_last_error=True)

ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001

user32.SendInput.restype = wintypes.UINT
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]


def _key_event(vk: int, up: bool) -> INPUT:
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    flags = KEYEVENTF_KEYUP if up else 0
    # Deliberately NOT setting KEYEVENTF_EXTENDEDKEY for Ctrl/Alt. That flag
    # selects the RIGHT-hand variant -- right Ctrl and right Alt -- and right
    # Alt is AltGr, which applications treat completely differently. Omitting
    # it yields plain left-hand Ctrl and Alt, which is what a normal hotkey
    # like Ctrl+Alt expects. Confirmed with a keyboard hook: the system sees
    # vk 0xa2 (LCONTROL) and 0xa4 (LMENU).
    inp.ki = KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0,
                        dwExtraInfo=INJECT_TAG)
    return inp


def send_inputs(items) -> bool:
    arr = (INPUT * len(items))(*items)
    return user32.SendInput(len(items), arr, ctypes.sizeof(INPUT)) == len(items)


def chord_down() -> bool:
    """Press Ctrl, then Alt."""
    return send_inputs([_key_event(vk, False) for vk in HOLD_KEYS])


def chord_up() -> bool:
    """Release Alt, then Ctrl (reverse order matters for held modifiers)."""
    return send_inputs([_key_event(vk, True) for vk in reversed(HOLD_KEYS)])


# ------------------------------------------------------- single instance --

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ERROR_ALREADY_EXISTS = 183
_singleton_handle = None
_singleton_owned = False


def acquire_singleton() -> bool:
    """Return False if another copy is already running.

    Two copies would both divert 0xC4 and both inject Ctrl+Alt, so every press
    would fire twice -- and whichever exits first would clear the diversion out
    from under the other. With auto-start at login this is easy to hit by
    accident, so the guard is worth having.

    Note: admin rights are NOT required. This was verified on the real device --
    every HID++ operation, including setCidReporting, works unelevated.

    Re-entrancy: CreateMutexW reports ERROR_ALREADY_EXISTS even when it is *this*
    process that already holds the name, so a second call inside one process
    (GUI: stop -> start again) would wrongly look like a conflict. The
    _singleton_owned flag short-circuits that case.
    """
    global _singleton_handle, _singleton_owned

    if _singleton_owned:
        return True                     # we already own it in this process

    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL,
                                      wintypes.LPCWSTR]
    h = kernel32.CreateMutexW(None, False, "Local\\mxvoice_singleton")
    if not h:
        return True                     # cannot tell -- allow the run
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        return False
    _singleton_handle = h               # keep the handle alive for the process
    _singleton_owned = True
    return True


# ------------------------------------------------------------ HID++ driver --


class HidppError(RuntimeError):
    pass


class HidppDevice:
    """A HID++ 2.0 session on one vendor-collection HID handle.

    Two handles are used deliberately: `h` for writes (opened overlapped, left
    alone) and `hr` for the event read loop (opened synchronously). Mixing them
    on one handle loses notifications, because cancelling a timed-out
    overlapped read also cancels the following one.
    """

    def __init__(self, handle, feature_index_1b04: int, read_handle=None,
                 reader=None, req_handle=None):
        self.h = handle
        self.hr = read_handle if read_handle is not None else handle
        # Requests need their own handle: once the background reader owns the
        # input handle, a request written there never gets a reply because the
        # reader thread is the one that would consume it.
        self.hq = req_handle if req_handle is not None else self.h
        self.feat = feature_index_1b04
        self.reader = reader

    # -- raw transport ------------------------------------------------------
    def _send(self, feature_index: int, function: int, params=(),
              sw: int = SOFTWARE_ID, timeout_ms: int = 2000):
        pkt = bytearray(20)
        pkt[0] = 0x11                       # long report
        pkt[1] = DEVICE_INDEX_DIRECT
        pkt[2] = feature_index
        pkt[3] = ((function & 0x0F) << 4) | (sw & 0x0F)
        for i, b in enumerate(params[:16]):
            pkt[4 + i] = b
        if not hidpp.write(self.h, bytes(pkt)):
            raise HidppError(f"write failed (err={ctypes.get_last_error()})")
        # Always read the reply inline on the request handle. The background
        # reader owns a different handle, so it cannot steal this response.
        return hidpp.read(self.hq, 32, timeout_ms)

    def _request(self, feature_index: int, function: int, params=()):
        """Send a request and return its response.

        Only called during setup, before the event loop starts. On this
        firmware press/release notifications share the function numbering with
        request replies, so running both at once would be ambiguous -- the
        design avoids it instead of guessing.
        """
        for _ in range(20):
            r = self._send(feature_index, function, params)
            if not r or len(r) < 6:
                continue
            if (r[3] & 0x0F) != SOFTWARE_ID:
                continue
            if r[2] == 0xFF:
                raise HidppError(f"device error response {r[4]:#04x}")
            return r
        raise HidppError("no response from device")

    # -- feature discovery --------------------------------------------------
    def get_feature_index(self, feature_id: int):
        r = self._request(ROOT_FEATURE_INDEX, 0x00,
                          [(feature_id >> 8) & 0xFF, feature_id & 0xFF])
        return r[4] or None

    # -- 0x1b04 calls -------------------------------------------------------
    def get_count(self) -> int:
        # Documented as a short call, but this device's vendor collection only
        # accepts 20-byte reports, so send the long form.
        return self._request(self.feat, FN_GET_COUNT, [0] * 16)[4]

    def get_cid_info(self, index: int) -> dict:
        """One control-table row. `index` is a row number, not a CID."""
        r = self._request(self.feat, FN_GET_CID_INFO, [index] + [0] * 15)
        p = r[4:20]
        return {
            "cid": (p[0] << 8) | p[1],
            "task": (p[2] << 8) | p[3],
            "flags": p[4] | (p[8] << 8),
            "position": p[5],
            "group": p[6],
            "group_mask": p[7],
        }

    def get_cid_reporting(self, cid: int) -> dict:
        r = self._request(self.feat, FN_GET_CID_REPORTING,
                          [(cid >> 8) & 0xFF, cid & 0xFF] + [0] * 14)
        p = r[4:20]
        bits = p[2]
        return {
            "cid": (p[0] << 8) | p[1],
            "diverted": bool(bits & (1 << 0)),
            "persistently_diverted": bool(bits & (1 << 2)),
            "raw_xy": bool(bits & (1 << 4)),
            "force_raw_xy": bool(bits & (1 << 6)),
            "raw_wheel": bool(p[5] & (1 << 2)),
            "analytics": bool(p[5] & (1 << 0)),
        }

    def set_cid_reporting(self, cid: int, *, diverted=None,
                          persistently_diverted=None, raw_xy=None,
                          force_raw_xy=None, raw_wheel=None, analytics=None,
                          remap=None) -> dict:
        """Send a setCidReporting request.

        Parameter byte 2 packs a value/valid pair per option:
            bit0 value  bit1 valid   (divert)
            bit2 value  bit3 valid   (persistent divert)
            bit4 value  bit5 valid   (raw xy)
            bit6 value  bit7 valid   (force raw xy)
        and byte 5 does the same for analytics (bits 0/1) and raw wheel (2/3).
        """
        p = bytearray(16)
        p[0] = (cid >> 8) & 0xFF
        p[1] = cid & 0xFF
        if diverted is not None:
            p[2] |= 1 << 1
            p[2] |= 1 if diverted else 0
        if persistently_diverted is not None:
            p[2] |= 1 << 3
            p[2] |= (1 if persistently_diverted else 0) << 2
        if raw_xy is not None:
            p[2] |= 1 << 5
            p[2] |= (1 if raw_xy else 0) << 4
        if force_raw_xy is not None:
            p[2] |= 1 << 7
            p[2] |= (1 if force_raw_xy else 0) << 6
        if remap is not None:
            p[3] = (remap >> 8) & 0xFF
            p[4] = remap & 0xFF
        if analytics is not None:
            p[5] |= 1 << 1
            p[5] |= 1 if analytics else 0
        if raw_wheel is not None:
            p[5] |= 1 << 3
            p[5] |= (1 if raw_wheel else 0) << 2
        r = self._request(self.feat, FN_SET_CID_REPORTING, bytes(p))
        return {"cid": (r[4] << 8) | r[5], "raw": bytes(r)}

    # -- notification decoding ---------------------------------------------
    @staticmethod
    def parse_event(data: bytes, feature_index: int, target_cid: int = None):
        """Decode one or more genuine 0x1b04 notifications.

        Returns ("down", cid) / ("up", cid) / ("buttons", cids) / None.

        THE SOFTWARE-ID FILTER IS MANDATORY. Replies to our own requests are
        broadcast to every handle open on the collection, and a getCidReporting
        reply is byte-identical to a press notification apart from byte 3:

            reply  11 ff 09 2a 00 c4 01 ...   fn=2 sw=0xA   <- ours, discard
            event  11 ff 09 20 00 c4 01 ...   fn=2 sw=0x0   <- device

        Only sw == 0x00 is a genuine notification; everything else is dropped.
        A single ReadFile may return several 20-byte reports concatenated.
        """
        if not data or len(data) < 6:
            return None

        if len(data) > 20:
            out = []
            for i in range(0, len(data) - 19, 20):
                ev = HidppDevice.parse_event(data[i:i + 20], feature_index,
                                             target_cid)
                if ev:
                    out.append(ev)
            if not out:
                return None
            return out[0] if len(out) == 1 else ("multi", out)

        if data[2] != feature_index:
            return None

        # 0xA is our own software id -> a reply to a request we sent.
        # 0x0 is the device speaking on its own -> a real notification.
        if (data[3] & 0x0F) != 0x00:
            return None

        function = (data[3] >> 4) & 0x0F
        payload = data[4:20]

        if function == EVT_DIVERTED_BUTTONS:
            cids = []
            for i in range(0, 8, 2):
                c = (payload[i] << 8) | payload[i + 1]
                if c:
                    cids.append(c)
            if not cids:
                # No control held any more -> release of whatever was down.
                return ("up", None)
            if target_cid is not None:
                return ("down", target_cid) if target_cid in cids else None
            return ("buttons", cids)

        if function == EVT_BUTTON_STATE:
            cid = (payload[0] << 8) | payload[1]
            if target_cid is not None and cid != target_cid:
                return None
            return ("down", cid) if payload[2] else ("up", cid)

        # fn 1 (rawXY) and anything else are not needed for push-to-talk.
        return None


# ------------------------------------------------------- device discovery --


def find_mouse_handle():
    """Open the MX Anywhere 3 HID++ vendor collection.

    Rather than trusting a hard-coded product id, this walks every Logitech HID
    interface, opens the ones exposing usage page 0xFF43 (Logitech's HID++
    vendor collection) and keeps the first that actually answers a HID++
    feature lookup for 0x1b04. That survives the pid changing when the mouse
    moves between Bluetooth direct and the Bolt receiver.
    """
    paths = hidpp.enumerate_paths(LOGITECH_VID)
    if not paths:
        raise SystemExit(
            "No Logitech HID interfaces found.\n"
            "  * Is the mouse connected and awake?\n"
            "  * Is this console running as Administrator?")

    # Prefer the known MX Anywhere 3 ids so unrelated Logitech gear is skipped,
    # but fall back to any Logitech device that exposes 0x1b04.
    def pid_of(p: str) -> int:
        low = p.lower()
        marker = "pid&"
        i = low.find(marker)
        if i < 0:
            return -1
        try:
            return int(low[i + len(marker):i + len(marker) + 4], 16)
        except ValueError:
            return -1

    preferred = [p for p in paths if pid_of(p) in MX_ANYWHERE_3_PIDS]
    candidates = preferred + [p for p in paths if p not in preferred]

    vendor_candidates = []
    for p in candidates:
        h = hidpp.open_path(p)
        if not h:
            continue
        c = hidpp.caps(h)
        if c and c.UsagePage == 0xFF43:
            vendor_candidates.append((h, p, c))
        else:
            hidpp.close(h)

    if not vendor_candidates:
        raise SystemExit("No HID++ vendor collection (usage page 0xFF43) found.")

    # Confirm the device really speaks 0x1b04 before committing to it.
    for h, p, c in vendor_candidates:
        hr = hidpp.open_path(p, overlapped=False)
        hq = hidpp.open_path(p, overlapped=False)
        probe_dev = HidppDevice(h, 0, hr, None, hq)
        try:
            if probe_dev.get_feature_index(FEAT_REPROG_CONTROLS_V4):
                for hh, pp, cc in vendor_candidates:
                    if hh != h:
                        hidpp.close(hh)
                return (h, p, c, hr, hq)
        except (HidppError, OSError):
            pass
        if hr:
            hidpp.close(hr)

    # Nothing answered -- still hand back the first vendor collection so the
    # caller can report a useful error.
    h, p, c = vendor_candidates[0]
    hr = hidpp.open_path(p, overlapped=False)
    hq = hidpp.open_path(p, overlapped=False)
    for hh, pp, cc in vendor_candidates[1:]:
        hidpp.close(hh)
    return (h, p, c, hr, hq)


# ------------------------------------------------------------ operations --


def probe(h, feat_index, quiet=False, read_handle=None, req_handle=None):
    dev = HidppDevice(h, feat_index, read_handle, None, req_handle)
    n = dev.get_count()
    if not quiet:
        print(f"[*] control table has {n} rows")
        print(f"    {'cid':>6}  {'task':>6}  {'flags':>6}  capabilities")
    rows = []
    for i in range(n):
        try:
            info = dev.get_cid_info(i)
        except HidppError as e:
            if not quiet:
                print(f"    row {i}: {e}")
            continue
        rows.append(info)
        if not quiet:
            caps = ", ".join(name for bit, name in FLAG_NAMES.items()
                             if info["flags"] & bit) or "-"
            print(f"    {info['cid']:#06x}  {info['task']:#06x}  "
                  f"{info['flags']:#06x}  {caps}")
            if info["cid"] in CID_NAMES:
                print(f"            -> {CID_NAMES[info['cid']]}")
    return rows


def pick_target(rows):
    by_cid = {r["cid"]: r for r in rows}
    for cid in (TARGET_CID, FALLBACK_CID):
        r = by_cid.get(cid)
        if r and (r["flags"] & FLAG_DIVERTABLE):
            return cid, r
    return None, None


KEEPALIVE_SECONDS = 5.0

# How long the button must stay released before we treat it as a real release.
# The device can emit repeated press/release pairs while the button is held
# down; without this, a hold would inject Ctrl+Alt down/up over and over and
# the IME would start and stop recording. A press arriving inside this window
# cancels the pending release, so a hold stays one continuous press.
RELEASE_DEBOUNCE_MS = 250


class KeepAlive:
    """A harmless periodic request, on its own thread.

    Windows delivers HID input reports asynchronously, so strictly this should
    not be necessary. It is cheap insurance: it keeps the device chattering and
    doubles as a liveness check. It owns the request handle and runs on a
    separate thread, so it can never block or delay the event loop.
    """

    def __init__(self, dev, interval: float = KEEPALIVE_SECONDS):
        import threading

        self.dev = dev
        self.interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self):
        while not self._stop.wait(self.interval):
            try:
                self.dev.get_count()
            except Exception:
                pass          # the device may be busy; never fatal

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()


def run(h, feat_index, do_inject=True, verbose=True, duration=None,
        read_handle=None, req_handle=None,
        debounce_ms=RELEASE_DEBOUNCE_MS, stop_event=None):
    # Phase 1 -- plain request/response on the dedicated request handle, no
    # background reader yet, so probe and the diversion write cannot race with
    # event delivery.
    dev = HidppDevice(h, feat_index, read_handle, None, req_handle)

    rows = probe(h, feat_index, quiet=True, read_handle=read_handle,
                 req_handle=req_handle)
    cid, info = pick_target(rows)
    if cid is None:
        print("[!] Neither 0xC4 nor 0xD7 is present and divertable.")
        probe(h, feat_index, read_handle=read_handle,
              req_handle=req_handle)
        return 1

    print(f"[*] target control : {cid:#06x}  {CID_NAMES.get(cid, '')}")
    print(f"[*] capabilities   : "
          f"{', '.join(n for b, n in FLAG_NAMES.items() if info['flags'] & b)}")

    try:
        dev.set_cid_reporting(cid, diverted=True, analytics=True)
    except HidppError as e:
        print(f"[!] setCidReporting failed: {e}")
        return 1

    st = dev.get_cid_reporting(cid)
    if not st["diverted"]:
        print("[!] Device did not confirm the diversion -- aborting.")
        return 1

    print(f"[*] diverted OK (analytics={st['analytics']}) -- "
          f"injecting {'Ctrl+Alt' if do_inject else '(nothing)'}")
    print("[*] Hold the wheel-rear button to talk, release to transcribe.")
    print("[*] Ctrl+C to stop.\n")

    # Phase 2 -- background reader on the event handle; the main loop only
    # consumes its queue. The keep-alive owns the request handle, so request
    # traffic can never steal or delay an event.
    reader = hidpp.ReportReader(read_handle, 64)
    dev.reader = reader
    keep = KeepAlive(dev).start()

    held = False
    release_at = None           # monotonic deadline of a pending release
    stats = {"down": 0, "up": 0, "repeat": 0}
    deadline = time.time() + duration if duration else None

    def _finish_release():
        nonlocal held, release_at
        release_at = None
        held = False
        stats["up"] += 1
        if verbose:
            print(f"  [{time.strftime('%H:%M:%S')}] UP   -> Ctrl+Alt up")
        if do_inject:
            chord_up()

    def on_down(edge_cid):
        """Button pressed, or repeating while still held."""
        nonlocal held, release_at
        if edge_cid is not None and edge_cid != cid:
            return
        if release_at is not None:
            # A press landed inside the debounce window: the button never
            # actually let go, it is repeating. Cancel the pending release so
            # the hold stays one continuous press instead of stuttering.
            release_at = None
            stats["repeat"] += 1
            return
        if held:
            return              # fn 0 and fn 2 both fire for a single press
        held = True
        stats["down"] += 1
        if verbose:
            print(f"  [{time.strftime('%H:%M:%S')}] DOWN -> Ctrl+Alt down")
        if do_inject:
            chord_down()

    def on_up(edge_cid):
        """Button released. Arm the release; only fire if it stays released."""
        nonlocal release_at
        if edge_cid is not None and edge_cid != cid:
            return
        if not held:
            return
        if debounce_ms <= 0:
            _finish_release()
        else:
            release_at = time.monotonic() + debounce_ms / 1000.0

    def handle_edge(kind, edge_cid):
        if kind == "down":
            on_down(edge_cid)
        else:
            on_up(edge_cid)

    try:
        last_status = time.monotonic()
        while (deadline is None or time.time() < deadline) and \
                not (stop_event is not None and stop_event.is_set()):
            data = dev.reader.get(50) if dev.reader is not None \
                else hidpp.read(dev.hr, 64, 50)

            # Fire a pending release once the button has stayed up long enough.
            if release_at is not None and time.monotonic() >= release_at:
                _finish_release()

            # Periodic heartbeat, so a piped log shows the program is alive.
            now = time.monotonic()
            if now - last_status >= 20:
                last_status = now
                if verbose:
                    print(f"  [{time.strftime('%H:%M:%S')}] ... held={held} "
                          f"presses={stats['down']} releases={stats['up']} "
                          f"repeats={stats['repeat']}")

            if not data:
                continue
            ev = dev.parse_event(data, feat_index, cid)
            if not ev:
                continue
            kind, value = ev

            if kind in ("down", "up"):
                handle_edge(kind, value)
            elif kind == "multi":
                for k, v in value:
                    if k in ("down", "up"):
                        handle_edge(k, v)
            elif kind == "buttons":
                handle_edge("down" if cid in value else "up", None)
    except KeyboardInterrupt:
        print(f"\n[*] stopping (presses={stats['down']} releases={stats['up']} "
              f"repeats_absorbed={stats['repeat']})")
    finally:
        keep.stop()
        reader.stop()
        if held and do_inject:
            chord_up()
        dev.reader = None
        try:
            dev.set_cid_reporting(cid, diverted=False, analytics=False)
            print("[*] diversion cleared -- button restored to normal.")
        except HidppError as e:
            print(f"[!] could not clear diversion: {e}")
    return 0


# ------------------------------------------------------------------ main --


def main(argv=None):
    # Line-buffer stdout so progress is visible even when piped to a file.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

    ap = argparse.ArgumentParser(
        description="Map the MX Anywhere 3 wheel-rear button to WeChat IME voice input.")
    ap.add_argument("--probe", action="store_true",
                    help="read the control table and exit (changes nothing)")
    ap.add_argument("--list", action="store_true",
                    help="list Logitech HID interfaces and exit")
    ap.add_argument("--no-inject", action="store_true",
                    help="divert and log events, but inject no keys")
    ap.add_argument("--seconds", type=float, default=None,
                    help="stop automatically after N seconds")
    ap.add_argument("--debounce", type=int, default=RELEASE_DEBOUNCE_MS,
                    metavar="MS",
                    help="ms the button must stay released to count as a "
                         "release (0 disables; default %(default)s)")
    args = ap.parse_args(argv)

    if args.list:
        for p in hidpp.enumerate_paths(LOGITECH_VID):
            print(p)
        return 0

    h, path, caps, hr, hq = find_mouse_handle()
    print(f"[*] device : {path}")
    print(f"[*] collection: usage page {caps.UsagePage:#06x}, "
          f"in={caps.InputReportByteLength} out={caps.OutputReportByteLength}")
    try:
        dev = HidppDevice(h, 0, hr, None, hq)
        feat_index = dev.get_feature_index(FEAT_REPROG_CONTROLS_V4)
        if feat_index is None:
            print("[!] This device does not expose 0x1b04 REPROG_CONTROLS_V4.")
            return 1
        print(f"[*] 0x1b04 REPROG_CONTROLS_V4 at feature index {feat_index}")

        if args.probe:
            probe(h, feat_index, read_handle=hr, req_handle=hq)
            return 0

        if not acquire_singleton():
            print("[!] mxvoice 已经在运行了，不再启动第二个实例。")
            print("    先关掉原来那个；如果是被强制结束的残留，"
                  "跑一下 restore.py。")
            return 1

        return run(h, feat_index, do_inject=not args.no_inject,
                   duration=args.seconds, read_handle=hr,
                   req_handle=hq, debounce_ms=args.debounce)
    finally:
        for hh in {h, hr, hq}:
            if hh:
                hidpp.close(hh)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.default_int_handler)
    sys.exit(main())
