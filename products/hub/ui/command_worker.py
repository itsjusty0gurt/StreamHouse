from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from products.hub.automation.models import AutomationExecutionResult
from products.hub.automation.service import AutomationService
from products.hub.twitch.commands import TwitchCommandTriggerResult
from products.hub.twitch.models import TwitchMessage


@dataclass(frozen=True, slots=True)
class CommandExecutionWorkerResult:
    command: TwitchCommandTriggerResult
    message: TwitchMessage
    execution: AutomationExecutionResult


class CommandExecutionSignals(QObject):
    completed = Signal(object)
    failed = Signal(object, object, str)


class CommandExecutionWorker(QRunnable):
    """Run network-backed command routines without blocking the Qt thread."""

    def __init__(
        self,
        automation_service: AutomationService,
        command: TwitchCommandTriggerResult,
        message: TwitchMessage,
    ) -> None:
        super().__init__()
        self.automation_service = automation_service
        self.command = command
        self.message = message
        self.signals = CommandExecutionSignals()

    @Slot()
    def run(self) -> None:
        try:
            execution = self.automation_service.publish_trigger(
                self.command.to_event()
            )
        except Exception as error:
            self.signals.failed.emit(self.command, self.message, str(error))
            return
        self.signals.completed.emit(
            CommandExecutionWorkerResult(
                self.command,
                self.message,
                execution,
            )
        )
