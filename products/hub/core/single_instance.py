from __future__ import annotations

import ctypes
import hashlib
import os
import threading
from ctypes import wintypes
from pathlib import Path
from typing import Protocol


class InstanceLockError(RuntimeError):
    """Raised when exclusive Hub ownership cannot be checked safely."""


class _LockBackend(Protocol):
    def try_acquire(self) -> bool: ...

    def release(self) -> None: ...


def _data_root_fingerprint(data_root: Path) -> str:
    canonical = os.path.normcase(str(data_root.expanduser().resolve()))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


class _WindowsMutexBackend:
    WAIT_OBJECT_0 = 0x00000000
    WAIT_ABANDONED = 0x00000080
    WAIT_TIMEOUT = 0x00000102
    WAIT_FAILED = 0xFFFFFFFF

    def __init__(self, name: str) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        )
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = (
            wintypes.HANDLE,
            wintypes.DWORD,
        )
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.ReleaseMutex.argtypes = (wintypes.HANDLE,)
        kernel32.ReleaseMutex.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        self._kernel32 = kernel32
        self._name = name
        self._handle: int | None = None

    def try_acquire(self) -> bool:
        handle = self._kernel32.CreateMutexW(None, False, self._name)
        if not handle:
            raise OSError(ctypes.get_last_error(), "Could not create Hub mutex")

        result = self._kernel32.WaitForSingleObject(handle, 0)
        if result in (self.WAIT_OBJECT_0, self.WAIT_ABANDONED):
            self._handle = handle
            return True

        self._kernel32.CloseHandle(handle)
        if result == self.WAIT_TIMEOUT:
            return False
        if result == self.WAIT_FAILED:
            raise OSError(ctypes.get_last_error(), "Could not wait for Hub mutex")
        raise OSError(f"Unexpected Hub mutex wait result: {result}")

    def release(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            self._kernel32.ReleaseMutex(handle)
        finally:
            self._kernel32.CloseHandle(handle)


class _QtLockFileBackend:
    def __init__(self, path: Path) -> None:
        from PySide6.QtCore import QLockFile

        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = QLockFile(str(path))
        # This is a long-running application lock. QLockFile will still remove a
        # lock whose recorded process no longer exists, but never based on age.
        self._lock.setStaleLockTime(0)

    def try_acquire(self) -> bool:
        from PySide6.QtCore import QLockFile

        if self._lock.tryLock(0):
            return True
        error = self._lock.error()
        if error == QLockFile.LockError.LockFailedError:
            return False
        raise OSError(f"Could not acquire Hub lock file: {error}")

    def release(self) -> None:
        self._lock.unlock()


class HubInstanceLock:
    """Own exclusive writable access to one resolved Hub data root."""

    _process_guard = threading.Lock()
    _process_owned: set[str] = set()

    def __init__(
        self,
        data_root: Path,
        *,
        backend: _LockBackend | None = None,
    ) -> None:
        self.data_root = data_root.expanduser().resolve()
        fingerprint = _data_root_fingerprint(self.data_root)
        self.identity = f"StreamhouseHub-{fingerprint}"
        if backend is None:
            if os.name == "nt":
                backend = _WindowsMutexBackend(f"Global\\{self.identity}")
            else:
                backend = _QtLockFileBackend(
                    self.data_root / "runtime" / "hub-instance.lock"
                )
        self._backend = backend
        self._acquired = False

    @property
    def acquired(self) -> bool:
        return self._acquired

    def try_acquire(self) -> bool:
        if self._acquired:
            return True
        with self._process_guard:
            if self.identity in self._process_owned:
                return False
            self._process_owned.add(self.identity)
        try:
            acquired = self._backend.try_acquire()
        except Exception as exc:
            with self._process_guard:
                self._process_owned.discard(self.identity)
            raise InstanceLockError(str(exc)) from exc
        if not acquired:
            with self._process_guard:
                self._process_owned.discard(self.identity)
            return False
        self._acquired = True
        return True

    def release(self) -> None:
        if not self._acquired:
            return
        try:
            self._backend.release()
        finally:
            self._acquired = False
            with self._process_guard:
                self._process_owned.discard(self.identity)

    def __enter__(self) -> HubInstanceLock:
        if not self.try_acquire():
            raise InstanceLockError("Streamhouse Hub is already running")
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()
