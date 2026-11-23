"""Send a bounded Windows shortcut without changing the foreground window."""

import ctypes
import re
import sys
from ctypes import wintypes

from products.hub.automation.cancellation import current_cancellation
from products.hub.automation.models import TaskDefinition, TaskExecutionResult, TriggerEvent
from products.hub.automation.system_trigger_config import hotkey_codes


def shortcut_keys(shortcut: str) -> tuple[int, ...]:
    key = shortcut.strip().upper()
    if re.fullmatch(r"F[1-9]|F1[01]", key):
        return (0x6F + int(key[1:]),)
    _, modifiers, vk = hotkey_codes(shortcut)
    return (*(virtual_key for flag, virtual_key in ((2, 0x11), (1, 0x12), (4, 0x10))
              if flag & modifiers), vk)


class _KeyInput(ctypes.Structure):
    _fields_ = [("vk", wintypes.WORD), ("scan", wintypes.WORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]


class _MouseInput(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG), ("data", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]


class _InputPayload(ctypes.Union):
    # MOUSEINPUT supplies the union's native alignment and size even for keys.
    _fields_ = [("keyboard", _KeyInput), ("mouse", _MouseInput)]


class _Input(ctypes.Structure):
    _fields_ = [("kind", wintypes.DWORD), ("payload", _InputPayload)]


class WindowsKeyboard:
    supported = sys.platform == "win32"

    def send(self, events: tuple[tuple[int, bool], ...]) -> int:
        send_input = ctypes.windll.user32.SendInput
        send_input.argtypes = (wintypes.UINT, ctypes.POINTER(_Input), ctypes.c_int)
        send_input.restype = wintypes.UINT
        inputs = (_Input * len(events))()
        for entry, (key, released) in zip(inputs, events):
            entry.kind = 1  # INPUT_KEYBOARD
            entry.payload.keyboard = _KeyInput(key, 0, 0x0002 if released else 0, 0, 0)
        return send_input(len(inputs), inputs, ctypes.sizeof(_Input))

    def is_down(self, key: int) -> bool:
        return bool(ctypes.windll.user32.GetAsyncKeyState(key) & 0x8000)


class KeyboardPressTask:
    task_type = "core.keyboard_press"

    def __init__(self, keyboard=None):
        self.keyboard = keyboard if keyboard is not None else WindowsKeyboard()

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        def result(success, detail):
            return TaskExecutionResult(task.task_id, task.task_type, success, detail)

        if not self.keyboard.supported:
            return result(False, "Keyboard Press is available on Windows only.")
        try:
            keys = shortcut_keys(str(task.config.get("shortcut", "")))
        except ValueError as error:
            return result(False, str(error))
        cancellation = current_cancellation()
        if cancellation is not None and cancellation.cancelled:
            return result(False, "Keyboard Press cancelled.")
        try:
            # Never release keys that were already down before this task. Also
            # refuse stray physical modifiers that would change the shortcut.
            if any(self.keyboard.is_down(key) for key in set(keys) | {0x10, 0x11, 0x12, 0x5B, 0x5C}):
                return result(False, "Release the held keys before sending the shortcut.")
            events = (*( (key, False) for key in keys), *((key, True) for key in reversed(keys)))
            sent = self.keyboard.send(events)
        except (OSError, RuntimeError):
            return result(False, "Windows could not send the shortcut.")
        if sent == len(events):
            return result(True, "Sent shortcut to the foreground window.")
        pressed: list[int] = []
        for key, released in events[:sent]:
            if released:
                pressed.remove(key)
            else:
                pressed.append(key)
        if pressed:
            cleanup = tuple((key, True) for key in reversed(pressed))
            try:
                # Try each outstanding release if the batched cleanup is partial.
                released = self.keyboard.send(cleanup)
                for event in cleanup[released:]:
                    self.keyboard.send((event,))
            except (OSError, RuntimeError):
                pass
        return result(False, "Windows did not send the complete shortcut.")
