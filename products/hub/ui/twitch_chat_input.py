from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Callable, Iterable

from PySide6.QtCore import QEvent, QObject, QPoint, QRunnable, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QFrame,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)


@dataclass(frozen=True)
class TwitchSlashCommand:
    name: str
    syntax: str
    description: str
    requires_user: bool = False
    availability: str = ""


@dataclass(frozen=True)
class TwitchSlashRequest:
    action: str
    user_reference: str
    duration: int | None = None
    reason: str = ""


@dataclass(frozen=True)
class TwitchUserSuggestion:
    login: str
    display_name: str = ""


TWITCH_SLASH_COMMANDS = (
    TwitchSlashCommand(
        "ban",
        "/ban <user> [reason]",
        "Ban a user",
        requires_user=True,
        availability="Requires Twitch moderation permission",
    ),
    TwitchSlashCommand(
        "timeout",
        "/timeout <user> [seconds] [reason]",
        "Temporarily timeout a user",
        requires_user=True,
        availability="Defaults to 600 seconds",
    ),
    TwitchSlashCommand(
        "unban",
        "/unban <user>",
        "Remove a ban or timeout",
        requires_user=True,
        availability="Requires Twitch moderation permission",
    ),
)


def parse_twitch_slash_request(text: str) -> TwitchSlashRequest:
    parts = text.strip().split()
    if not parts or not parts[0].startswith("/"):
        raise ValueError("Enter a supported Twitch slash command.")
    command = parts[0][1:].casefold()
    supported = {item.name for item in TWITCH_SLASH_COMMANDS}
    if command not in supported:
        raise ValueError(f"Unsupported Twitch slash command: /{command or '?'}")
    if len(parts) < 2:
        raise ValueError(f"/{command} requires a Twitch username.")
    user_reference = parts[1].lstrip("@").strip()
    if not user_reference:
        raise ValueError(f"/{command} requires a Twitch username.")
    if command == "timeout":
        duration = 600
        reason_start = 2
        if len(parts) > 2 and parts[2].isdigit():
            duration = int(parts[2])
            reason_start = 3
        if not 1 <= duration <= 1_209_600:
            raise ValueError(
                "Timeout duration must be between 1 and 1,209,600 seconds."
            )
        return TwitchSlashRequest(
            action=command,
            user_reference=user_reference,
            duration=duration,
            reason=" ".join(parts[reason_start:])[:500],
        )
    return TwitchSlashRequest(
        action=command,
        user_reference=user_reference,
        reason=" ".join(parts[2:])[:500] if command == "ban" else "",
    )


