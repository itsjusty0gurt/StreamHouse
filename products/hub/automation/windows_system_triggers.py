"""Small Win32 boundaries for registered shortcuts and process snapshots."""

import ctypes
import sys
from ctypes import wintypes


class WindowsHotkeys:
    supported = sys.platform == "win32"

    def __init__(self, window_id):
        self._window_id = window_id
        self._registered: dict[int, int] = {}

    def register(self, identifier: int, modifiers: int, key: int) -> bool:
        if not self.supported:
            return False
        hwnd = self._window_id()
        if not ctypes.windll.user32.RegisterHotKey(
            wintypes.HWND(hwnd), identifier, modifiers | 0x4000, key,
        ):
            return False
        self._registered[identifier] = hwnd
        return True

    def unregister(self, identifier: int) -> None:
        hwnd = self._registered.pop(identifier, None)
        if hwnd is not None:
            ctypes.windll.user32.UnregisterHotKey(wintypes.HWND(hwnd), identifier)


class _ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD), ("usage", wintypes.DWORD),
        ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
        ("module_id", wintypes.DWORD), ("threads", wintypes.DWORD),
        ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
        ("flags", wintypes.DWORD), ("executable", wintypes.WCHAR * 260),
    ]


def process_snapshot() -> dict[int, str]:
    if sys.platform != "win32":
        raise OSError("Process triggers are available on Windows.")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    capture = kernel.CreateToolhelp32Snapshot
    capture.argtypes = (wintypes.DWORD, wintypes.DWORD)
    capture.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    first, advance = kernel.Process32FirstW, kernel.Process32NextW
    for read in (first, advance):
        read.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ProcessEntry))
        read.restype = wintypes.BOOL
    handle = capture(0x00000002, 0)  # TH32CS_SNAPPROCESS
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        result = {}
        entry = _ProcessEntry()
        entry.size = ctypes.sizeof(entry)
        read = first
        while read(handle, ctypes.byref(entry)):
            if entry.pid:
                result[entry.pid] = entry.executable
            read = advance
        if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
            raise ctypes.WinError(ctypes.get_last_error())
        return result
    finally:
        close(handle)
