from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
from threading import RLock
from typing import Iterator, Mapping, Sequence


_BUNDLE_PATH_VARIABLES = frozenset(
    {
        "PATH",
        "QT_PLUGIN_PATH",
        "QT_QPA_PLATFORM_PLUGIN_PATH",
        "QML2_IMPORT_PATH",
        "QML_IMPORT_PATH",
        "QTWEBENGINEPROCESS_PATH",
        "QTWEBENGINE_RESOURCES_PATH",
        "QTWEBENGINE_LOCALES_PATH",
    }
)
_WINDOWS_DLL_DIRECTORY_LOCK = RLock()


def external_process_environment(
    environment: Mapping[str, str] | None = None,
    *,
    frozen: bool | None = None,
    bundle_root: str | os.PathLike[str] | None = None,
) -> dict[str, str]:
    """Return an environment suitable for a system-installed application.

    PyInstaller's PySide6 run-time hook points Qt and QML variables at the
    bundle and prepends the bundle root to ``PATH``. Those values are required
    by Hub and its bundled helpers, but make an unrelated Qt application load
    Hub's plug-ins. Preserve the normal user/system environment and remove
    only bundle-anchored entries plus PyInstaller's private child state.
    """

    result = dict(os.environ if environment is None else environment)
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if not is_frozen:
        return result

    root_value = bundle_root
    if root_value is None:
        root_value = getattr(sys, "_MEIPASS", "")
    if not root_value:
        return result
    root = Path(root_value)

    for key in tuple(result):
        normalized_key = key.upper()
        if normalized_key in _BUNDLE_PATH_VARIABLES:
            cleaned = _without_bundle_paths(result[key], root)
            if cleaned:
                result[key] = cleaned
            else:
                result.pop(key, None)
        elif normalized_key.startswith("_PYI_") or normalized_key in {
            "_MEIPASS2",
            "PYINSTALLER_RESET_ENVIRONMENT",
        }:
            result.pop(key, None)
    return result


def start_external_process(
    command: Sequence[str],
    *,
    cwd: str | os.PathLike[str] | None = None,
    startupinfo: subprocess.STARTUPINFO | None = None,
) -> subprocess.Popen[bytes]:
    """Start an external application without Hub's frozen runtime search paths."""

    environment = external_process_environment()
    with _standard_windows_dll_search_path():
        return subprocess.Popen(
            list(command),
            cwd=cwd,
            startupinfo=startupinfo,
            env=environment,
        )


def _without_bundle_paths(value: str, bundle_root: Path) -> str:
    return os.pathsep.join(
        entry
        for entry in value.split(os.pathsep)
        if entry and not _is_in_bundle(entry, bundle_root)
    )


def _is_in_bundle(value: str, bundle_root: Path) -> bool:
    try:
        path = Path(value.strip('"')).resolve(strict=False)
        root = bundle_root.resolve(strict=False)
        return os.path.commonpath((str(path), str(root))).casefold() == str(root).casefold()
    except (OSError, ValueError):
        return False


@contextmanager
def _standard_windows_dll_search_path() -> Iterator[None]:
    """Temporarily undo PyInstaller's inherited Windows DLL directory.

    The bootloader uses ``SetDllDirectoryW(bundle_root)`` and Windows passes
    that setting to child processes. Environment sanitization alone therefore
    cannot restore a normal executable launch. Serialize this very short
    process-creation window and restore Hub's exact prior directory afterward.
    """

    if sys.platform != "win32" or not bool(getattr(sys, "frozen", False)):
        yield
        return

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_dll_directory = kernel32.GetDllDirectoryW
    get_dll_directory.argtypes = (wintypes.DWORD, wintypes.LPWSTR)
    get_dll_directory.restype = wintypes.DWORD
    set_dll_directory = kernel32.SetDllDirectoryW
    set_dll_directory.argtypes = (wintypes.LPCWSTR,)
    set_dll_directory.restype = wintypes.BOOL

    with _WINDOWS_DLL_DIRECTORY_LOCK:
        buffer = ctypes.create_unicode_buffer(32_768)
        length = get_dll_directory(len(buffer), buffer)
        previous = buffer.value if length else None
        if not set_dll_directory(None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield
        finally:
            if not set_dll_directory(previous):
                raise ctypes.WinError(ctypes.get_last_error())
