"""
injecttest.py -- prove the synthetic Ctrl+Alt really enters the input queue.

Installs a WH_KEYBOARD_LL hook, then calls the exact chord_down()/chord_up()
that mxvoice.py uses, and reports what the hook observed. Closes the loop on
the injection half without needing a target application.

Our keys carry dwExtraInfo == INJECT_TAG, so the hook can tell them apart from
anything the user physically types.
"""
import ctypes
import sys
import threading
import time
from ctypes import wintypes

sys.path.insert(0, ".")
import mxvoice as mv            # noqa: E402

user32 = mv.user32

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD),
                ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", mv.ULONG_PTR)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int,
                              wintypes.WPARAM, wintypes.LPARAM)

user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC,
                                     ctypes.c_void_p, wintypes.DWORD]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                  wintypes.WPARAM, wintypes.LPARAM]

seen = []
foreign = []


def cb(nCode, wParam, lParam):
    if nCode == 0:
        info = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        msg = int(wParam)
        kind = "down" if msg in (WM_KEYDOWN, WM_SYSKEYDOWN) else "up"
        if info.dwExtraInfo == mv.INJECT_TAG:
            seen.append((kind, info.vkCode))
            print(f"  hook saw INJECTED {kind:4} vk=0x{info.vkCode:02x}",
                  flush=True)
        elif info.vkCode in (mv.VK_CONTROL, mv.VK_MENU):
            foreign.append((kind, info.vkCode))
    return user32.CallNextHookEx(None, nCode, wParam, lParam)


proc = HOOKPROC(cb)
h = user32.SetWindowsHookExW(WH_KEYBOARD_LL, proc, None, 0)
print(f"keyboard hook handle = {h}")
if not h:
    print(f"[!] hook failed err={ctypes.get_last_error()}")
    sys.exit(1)


def pump():
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


threading.Thread(target=pump, daemon=True).start()
time.sleep(0.5)

print("injecting Ctrl+Alt down ...")
print("  SendInput returned:", mv.chord_down())
time.sleep(0.4)
print("injecting Ctrl+Alt up ...")
print("  SendInput returned:", mv.chord_up())
time.sleep(1.0)

user32.UnhookWindowsHookEx(ctypes.c_void_p(h))

print()
print(f"observed injected : {seen}")
print(f"observed foreign  : {foreign}")

VK_LCONTROL, VK_LMENU = 0xA2, 0xA4
expected = [("down", VK_LCONTROL), ("down", VK_LMENU),
            ("up", VK_LMENU), ("up", VK_LCONTROL)]
if seen == expected:
    print("\nPASS -- left Ctrl down, left Alt down, left Alt up, left Ctrl up")
else:
    print(f"\nUNEXPECTED\n  expected {expected}")
