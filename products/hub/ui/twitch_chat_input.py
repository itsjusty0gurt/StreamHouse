from __future__ import annotations

import re
from collections import deque
from typing import Callable, Iterable

from PySide6.QtCore import QEvent, QObject, QRunnable, Qt, Signal
from PySide6.QtWidgets import QBoxLayout, QLineEdit, QWidget

from products.hub.automation.variable_registry import (
    VariableAvailability,
    VariableDefinition,
    VariableRegistry,
)
from products.hub.twitch.slash_commands import (
    TWITCH_SLASH_COMMANDS,
    TWITCH_SLASH_COMMANDS_BY_NAME,
    TwitchSlashRequest,
    parse_twitch_slash_request,
)
from products.hub.twitch.chat_helper import (
    TwitchUserSuggestion, matching_chat_commands, matching_chat_users,
)
from products.hub.twitch.commands import TwitchCommandTriggerStore
from products.hub.ui.variable_autocomplete import (
    TextCompletions, TextSuggestion, VariableAutocomplete,
)
from products.hub.ui.variable_picker import VariablePickerButton


def is_chat_variable(definition: VariableDefinition) -> bool:
    """Return whether a Variable is meaningful outside Automation context."""

    return definition.availability is VariableAvailability.GLOBAL


def render_chat_message(registry: VariableRegistry, template: str) -> str:
    """Resolve current global/provider values without trigger context."""

    return registry.render(template, {})


def is_twitch_action_message(text: str) -> bool:
    """Return whether text is Twitch's ordinary /me chat action syntax."""

    clean = text.lstrip().casefold()
    return clean == "/me" or clean.startswith("/me ")


def install_chat_variable_picker(
    registry: VariableRegistry,
    edit: QLineEdit,
    layout: QBoxLayout,
    parent: QWidget,
) -> VariablePickerButton:
    """Install the shared global Variable Picker beside a chat editor."""

    picker = VariablePickerButton(
        registry,
        edit,
        parent=parent,
        definition_filter=is_chat_variable,
    )
    picker.setObjectName("twitchChatVariablePickerButton")
    picker.setText("{x}")
    picker.setAccessibleName("Choose a chat Variable")
    layout.insertWidget(1, picker)
    return picker


_SLASH = re.compile(r"^\s*/([a-z]*)\Z", re.IGNORECASE | re.ASCII)
_SLASH_USER = re.compile(r"^\s*/([a-z]+) +(@?)([a-z0-9_]*)\Z", re.IGNORECASE | re.ASCII)
_CHAT_TOKEN = re.compile(
    r"(?<![\w@!/])(?:@(?P<login>[a-zA-Z0-9_]*)|!(?P<command>[a-zA-Z0-9_-]*))\Z"
)
_SLASH_SUFFIX = re.compile(r"[a-zA-Z]*")
_LOGIN_SUFFIX = re.compile(r"[a-zA-Z0-9_]*")
_COMMAND_SUFFIX = re.compile(r"[a-zA-Z0-9_-]*")


class TwitchChatInputController(QObject):
    """Contribute Twitch suggestions to the shared popup and own sent history."""

    def __init__(
        self,
        edit: QLineEdit,
        known_users: Callable[[], Iterable[TwitchUserSuggestion]],
        *,
        command_store: TwitchCommandTriggerStore | None = None,
        autocomplete: VariableAutocomplete | None = None,
        history_limit: int = 100,
    ) -> None:
        super().__init__(edit)
        self.edit = edit
        self.known_users = known_users
        self.command_store = command_store
        self.autocomplete = autocomplete or VariableAutocomplete(edit, None)
        self.autocomplete.completion_provider = self._completions
        self.history: deque[str] = deque(maxlen=max(1, history_limit))
        self._history_index: int | None = None
        self._history_draft = ""
        self._setting_text = False
        edit.installEventFilter(self)
        edit.textEdited.connect(self._text_edited)

    @property
    def helper_visible(self) -> bool:
        popup = self.autocomplete.popup
        return popup is not None and popup.isVisible()

    def record_sent(self, message: str) -> None:
        clean = message.strip()
        if clean and (not self.history or self.history[-1] != clean):
            self.history.append(clean)
        self.autocomplete.dismiss()
        self._leave_history()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            watched is self.edit and event.type() == QEvent.Type.KeyPress
            and not self.helper_visible
            and event.modifiers() in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.KeypadModifier)
        ):
            if event.key() == Qt.Key.Key_Up:
                return self._history_previous()
            if event.key() == Qt.Key.Key_Down:
                return self._history_next()
        return super().eventFilter(watched, event)

    def _text_edited(self, _text: str) -> None:
        if not self._setting_text:
            self._leave_history()

    def _completions(self, before: str, after: str) -> TextCompletions | None:
        def with_space(text: str, suffix: int) -> str:
            remaining = after[suffix:]
            if not remaining or remaining[0].isalnum():
                return text + " "
            return text

        match = _SLASH.fullmatch(before)
        if match is not None:
            query = match[1].casefold()
            suffix = len(_SLASH_SUFFIX.match(after)[0])
            suggestions = [
                TextSuggestion(
                    with_space(f"/{command.name}", suffix),
                    f"{command.syntax} — {command.description}",
                    f"Requires {command.required_scope}.",
                    follow_up=command.requires_user,
                )
                for command in TWITCH_SLASH_COMMANDS if command.name.startswith(query)
            ]
            if "me".startswith(query):
                suggestions.append(TextSuggestion(
                    with_space("/me", suffix), "/me <message> — Send a chat action"
                ))
            return TextCompletions(1 + len(match[1]), suffix, tuple(suggestions))

        target = _SLASH_USER.fullmatch(before)
        if target is not None:
            command = TWITCH_SLASH_COMMANDS_BY_NAME.get(target[1].casefold())
            if command is not None and command.requires_user:
                return self._user_completions(target[2], target[3], after)

        match = _CHAT_TOKEN.search(before)
        if match is None:
            return None
        if match["login"] is not None:
            return self._user_completions("@", match["login"], after)
        if self.command_store is None:
            return None
        suffix = len(_COMMAND_SUFFIX.match(after)[0])
        return TextCompletions(len(match[0]), suffix, tuple(
            TextSuggestion(
                with_space(f"!{command.name}", suffix),
                f"!{command.name} — {command.source}",
                command.description,
            )
            for command in matching_chat_commands(self.command_store, match["command"].casefold())
        ))

    def _user_completions(self, prefix: str, fragment: str, after: str) -> TextCompletions:
        suffix = len(_LOGIN_SUFFIX.match(after)[0])
        space = " " if not after[suffix:] else ""
        return TextCompletions(len(prefix) + len(fragment), suffix, tuple(
            TextSuggestion(
                f"{prefix}{user.login}{space}",
                f"@{user.login}" + (
                    f" — {user.display_name}"
                    if user.display_name.casefold() not in ("", user.login) else ""
                ),
            )
            for user in matching_chat_users(self.known_users(), fragment.casefold())
        ))

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
    """Execute one API-backed Twitch chat action away from the Qt UI thread."""

    def __init__(self, service, request: TwitchSlashRequest) -> None:
        super().__init__()
        self.service = service
        self.request = request
        self.signals = _SlashWorkerSignals()

    def run(self) -> None:
        try:
            success, target = self.service.execute_slash_action(self.request)
            self.signals.finished.emit(
                self,
                self.request,
                success,
                target,
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