class TwitchChatInputController(QObject):
    """Own slash completion and bounded, session-only sent-message history."""

    def __init__(
        self,
        edit: QLineEdit,
        known_users: Callable[[], Iterable[TwitchUserSuggestion]],
        *,
        history_limit: int = 100,
    ) -> None:
        super().__init__(edit)
        self.edit = edit
        self.known_users = known_users
        self.history: deque[str] = deque(maxlen=max(1, history_limit))
        self._history_index: int | None = None
        self._history_draft = ""
        self._setting_text = False
        self._suppressed_text = ""

        self.popup = QFrame(edit.window(), Qt.WindowType.ToolTip)
        self.popup.setObjectName("twitchSlashHelper")
        popup_layout = QVBoxLayout(self.popup)
        popup_layout.setContentsMargins(1, 1, 1, 1)
        self.suggestions = QListWidget(self.popup)
        self.suggestions.setObjectName("twitchSlashSuggestions")
        self.suggestions.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        popup_layout.addWidget(self.suggestions)
        self.popup.setStyleSheet(
            "QFrame#twitchSlashHelper {"
            "background: palette(base); border: 1px solid palette(mid);"
            "border-radius: 5px;"
            "}"
            "QListWidget#twitchSlashSuggestions {"
            "background: transparent; border: none; outline: none; padding: 3px;"
            "}"
            "QListWidget#twitchSlashSuggestions::item { padding: 5px 7px; }"
            "QListWidget#twitchSlashSuggestions::item:selected {"
            "background: palette(highlight); color: palette(highlighted-text);"
            "}"
        )
        self.popup.hide()
        self.suggestions.itemClicked.connect(self._complete_item)
        edit.installEventFilter(self)
        edit.textEdited.connect(self._text_edited)

    @property
    def helper_visible(self) -> bool:
        return not self.popup.isHidden() and self.suggestions.count() > 0

    def record_sent(self, message: str) -> None:
        clean = message.strip()
        if clean and (not self.history or self.history[-1] != clean):
            self.history.append(clean)
        self._leave_history()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is not self.edit or event.type() != QEvent.Type.KeyPress:
            return super().eventFilter(watched, event)
        key_event = event if isinstance(event, QKeyEvent) else None
        if key_event is None:
            return False
        key = key_event.key()
        if self.helper_visible:
            if key in {Qt.Key.Key_Up, Qt.Key.Key_Down}:
                self._move_selection(-1 if key == Qt.Key.Key_Up else 1)
                return True
            if key in {Qt.Key.Key_Tab, Qt.Key.Key_Return, Qt.Key.Key_Enter}:
                item = self.suggestions.currentItem()
                if item is not None:
                    self._complete_item(item)
                return True
            if key == Qt.Key.Key_Escape:
                self.popup.hide()
                self._suppressed_text = self.edit.text()
                return True
        if key == Qt.Key.Key_Up:
            return self._history_previous()
        if key == Qt.Key.Key_Down:
            return self._history_next()
        return super().eventFilter(watched, event)

    def _text_edited(self, text: str) -> None:
        if self._setting_text:
            return
        self._leave_history()
        self._suppressed_text = ""
        self._refresh_helper(text)

    def refresh(self) -> None:
        self._refresh_helper(self.edit.text())

    def _refresh_helper(self, text: str) -> None:
        self.suggestions.clear()
        if not text.startswith("/") or text == self._suppressed_text:
            self.popup.hide()
            return
        command_text = text[1:]
        if " " not in command_text:
            query = command_text.casefold()
            for command in TWITCH_SLASH_COMMANDS:
                if command.name.startswith(query):
                    item = QListWidgetItem(
                        f"{command.syntax}\n{command.description} — {command.availability}"
                    )
                    item.setData(
                        Qt.ItemDataRole.UserRole,
                        ("command", command.name),
                    )
                    item.setToolTip(
                        f"{command.description}. {command.availability}."
                    )
                    self.suggestions.addItem(item)
        else:
            command_name, argument_text = command_text.split(" ", 1)
            command = next(
                (
                    item
                    for item in TWITCH_SLASH_COMMANDS
                    if item.name == command_name.casefold()
                ),
                None,
            )
            if command is not None and command.requires_user:
                if (
                    " " not in argument_text.rstrip()
                    and not argument_text.endswith(" ")
                ):
                    query = argument_text.lstrip("@").casefold()
                    for user in self._matching_users(query):
                        label = f"@{user.login}"
                        if (
                            user.display_name
                            and user.display_name.casefold()
                            != user.login.casefold()
                        ):
                            label += f"  {user.display_name}"
                        item = QListWidgetItem(label)
                        item.setData(Qt.ItemDataRole.UserRole, ("user", user.login))
                        self.suggestions.addItem(item)
        if not self.suggestions.count():
            self.popup.hide()
            return
        self.suggestions.setCurrentRow(0)
        visible_rows = min(self.suggestions.count(), 7)
        row_height = max(self.suggestions.sizeHintForRow(0), 34)
        width = max(self.edit.width(), 360)
        self.popup.resize(width, visible_rows * row_height + 8)
        position = self.edit.mapToGlobal(QPoint(0, self.edit.height() + 3))
        self.popup.move(position)
        self.popup.show()

    def _matching_users(self, query: str) -> list[TwitchUserSuggestion]:
        matches: dict[str, TwitchUserSuggestion] = {}
        for user in self.known_users():
            login = user.login.strip().lstrip("@")
            display = user.display_name.strip()
            if not login:
                continue
            if (
                query
                and query not in login.casefold()
                and query not in display.casefold()
            ):
                continue
            matches.setdefault(login.casefold(), TwitchUserSuggestion(login, display))
        return sorted(
            matches.values(),
            key=lambda item: (item.login.casefold(), item.display_name.casefold()),
        )[:20]

    def _complete_item(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(data, tuple) or len(data) != 2:
            return
        kind, value = data
        if kind == "command":
            completed = f"/{value} "
            suppress = False
        else:
            command = self.edit.text()[1:].split(" ", 1)[0]
            completed = f"/{command} {value} "
            suppress = True
        self._set_text(completed)
        self.edit.setCursorPosition(len(completed))
        self.popup.hide()
        self._suppressed_text = completed if suppress else ""
        if not suppress:
            self._refresh_helper(completed)
        self.edit.setFocus()

    def _move_selection(self, delta: int) -> None:
        count = self.suggestions.count()
        if count:
            self.suggestions.setCurrentRow(
                (self.suggestions.currentRow() + delta) % count
            )

    def _history_previous(self) -> bool:
        if not self.history:
            return False
        if self._history_index is None:
            if self.edit.text():
                return False
            self._history_draft = self.edit.text()
            self._history_index = len(self.history) - 1
        elif self._history_index > 0:
            self._history_index -= 1
        self._set_text(self.history[self._history_index])
        return True

    def _history_next(self) -> bool:
        if self._history_index is None:
            return False
        if self._history_index < len(self.history) - 1:
            self._history_index += 1
            self._set_text(self.history[self._history_index])
        else:
            self._set_text(self._history_draft)
            self._leave_history()
        return True

    def _set_text(self, text: str) -> None:
        self._setting_text = True
        self.edit.setText(text)
        self._setting_text = False

    def _leave_history(self) -> None:
        self._history_index = None
        self._history_draft = ""


class _SlashWorkerSignals(QObject):
    finished = Signal(object, object, bool, str, str)


class TwitchSlashActionWorker(QRunnable):
    """Resolve a named user and execute an existing Twitch moderation action."""

    def __init__(self, service, request: TwitchSlashRequest) -> None:
        super().__init__()
        self.service = service
        self.request = request
        self.signals = _SlashWorkerSignals()

    def run(self) -> None:
        try:
            user = self.service.resolve_user(self.request.user_reference)
            user_id = str(user.get("id", "")) if isinstance(user, dict) else ""
            if not user_id:
                raise ValueError(
                    f"Twitch user @{self.request.user_reference} was not found."
                )
            success = self.service.moderate_user(
                self.request.action,
                user_id,
                duration=self.request.duration,
                reason=self.request.reason,
            )
            self.signals.finished.emit(
                self,
                self.request,
                success,
                self.request.user_reference,
                "",
            )
        except Exception as error:
            self.signals.finished.emit(
                self,
                self.request,
                False,
                self.request.user_reference,
                str(error),
            )
