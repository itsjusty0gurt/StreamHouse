"""Validation and wall-clock calculations for Core system triggers."""

from __future__ import annotations

import re
from os.path import normpath
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from PySide6.QtCore import QDateTime, QTime, QTimeZone


SYSTEM_TRIGGER_TYPES = {
    "scheduled_time": "Scheduled Time / Cron",
    "hotkey": "Hotkey",
    "process_started": "Process Started",
    "process_stopped": "Process Stopped",
    "file_watcher": "File Watcher",
}
SYSTEM_VARIABLE_PREFIXES = {
    "scheduled_time": "core.schedule.",
    "hotkey": "core.hotkey.",
    "process_started": "core.process.",
    "process_stopped": "core.process.",
    "file_watcher": "core.file.",
}
FILE_EVENTS = {"created": "Created", "modified": "Modified", "deleted": "Deleted"}
_MODIFIERS = {"Ctrl": 2, "Alt": 1, "Shift": 4}
_MONTHS = dict(zip("jan feb mar apr may jun jul aug sep oct nov dec".split(), range(1, 13)))
_DAYS = dict(zip("sun mon tue wed thu fri sat".split(), range(7)))


def local_schedule_timezone() -> QTimeZone:
    """Use OS local-time rules even when Qt cannot map a Windows zone ID."""
    return QTimeZone(QTimeZone.Initialization.LocalTime)


def _cron_field(field: str, low: int, high: int, names: Mapping[str, int]) -> set[int]:
    result: set[int] = set()

    def number(value: str) -> int:
        if value in names:
            return names[value]
        if not re.fullmatch(r"[0-9]{1,3}", value):
            raise ValueError("Use numbers, names, ranges, lists, or steps in cron fields.")
        return int(value)

    for part in field.split(","):
        base, separator, step_text = part.partition("/")
        if separator and not re.fullmatch(r"[0-9]{1,3}", step_text):
            raise ValueError("A cron step must be a positive number.")
        step = int(step_text) if separator else 1
        if base == "*":
            start, end = low, high
        elif "-" in base:
            first, _, last = base.partition("-")
            start, end = number(first), number(last)
        else:
            if separator:
                raise ValueError("Apply cron steps to * or a range.")
            start = end = number(base)
        if not low <= start <= end <= high or step < 1:
            raise ValueError(f"Cron field must be in the range {low}–{high}, with a positive step.")
        result.update(range(start, end + 1, step))
    return result


@dataclass(frozen=True)
class CronSchedule:
    expression: str
    times: tuple[tuple[int, int], ...]
    dates: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    match_both_days: bool

    @classmethod
    def parse(cls, expression: str) -> CronSchedule:
        if len(expression) > 512:
            raise ValueError("The cron expression is too long.")
        fields = expression.casefold().split()
        if len(fields) != 5:
            raise ValueError("Enter five cron fields: minute hour day-of-month month day-of-week.")
        minute, hour, day, month, weekday = fields
        minutes = _cron_field(minute, 0, 59, {})
        hours = _cron_field(hour, 0, 23, {})
        dates = _cron_field(day, 1, 31, {})
        months = _cron_field(month, 1, 12, _MONTHS)
        weekdays = {d % 7 for d in _cron_field(weekday, 0, 7, _DAYS)}
        both = day.startswith("*") or weekday.startswith("*")
        limit = {2: 29, 4: 30, 6: 30, 9: 30, 11: 30}
        if both and min(dates) > max(limit.get(m, 31) for m in months):
            raise ValueError("This schedule cannot occur on the selected dates.")
        return cls(
            " ".join(fields), tuple((h, m) for h in sorted(hours) for m in sorted(minutes)),
            frozenset(dates), frozenset(months), frozenset(weekdays), both,
        )

    def next_after(self, after: int, zone: QTimeZone) -> int | None:
        """First instant after epoch milliseconds; skip gaps and use the first fold."""
        local = QDateTime.fromMSecsSinceEpoch(after, zone)
        date = local.date()
        # A leap-day schedule can span eight years across a non-leap century.
        for offset in range(366 * 8 + 2):
            month, dom, dow = date.month(), date.day(), date.dayOfWeek() % 7
            d, w = dom in self.dates, dow in self.weekdays
            if month in self.months and ((d and w) if self.match_both_days else (d or w)):
                for hour, minute in self.times:
                    wall = QTime(hour, minute)
                    if offset == 0 and wall <= local.time():
                        continue
                    stamp = QDateTime(date, wall, zone, QDateTime.TransitionResolution.PreferBefore)
                    if stamp.isValid() and stamp.date() == date and stamp.time() == wall:
                        epoch = stamp.toMSecsSinceEpoch()
                        if epoch > after:
                            return epoch
            date = date.addDays(1)
        return None


