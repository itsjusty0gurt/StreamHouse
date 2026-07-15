from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from core.paths import user_data_root

if TYPE_CHECKING:
    from twitch.auth import TwitchToken


class _DataBlob(ctypes.Structure):
    _fields_ = (("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte)))


class TwitchTokenStore:
    """Store Twitch OAuth tokens encrypted for the current Windows user."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or user_data_root() / "twitch-token.dat"

    @classmethod
    def bot_account(cls) -> TwitchTokenStore:
        """Return the independent encrypted store for Sally's chat identity."""
        return cls(user_data_root() / "twitch-bot-token.dat")

    @staticmethod
    def _blob(data: bytes) -> tuple[_DataBlob, object]:
        buffer = ctypes.create_string_buffer(data)
        blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
        return blob, buffer

    @staticmethod
    def _protect(data: bytes) -> bytes:
        if os.name != "nt":
            raise OSError("Secure Twitch token storage requires Windows.")
        source, source_buffer = TwitchTokenStore._blob(data)
        output = _DataBlob()
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        # Machine-bound DPAPI remains decryptable when Sally is launched from
        # different desktop process contexts. The file itself remains inside
        # the current user's LocalAppData directory and inherits its ACL.
        cryptprotect_local_machine = 0x4
        if not crypt32.CryptProtectData(
            ctypes.byref(source), "Sally AI Twitch token", None, None, None,
            cryptprotect_local_machine,
            ctypes.byref(output),
        ):
            raise ctypes.WinError()
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            kernel32.LocalFree(output.pbData)
            del source_buffer

    @staticmethod
    def _unprotect(data: bytes) -> bytes:
        if os.name != "nt":
            raise OSError("Secure Twitch token storage requires Windows.")
        source, source_buffer = TwitchTokenStore._blob(data)
        output = _DataBlob()
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        if not crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, 0,
            ctypes.byref(output),
        ):
            raise ctypes.WinError()
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            kernel32.LocalFree(output.pbData)
            del source_buffer

    def load(self) -> TwitchToken | None:
        if not self.path.exists():
            return None
        from twitch.auth import TwitchToken

        values = json.loads(self._unprotect(self.path.read_bytes()).decode("utf-8"))
        return TwitchToken.from_dict(values)

    def save(self, token: TwitchToken) -> None:
        payload = json.dumps(asdict(token), separators=(",", ":")).encode("utf-8")
        protected = self._protect(payload)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_bytes(protected)
        temporary.replace(self.path)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
