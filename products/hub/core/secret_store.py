from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

from shared.streamhouse_runtime.json_store import atomic_write_bytes


class _DataBlob(ctypes.Structure):
    _fields_ = (("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte)))


class SecretStore:
    """Small Windows-DPAPI encrypted secret file."""

    DPAPI_FLAGS = 0x1  # CRYPTPROTECT_UI_FORBIDDEN; current-user scope.

    def __init__(
        self,
        path: Path,
        description: str = "Streamhouse secret",
    ) -> None:
        self.path = path
        self.description = description

    @staticmethod
    def _blob(data: bytes) -> tuple[_DataBlob, object]:
        buffer = ctypes.create_string_buffer(data)
        return (
            _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))),
            buffer,
        )

    def save(self, value: str) -> None:
        if not value:
            self.clear()
            return
        if os.name != "nt":
            raise OSError("Secure secret storage requires Windows.")
        source, source_buffer = self._blob(value.encode("utf-8"))
        output = _DataBlob()
        # UI-forbidden, current-user DPAPI. Do not use LOCAL_MACHINE scope:
        # LocalAppData ACLs are useful defense, but the encrypted payload itself
        # must remain bound to the Windows account that owns it.
        if not ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(source), self.description, None, None, None, self.DPAPI_FLAGS,
            ctypes.byref(output),
        ):
            raise ctypes.WinError()
        try:
            encrypted = ctypes.string_at(output.pbData, output.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(output.pbData)
            del source_buffer
        atomic_write_bytes(self.path, encrypted)

    def load(self) -> str:
        if not self.path.exists():
            return ""
        if os.name != "nt":
            raise OSError("Secure secret storage requires Windows.")
        source, source_buffer = self._blob(self.path.read_bytes())
        output = _DataBlob()
        if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, self.DPAPI_FLAGS,
            ctypes.byref(output),
        ):
            raise ctypes.WinError()
        try:
            return ctypes.string_at(output.pbData, output.cbData).decode("utf-8")
        finally:
            ctypes.windll.kernel32.LocalFree(output.pbData)
            del source_buffer

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