def hotkey_codes(text: str) -> tuple[str, int, int]:
    parts = text.split("+")
    mods = {part.strip().casefold() for part in parts[:-1]}
    if len(mods) != len(parts) - 1 or not mods <= {"ctrl", "alt", "shift"}:
        raise ValueError("Use Ctrl, Alt, and Shift to configure a Windows hotkey.")
    key = parts[-1].strip().upper()
    if not (mods & {"ctrl", "alt"} or (mods and re.fullmatch(r"F[1-9]|F1[01]", key))):
        raise ValueError("Use Ctrl or Alt, or Shift with a function key.")
    if re.fullmatch(r"[A-Z0-9]", key):
        vk = ord(key)
    elif re.fullmatch(r"F[1-9]|F1[01]", key):
        vk = 0x6F + int(key[1:])
    else:
        raise ValueError("Choose a letter, digit, or F1–F11.")
    if mods == {"alt"} and key == "F4":
        raise ValueError("Alt+F4 is reserved for closing windows.")
    selected = [name for name in _MODIFIERS if name.casefold() in mods]
    return "+".join((*selected, key)), sum(_MODIFIERS[name] for name in selected), vk


def system_config(event_type: str, config: Mapping[str, object]) -> dict[str, str]:
    if event_type not in SYSTEM_TRIGGER_TYPES:
        return {}
    if not isinstance(config, Mapping):
        raise ValueError("System trigger configuration must be an object.")
    if event_type == "scheduled_time":
        schedule = CronSchedule.parse(str(config.get("expression", "")))
        mode = str(config.get("mode", "cron"))
        if mode not in {"simple", "cron"}:
            raise ValueError("Choose Simple Schedule or Advanced Cron.")
        if mode == "simple":
            minute, hour, dom, month, weekdays = schedule.expression.split()
            if (not minute.isdecimal() or not hour.isdecimal() or dom != "*" or month != "*"
                    or not re.fullmatch(r"\*|[0-6](?:,[0-6])*", weekdays)):
                raise ValueError("A simple schedule requires a time and optional weekdays.")
        return {"mode": mode, "expression": schedule.expression}
    if event_type == "hotkey":
        shortcut, _, _ = hotkey_codes(str(config.get("shortcut", "")))
        return {"shortcut": shortcut}
    if event_type.startswith("process_"):
        name = str(config.get("name", "")).strip()
        if not re.fullmatch(r'[^\\/:*?"<>|\x00-\x1f]{1,251}\.exe', name, flags=re.IGNORECASE):
            raise ValueError("Enter an executable name such as notepad.exe, without folders.")
        return {"name": name}
    path = str(config.get("path", "")).strip()
    event = str(config.get("event", ""))
    kind = str(config.get("kind", "file"))
    if not path or "\x00" in path or not Path(path).is_absolute() or Path(path).anchor.startswith("\\\\"):
        raise ValueError("Choose an absolute path on a local drive.")
    if event not in FILE_EVENTS or kind not in {"file", "folder"}:
        raise ValueError("Choose a file or folder and a change to watch.")
    return {"path": normpath(path), "event": event, "kind": kind}


def system_trigger_summary(event_type: str, config: Mapping[str, str]) -> str:
    if event_type == "scheduled_time":
        if config["mode"] == "simple":
            minute, hour, _, _, weekdays = config["expression"].split()
            days = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
            repeat = (
                "Daily" if weekdays == "*"
                else ", ".join(days[int(day)] for day in weekdays.split(","))
            )
            return f"{repeat} at {int(hour):02d}:{int(minute):02d} · local time"
        return config["expression"]
    if event_type == "hotkey":
        return config["shortcut"]
    if event_type.startswith("process_"):
        return config["name"]
    return f'{FILE_EVENTS[config["event"]]} · {config["path"]}'
