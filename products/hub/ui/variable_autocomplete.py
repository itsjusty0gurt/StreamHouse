from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QApplication, QLineEdit, QListWidget, QListWidgetItem, QTextEdit,
)

from products.hub.automation.variable_registry import VariableDefinition, VariableRegistry


_UNFINISHED = re.compile(r"\{([a-zA-Z0-9_.]*)\Z")
_CLOSING_SUFFIX = re.compile(r"[a-zA-Z0-9_.]*\}")
_KEYS = {
    Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Return, Qt.Key.Key_Enter,
    Qt.Key.Key_Tab, Qt.Key.Key_Escape,
}


def picker_definitions(
    registry: VariableRegistry,
    extra_definitions: tuple[VariableDefinition, ...],
    definition_filter: Callable[[VariableDefinition], bool] | None,
) -> tuple[VariableDefinition, ...]:
    """Use canonical picker metadata, with registered definitions taking priority."""
    definitions: dict[str, VariableDefinition] = {}
    for definition in (*registry.definitions(), *extra_definitions):
        definitions.setdefault(definition.name, definition)
    return tuple(
        definition for definition in definitions.values()
        if definition_filter is None or definition_filter(definition)
    )


def _qt_length(text: str) -> int:
    # Qt cursor positions count UTF-16 code units, not Python code points.
    return len(text.encode("utf-16-le", errors="surrogatepass")) // 2


@dataclass(frozen=True, slots=True)
class TextSuggestion:
    text: str
    label: str
    tooltip: str = ""
    follow_up: bool = False


@dataclass(frozen=True, slots=True)
class TextCompletions:
    # Character counts adjacent to the cursor, in Python code points.
    before: int
    after: int
    suggestions: tuple[TextSuggestion, ...]


