"""Qt-owned scheduling and event delivery, with filesystem/process reads off-thread."""

from __future__ import annotations

import itertools
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import (
    QDateTime, QFileSystemWatcher, QObject, QRunnable, QThreadPool,
    QTimeZone, QTimer, Qt, Signal, Slot,
)

from products.hub.automation.core_triggers import CoreAutomationTrigger, CoreTriggerStore
from products.hub.automation.models import TriggerEvent
from products.hub.automation.system_trigger_config import (
    SYSTEM_TRIGGER_TYPES, CronSchedule, hotkey_codes,
)
from products.hub.automation.windows_system_triggers import process_snapshot
from shared.streamhouse_runtime.logger import Logger


def process_changes(before: dict[int, str], after: dict[int, str], name: str, started: bool):
    first, other = (after, before) if started else (before, after)
    for pid, executable in sorted(first.items()):
        if executable.casefold() == name.casefold() and other.get(pid, "").casefold() != name.casefold():
            yield pid, executable


@dataclass(frozen=True)
class FileState:
    signature: tuple[int, ...] | None
    watches: tuple[str, ...]


def file_state(path: Path, kind: str) -> FileState:
    def metadata(candidate):
        try:
            return candidate.stat()
        except (FileNotFoundError, NotADirectoryError):
            return None

    item = metadata(path)
    present = item is not None and (
        stat.S_ISDIR(item.st_mode) if kind == "folder" else stat.S_ISREG(item.st_mode)
    )
    watches = [str(path)] if present else []
    for ancestor in path.parents:
        meta = metadata(ancestor)
        if meta is not None and stat.S_ISDIR(meta.st_mode):
            watches.append(str(ancestor))
            break
    signature = (
        (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
        if present and item is not None else None
    )
    return FileState(signature, tuple(watches))


def file_change(before: FileState, after: FileState) -> str | None:
    if before.signature == after.signature:
        return None
    if before.signature is None:
        return "created"
    if after.signature is None:
        return "deleted"
    return "modified"


class _ScanResult(QObject):
    ready = Signal(object, object, object)


class _Scan(QRunnable):
    def __init__(self, process_targets, file_targets, snapshot, done):
        super().__init__()
        self.process_targets = process_targets
        self.file_targets = file_targets
        self.snapshot = snapshot
        self.result = _ScanResult()
        self.result.ready.connect(done, Qt.ConnectionType.QueuedConnection)

    def run(self):
        processes = None
        if self.process_targets:
            try:
                processes = self.snapshot()
            except OSError:
                pass  # A failed snapshot is not a system-wide process exit.
        files = {}
        for identifier, (trigger, generation) in self.file_targets.items():
            try:
                config = trigger.system_config
                files[identifier] = (
                    generation, file_state(Path(config["path"]), config["kind"])
                )
            except OSError:
                continue  # Inaccessible is not deleted.
        self.result.ready.emit(self.process_targets, processes, files)


class SystemTriggerRuntime(QObject):
    _store_changed = Signal()
    _hotkey_pressed = Signal(int)
    _clock_changed = Signal()
    _trigger_ready = Signal(object, object)

    def __init__(
        self, store: CoreTriggerStore, publish, hotkeys, parent=None, *,
        clock=lambda: time.time_ns() // 1_000_000,
        zone=QTimeZone.systemTimeZone, snapshot=process_snapshot, watcher_factory=QFileSystemWatcher,
    ):
        super().__init__(parent)
        self.store, self.publish, self.hotkeys = store, publish, hotkeys
        self.clock, self.zone, self.snapshot = clock, zone, snapshot
        self.process_supported = sys.platform == "win32" or snapshot is not process_snapshot
        self.running = False
        self.recording = False
        self.targets: dict[str, tuple[CoreAutomationTrigger, int]] = {}
        self.sequence = itertools.count(1)
        self.shortcut_ids: dict[str, int] = {}
        self.shortcut_failures: set[str] = set()
        self.schedules: dict[str, tuple[CronSchedule, int | None]] = {}
        self.last_due: dict[str, int] = {}
        self.zone_id = b""
        self.process_baselines: dict[str, dict[int, str]] = {}
        self.file_baselines: dict[str, FileState] = {}
        self._scan = None
        self._files_dirty = False
        self.schedule_timer = QTimer(self, singleShot=True)
        self.schedule_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.schedule_timer.timeout.connect(self.check_schedules)
        self.process_timer = QTimer(self, interval=1_000)
        self.process_timer.timeout.connect(self.request_scan)
        self.file_timer = QTimer(self, interval=150, singleShot=True)
        self.file_timer.timeout.connect(self.request_scan)
        self.watcher = watcher_factory(self)
        self.watcher.fileChanged.connect(self._file_changed)
        self.watcher.directoryChanged.connect(self._file_changed)
        self._store_changed.connect(self.synchronize, Qt.ConnectionType.QueuedConnection)
        self._store_callback = self._store_changed.emit
        self._hotkey_pressed.connect(self._deliver_hotkey, Qt.ConnectionType.QueuedConnection)
        self._clock_changed.connect(self.replan, Qt.ConnectionType.QueuedConnection)
        self._trigger_ready.connect(self._deliver, Qt.ConnectionType.QueuedConnection)

    def start(self):
        if not self.running:
            self.running = True
            self.store.subscribe_changes(self._store_callback)
            self.synchronize()

    def stop(self):
        self.running = False
        self.store.unsubscribe_changes(self._store_callback)
        for timer in (self.schedule_timer, self.process_timer, self.file_timer):
            timer.stop()
        self._clear_hotkeys()
        paths = self.watcher.files() + self.watcher.directories()
        if paths:
            self.watcher.removePaths(paths)
        self.targets.clear()
        self.schedules.clear()
        self.process_baselines.clear()
        self.file_baselines.clear()
        self.last_due.clear()
        self.shortcut_failures.clear()
        self._files_dirty = False

    def _clear_hotkeys(self):
        for identifier in self.shortcut_ids.values():
            self.hotkeys.unregister(identifier)
        self.shortcut_ids.clear()

    @Slot(bool)
    def set_recording(self, recording: bool):
        if self.recording != recording:
            self.recording = recording
            self._clear_hotkeys()
            if self.running and not recording:
                self.synchronize()

    def _register_hotkeys(self):
        if self.recording:
            return
        for key, (trigger, _) in self.targets.items():
            if trigger.event_type != "hotkey" or key in self.shortcut_ids:
                continue
            text, modifiers, vk = hotkey_codes(trigger.system_config["shortcut"])
            identifier = next(self.sequence)
            if identifier <= 0xBFFF and self.hotkeys.register(identifier, modifiers, vk):
                self.shortcut_ids[key] = identifier
                self.shortcut_failures.discard(key)
            elif key not in self.shortcut_failures:
                self.shortcut_failures.add(key)
                reason = "already in use or reserved" if self.hotkeys.supported else "not supported on this platform"
                Logger.warning(f"Could not activate hotkey {text}: {reason}.", source="AUTOMATION")

    def native_message(self, message: int, parameter: int) -> bool:
        if not self.running:
            return False
        if message == 0x0312 and parameter in self.shortcut_ids.values():  # WM_HOTKEY
            self._hotkey_pressed.emit(parameter)
            return True
        if message == 0x001E or (message == 0x0218 and parameter == 0x12):
            # WM_TIMECHANGE / WM_POWERBROADCAST(PBT_APMRESUMEAUTOMATIC).
            self._clock_changed.emit()
        return False

    @Slot(int)
    def _deliver_hotkey(self, identifier: int):
        for trigger_id, registered in self.shortcut_ids.items():
            if registered == identifier and not self.recording:
                current = self.targets[trigger_id][0]
                self._emit(current, {"core.hotkey.shortcut": current.system_config["shortcut"]})
                return

    @Slot()
    def synchronize(self):
        if not self.running:
            return
        active = {
            trigger.trigger_id: trigger for trigger in self.store.triggers
            if trigger.enabled and trigger.event_type in SYSTEM_TRIGGER_TYPES
            and (self.process_supported or not trigger.event_type.startswith("process_"))
        }
        for key in set(self.targets) - set(active):
            self._remove(key)
        now, zone = self.clock(), self.zone()
        for key, trigger in active.items():
            if key in self.targets and self.targets[key][0] == trigger:
                continue
            self._remove(key)
            self.targets[key] = trigger, next(self.sequence)
            if trigger.event_type == "scheduled_time":
                schedule = CronSchedule.parse(trigger.system_config["expression"])
                self.schedules[key] = schedule, schedule.next_after(now, zone)
            if trigger.event_type == "file_watcher":
                self._files_dirty = True
        self._register_hotkeys()
        self._arm_schedule(now, zone)
        self._set_watches()
        has_process = any(t.event_type.startswith("process_") for t, _ in self.targets.values())
        if has_process and not self.process_timer.isActive():
            self.process_timer.start()
        elif not has_process:
            self.process_timer.stop()
        if self._files_dirty or any(
            t.event_type.startswith("process_") and key not in self.process_baselines
            for key, (t, _) in self.targets.items()
        ):
            self.request_scan()

    def _remove(self, key):
        self.targets.pop(key, None)
        if key in self.shortcut_ids:
            self.hotkeys.unregister(self.shortcut_ids.pop(key))
        self.shortcut_failures.discard(key)
        self.schedules.pop(key, None)
        # Keep the session high-water mark through disable/enable and clock rollback.
        if self.store.get(key) is None:
            self.last_due.pop(key, None)
        self.process_baselines.pop(key, None)
        self.file_baselines.pop(key, None)

    def _arm_schedule(self, now, zone):
        self.zone_id = bytes(zone.id())
        if not self.schedules:
            self.schedule_timer.stop()
            return
        deadlines = [due for _, due in self.schedules.values() if due is not None]
        # A low-frequency watchdog also notices clock/zone changes on systems
        # that do not emit Windows time-change notifications.
        self.schedule_timer.start(max(1, min(60_000, min(deadlines, default=now + 60_000) - now)))

    @Slot()
    def replan(self):
        now, zone = self.clock(), self.zone()
        for key, (schedule, _) in self.schedules.items():
            self.schedules[key] = schedule, schedule.next_after(now, zone)
        self._arm_schedule(now, zone)

    @Slot()
    def check_schedules(self):
        now, zone = self.clock(), self.zone()
        if bytes(zone.id()) != self.zone_id:
            self.replan()
            return
        for key, (schedule, due) in tuple(self.schedules.items()):
            if due is None or due > now:
                continue
            self.schedules[key] = schedule, schedule.next_after(now, zone)
            if now - due < 60_000 and due > self.last_due.get(key, -1):
                self.last_due[key] = due
                stamp = QDateTime.fromMSecsSinceEpoch(due, zone)
                self._emit(self.targets[key][0], {
                    "core.schedule.time": stamp.time().toString("HH:mm"),
                    "core.schedule.date": stamp.date().toString(Qt.DateFormat.ISODate),
                    "core.schedule.expression": schedule.expression,
                })
        self._arm_schedule(now, zone)

    @Slot(str)
    def _file_changed(self, _path):
        if self.running:
            self._files_dirty = True
            if not self.file_timer.isActive():
                self.file_timer.start()

    @Slot()
    def request_scan(self):
        if not self.running or self._scan is not None:
            return
        processes = {key: item for key, item in self.targets.items() if item[0].event_type.startswith("process_")}
        files = {
            key: item for key, item in self.targets.items()
            if self._files_dirty and item[0].event_type == "file_watcher"
        }
        self._files_dirty = False
        if processes or files:
            self._scan = _Scan(processes, files, self.snapshot, self._scanned)
            QThreadPool.globalInstance().start(self._scan)

    @Slot(object, object, object)
    def _scanned(self, targets, processes, files):
        self._scan = None
        if not self.running:
            return
        if processes is not None:
            for key, (trigger, generation) in targets.items():
                if self.targets.get(key) != (trigger, generation):
                    continue
                before = self.process_baselines.get(key)
                self.process_baselines[key] = processes
                if before is None:
                    continue
                started = trigger.event_type == "process_started"
                for pid, name in process_changes(before, processes, trigger.system_config["name"], started):
                    self._emit(trigger, {
                        "core.process.name": name, "core.process.pid": str(pid),
                        "core.process.event": "started" if started else "stopped",
                    })
        for key, (generation, state) in files.items():
            target = self.targets.get(key)
            if target is None or target[1] != generation:
                continue
            trigger = target[0]
            before = self.file_baselines.get(key)
            self.file_baselines[key] = state
            config = trigger.system_config
            if before is not None and file_change(before, state) == config["event"]:
                self._emit(trigger, {
                    "core.file.path": config["path"], "core.file.name": Path(config["path"]).name,
                    "core.file.event": config["event"],
                })
        added = self._set_watches()
        if added:  # Close the gap between sampling the baseline and installing a watch.
            self._files_dirty = True
        if self._files_dirty and not self.file_timer.isActive():
            self.file_timer.start()

    def _set_watches(self):
        wanted = {path for item in self.file_baselines.values() for path in item.watches}
        current = set(self.watcher.files() + self.watcher.directories())
        if current - wanted:
            self.watcher.removePaths(sorted(current - wanted))
        added = wanted - current
        return bool(added - set(self.watcher.addPaths(sorted(added)))) if added else False

    def _emit(self, trigger, context):
        target = self.targets.get(trigger.trigger_id)
        if target is not None and target[0] == trigger:
            self._trigger_ready.emit(target, context)

    @Slot(object, object)
    def _deliver(self, target, context):
        trigger, _ = target
        if (
            not self.running or self.targets.get(trigger.trigger_id) != target
            or self.store.get(trigger.trigger_id) != trigger
            or (trigger.event_type == "hotkey" and self.recording)
        ):
            return
        self.publish(TriggerEvent(
            trigger_id=trigger.trigger_id, service="core", trigger_type=trigger.event_type,
            context=context,
        ), SYSTEM_TRIGGER_TYPES[trigger.event_type])
