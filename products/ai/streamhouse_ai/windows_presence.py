from __future__ import annotations

import sys

from shared.streamhouse_shared.presence_protocol import (
    HUB_WINDOW_TITLE,
    LEGACY_HUB_WINDOW_TITLE,
    STREAMHOUSE_AI_PRESENCE_MESSAGE,
)


class WindowsHubPresenceNotifier:
    """Uses Streamhouse Hub's event loop; no Hub polling is required."""

    def __init__(self, port: int, protocol_version: int) -> None:
        self.port = port
        self.protocol_version = protocol_version
        self.connected = False
        self._window_handle = 0
        self._message_id = 0
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            self._user32 = ctypes.windll.user32
            self._user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
            self._user32.RegisterWindowMessageW.restype = wintypes.UINT
            self._user32.FindWindowW.argtypes = [
                wintypes.LPCWSTR,
                wintypes.LPCWSTR,
            ]
            self._user32.FindWindowW.restype = wintypes.HWND
            self._user32.IsWindow.argtypes = [wintypes.HWND]
            self._user32.IsWindow.restype = wintypes.BOOL
            self._user32.PostMessageW.argtypes = [
                wintypes.HWND,
                wintypes.UINT,
                wintypes.WPARAM,
                wintypes.LPARAM,
            ]
            self._user32.PostMessageW.restype = wintypes.BOOL
            self._message_id = int(
                self._user32.RegisterWindowMessageW(
                    STREAMHOUSE_AI_PRESENCE_MESSAGE
                )
            )
        else:
            self._user32 = None

    def refresh(self) -> bool:
        if self._user32 is None:
            self.connected = False
            return False
        if self._window_handle and self._user32.IsWindow(self._window_handle):
            self.connected = True
            return True
        self._window_handle = int(
            self._user32.FindWindowW(None, HUB_WINDOW_TITLE)
            or self._user32.FindWindowW(None, LEGACY_HUB_WINDOW_TITLE)
            or 0
        )
        if not self._window_handle:
            self.connected = False
            return False
        delivered = bool(
            self._user32.PostMessageW(
                self._window_handle,
                self._message_id,
                self.protocol_version,
                self.port,
            )
        )
        self.connected = delivered
        return delivered

    def disconnect(self) -> None:
        if (
            self._user32 is not None
            and self._window_handle
            and self._user32.IsWindow(self._window_handle)
        ):
            self._user32.PostMessageW(
                self._window_handle,
                self._message_id,
                self.protocol_version,
                0,
            )
        self.connected = False
        self._window_handle = 0