class VariableAutocomplete(QObject):
    """Shared cursor completion; Variables are suggested without resolving values."""

    def __init__(
        self,
        target: QLineEdit | QTextEdit,
        registry: VariableRegistry | None,
        *,
        extra_definitions: tuple[VariableDefinition, ...] = (),
        definition_filter: Callable[[VariableDefinition], bool] | None = None,
    ) -> None:
        super().__init__(target)
        self.target = target
        self.registry = registry
        self.extra_definitions = extra_definitions
        self.definition_filter = definition_filter
        self.completion_provider: Callable[[str, str], TextCompletions | None] | None = None
        self.popup: QListWidget | None = None
        self._catalog: tuple[VariableDefinition, ...] | None = None
        self._query: str | None = None
        self._matches: tuple[TextSuggestion, ...] = ()
        self._replacement: tuple[int, int] | None = None
        self._active_cursor: tuple[int, str, str] | None = None
        self._inserting = False
        if isinstance(target, QLineEdit):
            target.textEdited.connect(self._edited)
            target.textChanged.connect(self._line_text_changed)
        else:
            target.textChanged.connect(self._edited)
        target.cursorPositionChanged.connect(self._cursor_moved)
        target.selectionChanged.connect(self._cursor_moved)
        target.installEventFilter(self)

    def _cursor_text(self) -> tuple[int, str, str] | None:
        target = self.target
        if not target.hasFocus() or not target.isEnabled() or target.isReadOnly():
            return None
        if isinstance(target, QLineEdit):
            if (
                target.echoMode() != QLineEdit.EchoMode.Normal
                or target.hasSelectedText()
            ):
                return None
            text, position = target.text(), target.cursorPosition()
            encoded = text.encode("utf-16-le", errors="surrogatepass")
            before = encoded[: position * 2].decode("utf-16-le", errors="surrogatepass")
            after = text[len(before):]
        else:
            cursor = target.textCursor()
            if cursor.hasSelection():
                return None
            position = cursor.position()
            block = cursor.block().text()
            encoded = block.encode("utf-16-le", errors="surrogatepass")
            before = encoded[: cursor.positionInBlock() * 2].decode(
                "utf-16-le", errors="surrogatepass"
            )
            after = block[len(before):]
        return position, before, after

    def _edited(self, *_args: object) -> None:
        if not self._inserting:
            self._refresh()

    def _cursor_moved(self, *_args: object) -> None:
        if self._replacement is not None and not self._inserting:
            if self._cursor_text() != self._active_cursor:
                self._refresh()

    def _line_text_changed(self, _text: str) -> None:
        if not self._inserting and self._active_cursor is not None:
            if self._cursor_text() != self._active_cursor:
                self.dismiss()

    def dismiss(self) -> None:
        self._catalog = None
        self._query = None
        self._matches = ()
        self._replacement = None
        self._active_cursor = None
        if self.popup is not None:
            self.popup.hide()

    def _variable_matches(self, query: str) -> tuple[TextSuggestion, ...]:
        assert self.registry is not None
        if self._catalog is None:
            self._catalog = picker_definitions(
                self.registry, self.extra_definitions, self.definition_filter
            )
        if query == self._query:
            return self._matches
        self._query = query
        matches = sorted(
            (
                item for item in self._catalog
                if query in item.name or query in item.display_name.casefold()
            ),
            key=lambda item: (not item.name.startswith(query), item.name),
        )
        suggestions = []
        for definition in matches:
            context = definition.context_label
            label = f"{definition.placeholder}  —  {definition.display_name}"
            if definition.lifetime_label == "Routine":
                label += " (routine)"
                context = context or "Available only in its matching routine context"
            suggestions.append(TextSuggestion(
                definition.placeholder, label,
                "\n".join(filter(None, (definition.description, context))),
            ))
        return tuple(suggestions)

    def _refresh(self) -> None:
        cursor = self._cursor_text()
        if cursor is None:
            self.dismiss()
            return
        position, before, after = cursor
        match = _UNFINISHED.search(before) if self.registry is not None else None
        if match is not None and not _CLOSING_SUFFIX.match(after):
            completions = TextCompletions(
                len(match[0]), 0, self._variable_matches(match[1].casefold())
            )
        else:
            self._catalog = None
            self._query = None
            completions = (
                self.completion_provider(before, after)
                if self.completion_provider is not None else None
            )
        if completions is None:
            self.dismiss()
            return
        self._active_cursor = cursor
        self._replacement = (
            position - (_qt_length(before[-completions.before:]) if completions.before else 0),
            position + _qt_length(after[:completions.after]),
        )
        matches = completions.suggestions
        if not matches:
            self._matches = matches
            if self.popup is not None:
                self.popup.clear()
                self.popup.hide()
            return
        if matches != self._matches or self.popup is None:
            self._matches = matches
            if self.popup is None:
                self.popup = QListWidget(self.target)
                self.popup.setObjectName("variableAutocomplete")
                self.popup.setAccessibleName(
                    "Text suggestions" if self.completion_provider else "Variable suggestions"
                )
                self.popup.setWindowFlags(
                    Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint
                    | Qt.WindowType.WindowDoesNotAcceptFocus
                )
                self.popup.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
                self.popup.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                self.popup.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                self.popup.setTextElideMode(Qt.TextElideMode.ElideMiddle)
                self.popup.itemClicked.connect(self._choose)
            self.popup.clear()
            for suggestion in matches:
                item = QListWidgetItem(suggestion.label)
                item.setData(Qt.ItemDataRole.UserRole, suggestion.text)
                item.setToolTip(suggestion.tooltip)
                self.popup.addItem(item)
            self.popup.setCurrentRow(0)
        if self.popup is not None and self.popup.count():
            self._position_popup()
            self.popup.show()

    def _position_popup(self) -> None:
        popup = self.popup
        assert popup is not None
        target = self.target
        anchor = target if isinstance(target, QLineEdit) else target.viewport()
        rect = target.cursorRect()
        below = anchor.mapToGlobal(rect.bottomLeft() + QPoint(0, 2))
        above = anchor.mapToGlobal(rect.topLeft() - QPoint(0, 1))
        screen = QApplication.screenAt(below) or target.screen()
        bounds = screen.availableGeometry()
        width = min(max(300, target.width()), 580, bounds.width())
        height = min(8, popup.count()) * max(popup.sizeHintForRow(0), 18) + 4
        room_below = max(0, bounds.bottom() - below.y() + 1)
        room_above = max(0, above.y() - bounds.top())
        use_above = room_below < height and room_above > room_below
        height = min(height, room_above if use_above else room_below)
        x = max(bounds.left(), min(below.x(), bounds.right() - width + 1))
        popup.setGeometry(x, above.y() - height if use_above else below.y(), width, height)

    def _choose(self, item: QListWidgetItem) -> None:
        assert self.popup is not None
        row = self.popup.row(item)
        span = self._replacement
        if (
            span is None or not 0 <= row < len(self._matches)
            or self._cursor_text() != self._active_cursor
        ):
            self.dismiss()
            return
        suggestion = self._matches[row]
        self.dismiss()
        start, end = span
        placeholder = suggestion.text
        self._inserting = True
        try:
            if isinstance(self.target, QLineEdit):
                if (
                    _qt_length(self.target.text()) - (end - start) + _qt_length(placeholder)
                    > self.target.maxLength()
                ):
                    return
                self.target.setSelection(start, end - start)
                self.target.insert(placeholder)
            else:
                cursor = self.target.textCursor()
                cursor.setPosition(start)
                cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
                cursor.insertText(placeholder)
                self.target.setTextCursor(cursor)
        finally:
            self._inserting = False
        if suggestion.follow_up:
            self._refresh()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.target:
            if event.type() in (QEvent.Type.FocusOut, QEvent.Type.Hide):
                self.dismiss()
            popup = self.popup
            if (
                popup is not None and popup.isVisible()
                and event.type() in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress)
                and event.key() in _KEYS
                and event.modifiers() in (
                    Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.KeypadModifier,
                )
            ):
                if event.type() == QEvent.Type.ShortcutOverride:
                    event.accept()
                elif event.key() == Qt.Key.Key_Escape:
                    self.dismiss()
                elif event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                    delta = -1 if event.key() == Qt.Key.Key_Up else 1
                    popup.setCurrentRow((popup.currentRow() + delta) % popup.count())
                elif popup.currentItem() is not None:
                    self._choose(popup.currentItem())
                return True
        return super().eventFilter(watched, event)
