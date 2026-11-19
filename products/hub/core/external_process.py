from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
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


@dataclass(frozen=True)
class StartedExternalProcess:
    """Minimal result for an independently launched Windows application."""

    pid: int


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
) -> subprocess.Popen[bytes] | StartedExternalProcess:
    """Start an external application outside Hub's packaged process tree.

    OBS Application Audio Capture includes the selected process's descendants.
    On Windows, use the interactive desktop shell as the supported alternate
    parent so an unrelated application is not classified as part of Hub solely
    because an Automation task launched it. Other platforms keep the normal
    non-blocking ``Popen`` path.
    """

    environment = external_process_environment()
    with _standard_windows_dll_search_path():
        if sys.platform == "win32":
            return _start_independent_windows_process(
                command,
                cwd=cwd,
                startupinfo=startupinfo,
                environment=environment,
            )
        return subprocess.Popen(
            list(command),
            cwd=cwd,
            startupinfo=startupinfo,
            env=environment,
        )


def _start_independent_windows_process(
    command: Sequence[str],
    *,
    cwd: str | os.PathLike[str] | None,
    startupinfo: subprocess.STARTUPINFO | None,
    environment: Mapping[str, str],
) -> StartedExternalProcess:
    """Create a process whose Windows parent is the interactive shell.

    ``DETACHED_PROCESS`` and ``CREATE_NEW_PROCESS_GROUP`` affect console
    attachment and control signals, not process ancestry. Windows exposes the
    parent override explicitly through ``PROC_THREAD_ATTRIBUTE_PARENT_PROCESS``.
    """

    import ctypes
    from ctypes import wintypes

    process_create_process = 0x0080
    proc_thread_attribute_parent_process = 0x00020000
    create_unicode_environment = 0x00000400
    extended_startupinfo_present = 0x00080000

    class StartupInfoW(ctypes.Structure):
        _fields_ = (
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(wintypes.BYTE)),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        )

    class StartupInfoExW(ctypes.Structure):
        _fields_ = (
            ("StartupInfo", StartupInfoW),
            ("lpAttributeList", ctypes.c_void_p),
        )

    class ProcessInformation(ctypes.Structure):
        _fields_ = (
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        )

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    get_shell_window = user32.GetShellWindow
    get_shell_window.argtypes = ()
    get_shell_window.restype = wintypes.HWND
    get_window_thread_process_id = user32.GetWindowThreadProcessId
    get_window_thread_process_id.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    get_window_thread_process_id.restype = wintypes.DWORD
    open_process = kernel32.OpenProcess
    open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_process.restype = wintypes.HANDLE
    initialize_attributes = kernel32.InitializeProcThreadAttributeList
    initialize_attributes.argtypes = (
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_size_t),
    )
    initialize_attributes.restype = wintypes.BOOL
    update_attributes = kernel32.UpdateProcThreadAttribute
    update_attributes.argtypes = (
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_void_p,
    )
    update_attributes.restype = wintypes.BOOL
    delete_attributes = kernel32.DeleteProcThreadAttributeList
    delete_attributes.argtypes = (ctypes.c_void_p,)
    delete_attributes.restype = None
    create_process = kernel32.CreateProcessW
    create_process.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(StartupInfoExW),
        ctypes.POINTER(ProcessInformation),
    )
    create_process.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    shell_window = get_shell_window()
    if not shell_window:
        raise OSError("The Windows desktop shell is unavailable.")
    shell_pid = wintypes.DWORD()
    if not get_window_thread_process_id(shell_window, ctypes.byref(shell_pid)):
        raise ctypes.WinError(ctypes.get_last_error())
    shell_handle = open_process(process_create_process, False, shell_pid.value)
    if not shell_handle:
        raise ctypes.WinError(ctypes.get_last_error())

    attribute_size = ctypes.c_size_t()
    initialize_attributes(None, 1, 0, ctypes.byref(attribute_size))
    attribute_buffer = ctypes.create_string_buffer(attribute_size.value)
    attribute_list = ctypes.cast(attribute_buffer, ctypes.c_void_p)
    attributes_initialized = False
    process_info = ProcessInformation()
    try:
        if not initialize_attributes(attribute_list, 1, 0, ctypes.byref(attribute_size)):
            raise ctypes.WinError(ctypes.get_last_error())
        attributes_initialized = True
        parent_handle = wintypes.HANDLE(shell_handle)
        if not update_attributes(
            attribute_list,
            0,
            proc_thread_attribute_parent_process,
            ctypes.byref(parent_handle),
            ctypes.sizeof(parent_handle),
            None,
            None,
        ):
            raise ctypes.WinError(ctypes.get_last_error())

        startup = StartupInfoExW()
        startup.StartupInfo.cb = ctypes.sizeof(startup)
        if startupinfo is not None:
            startup.StartupInfo.dwFlags = startupinfo.dwFlags
            startup.StartupInfo.wShowWindow = startupinfo.wShowWindow
        startup.lpAttributeList = attribute_list

        command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(list(command)))
        environment_entries = sorted(environment.items(), key=lambda item: item[0].casefold())
        environment_block = ctypes.create_unicode_buffer(
            "\0".join(f"{key}={value}" for key, value in environment_entries) + "\0\0"
        )
        working_directory = os.fspath(cwd) if cwd is not None else None
        if not create_process(
            os.fspath(command[0]),
            command_line,
            None,
            None,
            False,
            create_unicode_environment | extended_startupinfo_present,
            ctypes.cast(environment_block, ctypes.c_void_p),
            working_directory,
            ctypes.byref(startup),
            ctypes.byref(process_info),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return StartedExternalProcess(int(process_info.dwProcessId))
    finally:
        if process_info.hThread:
            close_handle(process_info.hThread)
        if process_info.hProcess:
            close_handle(process_info.hProcess)
        if attributes_initialized:
            delete_attributes(attribute_list)
        close_handle(shell_handle)


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
