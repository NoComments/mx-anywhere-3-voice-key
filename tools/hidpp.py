"""
hidpp.py -- minimal HID++ 2.0 client over Windows' built-in hid.dll (via ctypes).

No third-party packages required.

Provides just enough to:
  * enumerate Logitech HID++ interfaces (vendor usage page 0xFF00 / 0x0001)
  * send a HID++ short/long report
  * read HID++ responses and notifications

HID++ 2.0 report format used here (Logitech "short" 7-byte report):
    byte0 = report id (0x10 or 0x11 for some receivers; 0x11 is long)
    byte1 = device index (0xFF for a directly-attached / Bluetooth device)
    byte2 = feature index
    byte3 = function | sw id (high nibble = function, low nibble = sw id)
    byte4.. = parameters

For BLE/direct-connected devices the "Short" report (report id 0x10) is
7 bytes total; the "Long" report (report id 0x11) is 20 bytes.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
from ctypes import wintypes

# ---------------------------------------------------------------- hid.dll --


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
                ("Data4", ctypes.c_ubyte * 8)]

    def __init__(self, s: str | None = None):
        super().__init__()
        if s:
            ctypes.oledll.ole32.CLSIDFromString(s, ctypes.byref(self))


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Size", wt.ULONG), ("VendorID", wt.USHORT),
                ("ProductID", wt.USHORT), ("VersionNumber", wt.USHORT)]


hid = ctypes.WinDLL("hid", use_last_error=True)
setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

hid.HidD_GetHidGuid.argtypes = [ctypes.POINTER(GUID)]
hid.HidD_GetAttributes.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDD_ATTRIBUTES)]
hid.HidD_GetPreparsedData.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
hid.HidD_GetProductString.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.ULONG]
hid.HidD_SetNumInputBuffers.argtypes = [ctypes.c_void_p, wt.ULONG]

HidP = ctypes.WinDLL("hid", use_last_error=True)


class HIDP_CAPS(ctypes.Structure):
    # Layout per ddk/hidpi.h. Native size is 62 bytes (all members are USHORT);
    # _pack_ = 1 keeps ctypes from inserting padding that does not exist in C.
    _pack_ = 1
    _fields_ = [
        ("Usage", wt.USHORT), ("UsagePage", wt.USHORT),
        ("InputReportByteLength", wt.USHORT),
        ("OutputReportByteLength", wt.USHORT),
        ("FeatureReportByteLength", wt.USHORT),
        ("Reserved", wt.USHORT * 17),
        ("NumberLinkCollectionNodes", wt.USHORT),
        ("NumberInputButtonCaps", wt.USHORT),
        ("NumberInputValueCaps", wt.USHORT),
        ("NumberInputDataIndices", wt.USHORT),
        ("NumberOutputButtonCaps", wt.USHORT),
        ("NumberOutputValueCaps", wt.USHORT),
        ("NumberOutputDataIndices", wt.USHORT),
        ("NumberFeatureButtonCaps", wt.USHORT),
        ("NumberFeatureValueCaps", wt.USHORT),
        ("NumberFeatureDataIndices", wt.USHORT),
    ]


# Bind HidP_GetCaps only AFTER HIDP_CAPS exists. Using a string forward
# reference here makes ctypes resolve the pointer type lazily, and the DLL can
# then write past the real struct -- which segfaults the process.
HidP.HidP_GetCaps.restype = wt.LONG
HidP.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]


DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x1
FILE_SHARE_WRITE = 0x2
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
INVALID_HANDLE = ctypes.c_void_p(-1).value


# ctypes.wintypes has no OVERLAPPED; define it (dwOffset/dwOffsetHigh are a
# union with the 64-bit Offset, so two DWORDs are fine here).
class OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_void_p),
        ("InternalHigh", ctypes.c_void_p),
        ("Offset", wt.DWORD),
        ("OffsetHigh", wt.DWORD),
        ("hEvent", ctypes.c_void_p),
    ]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    # NOTE: `Reserved` is a ULONG_PTR on the native side, so it MUST be
    # c_void_p -- using POINTER(ULONG) makes ctypes walk off into the weeds.
    _fields_ = [("cbSize", wt.DWORD), ("InterfaceClassGuid", GUID),
                ("Flags", wt.DWORD), ("Reserved", ctypes.c_void_p)]


setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wt.LPCWSTR,
                                         ctypes.c_void_p, wt.DWORD]
setupapi.SetupDiEnumDeviceInterfaces.restype = wt.BOOL
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    ctypes.c_void_p,          # DeviceInfoSet
    ctypes.c_void_p,          # DeviceInfoData (may be NULL)
    ctypes.POINTER(GUID),     # InterfaceClassGuid
    wt.DWORD,                 # MemberIndex
    ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wt.BOOL
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA), ctypes.c_void_p,
    wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]

# kernel32 handles are pointer-sized on x64; declaring restype/argtypes keeps
# Python from truncating them to 32 bits (the classic ctypes handle bug).
kernel32.CreateFileW.restype = ctypes.c_void_p
kernel32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD,
                                 ctypes.c_void_p, wt.DWORD, wt.DWORD,
                                 ctypes.c_void_p]
kernel32.CloseHandle.restype = wt.BOOL
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
kernel32.WriteFile.restype = wt.BOOL
kernel32.WriteFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.DWORD,
                               ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
kernel32.ReadFile.restype = wt.BOOL
kernel32.ReadFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.DWORD,
                              ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
kernel32.CreateEventW.restype = ctypes.c_void_p
kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR]
kernel32.WaitForSingleObject.restype = wt.DWORD
kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, wt.DWORD]
kernel32.GetOverlappedResult.restype = wt.BOOL
kernel32.GetOverlappedResult.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                         ctypes.POINTER(wt.DWORD), wt.BOOL]
kernel32.CancelIo.restype = wt.BOOL
kernel32.CancelIo.argtypes = [ctypes.c_void_p]


def enumerate_paths(vid: int | None = None) -> list[str]:
    """Return device interface paths for HID devices, optionally filtered by VID."""
    g = GUID()
    hid.HidD_GetHidGuid(ctypes.byref(g))
    h = setupapi.SetupDiGetClassDevsW(ctypes.byref(g), None, None,
                                      DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if not h or h == INVALID_HANDLE:
        return []
    out: list[str] = []
    i = 0
    try:
        while True:
            did = SP_DEVICE_INTERFACE_DATA()
            did.cbSize = ctypes.sizeof(did)
            if not setupapi.SetupDiEnumDeviceInterfaces(
                    ctypes.c_void_p(h), None, ctypes.byref(g), i, ctypes.byref(did)):
                break
            need = wt.DWORD(0)
            setupapi.SetupDiGetDeviceInterfaceDetailW(
                ctypes.c_void_p(h), ctypes.byref(did), None, 0,
                ctypes.byref(need), None)
            if need.value:
                buf = ctypes.create_string_buffer(need.value)
                # SP_DEVICE_INTERFACE_DETAIL_DATA_W.cbSize is a DWORD, but on
                # x64 the struct is 8-byte aligned -> cbSize must be 8 there.
                ctypes.cast(buf, ctypes.POINTER(wt.DWORD))[0] = (
                    8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6)
                if setupapi.SetupDiGetDeviceInterfaceDetailW(
                        ctypes.c_void_p(h), ctypes.byref(did), buf, need.value,
                        ctypes.byref(need), None):
                    out.append(ctypes.wstring_at(
                        ctypes.addressof(buf) + ctypes.sizeof(wt.DWORD)))
            i += 1
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(ctypes.c_void_p(h))

    if vid is None:
        return out
    # BLE/Bluetooth paths encode the vendor id as `vid&02046d` (the `02` is a
    # usage-page prefix) while USB paths use `vid_046d`. Accept either form.
    keep = []
    needle = f"{vid:04x}"
    for p in out:
        low = p.lower()
        if (f"vid_{needle}" in low) or (f"vid&02{needle}" in low) \
                or (f"vid&{needle}" in low):
            keep.append(p)
    return keep


def open_path(path: str, overlapped: bool = True):
    """Open a HID device path, returning an integer handle (or None).

    `overlapped=True` is required for read() to honour its timeout: without
    FILE_FLAG_OVERLAPPED, ReadFile blocks synchronously until the device sends
    a report, and a quiet device hangs the caller forever.
    """
    flags = FILE_FLAG_OVERLAPPED if overlapped else 0
    h = kernel32.CreateFileW(path, GENERIC_READ | GENERIC_WRITE,
                             FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                             OPEN_EXISTING, flags, None)
    if not h or h == INVALID_HANDLE:
        # retry read-only
        h = kernel32.CreateFileW(path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                 None, OPEN_EXISTING, flags, None)
    if not h or h == INVALID_HANDLE:
        return None
    hid.HidD_SetNumInputBuffers(ctypes.c_void_p(h), 64)
    return h


def close(handle):
    if handle:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def caps(handle) -> HIDP_CAPS | None:
    pp = ctypes.c_void_p()
    if not hid.HidD_GetPreparsedData(ctypes.c_void_p(handle), ctypes.byref(pp)):
        return None
    try:
        c = HIDP_CAPS()
        if HidP.HidP_GetCaps(pp, ctypes.byref(c)) < 0:
            return None
        return c
    finally:
        hid.HidD_FreePreparsedData(pp)


def attrs(handle):
    a = HIDD_ATTRIBUTES()
    a.Size = ctypes.sizeof(a)
    if not hid.HidD_GetAttributes(ctypes.c_void_p(handle), ctypes.byref(a)):
        return None
    return a


def product_string(handle) -> str:
    buf = ctypes.create_unicode_buffer(128)
    if hid.HidD_GetProductString(ctypes.c_void_p(handle), buf,
                                 ctypes.sizeof(buf)):
        return buf.value
    return ""


def write(handle, data: bytes) -> bool:
    """Write a report. Uses overlapped I/O because open_path sets
    FILE_FLAG_OVERLAPPED -- passing NULL there fails with ERROR_INVALID_PARAMETER."""
    buf = ctypes.create_string_buffer(bytes(data), len(data))
    ov = OVERLAPPED()
    ev = kernel32.CreateEventW(None, True, False, None)
    ov.hEvent = ev
    n = wt.DWORD(0)
    ok = kernel32.WriteFile(ctypes.c_void_p(handle), buf, len(data),
                            ctypes.byref(n), ctypes.byref(ov))
    if not ok:
        err = ctypes.get_last_error()
        if err != 997:  # ERROR_IO_PENDING
            kernel32.CloseHandle(ev)
            return False
        if kernel32.WaitForSingleObject(ev, 2000) != 0:
            kernel32.CancelIo(ctypes.c_void_p(handle))
            kernel32.CloseHandle(ev)
            return False
        if not kernel32.GetOverlappedResult(ctypes.c_void_p(handle),
                                            ctypes.byref(ov),
                                            ctypes.byref(n), False):
            kernel32.CloseHandle(ev)
            return False
    kernel32.CloseHandle(ev)
    return n.value == len(data)


def read(handle, length: int, timeout_ms: int = 500) -> bytes | None:
    """Read one report, waiting at most `timeout_ms` for it.

    Opens a *fresh synchronous* handle each call. That is the only pattern
    that behaves correctly here: with FILE_FLAG_OVERLAPPED a timed-out read
    must be cancelled, and CancelIo also kills the request the next call would
    have received -- so notifications silently disappear.
    """
    return read_sync(handle, length, timeout_ms)


def read_sync(handle, length: int, timeout_ms: int = 500) -> bytes | None:
    """One ReadFile on a worker thread, honouring `timeout_ms`.

    `handle` must have been opened WITHOUT FILE_FLAG_OVERLAPPED.
    """
    import threading

    result = {}
    done = threading.Event()

    def worker():
        buf = ctypes.create_string_buffer(length)
        n = wt.DWORD(0)
        ok = kernel32.ReadFile(ctypes.c_void_p(handle), buf, length,
                               ctypes.byref(n), None)
        if ok and n.value:
            result["data"] = bytes(buf.raw[:n.value])
        done.set()

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    done.wait(timeout_ms / 1000.0)
    return result.get("data")


class ReportReader:
    """Background reader for an event stream.

    A HID++ device delivers notifications whenever it likes; polling with a
    short timeout loses them, and a timed-out overlapped read must be
    cancelled, which drops the next report too. So keep one blocking ReadFile
    permanently in flight on its own thread and hand finished reports to the
    consumer through a queue.
    """

    def __init__(self, handle, length: int = 64):
        import queue
        import threading

        self.handle = handle
        self.length = length
        self.queue = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._stop.is_set():
            buf = ctypes.create_string_buffer(self.length)
            n = wt.DWORD(0)
            ok = kernel32.ReadFile(ctypes.c_void_p(self.handle), buf,
                                   self.length, ctypes.byref(n), None)
            if self._stop.is_set():
                return
            if ok and n.value:
                self.queue.put(bytes(buf.raw[:n.value]))

    def get(self, timeout_ms: int = 200):
        import queue as _q

        try:
            return self.queue.get(timeout=timeout_ms / 1000.0)
        except _q.Empty:
            return None

    def stop(self):
        self._stop.set()
