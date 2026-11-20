from unittest.mock import Mock

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QVBoxLayout, QTextEdit
from shiboken6 import isValid

from products.hub.automation.models import TaskDefinition
from products.hub.automation.variable_registry import (
    CallbackVariableProvider,
    VariableAvailability,
    VariableDataType,
    VariableDefinition,
    VariableRegistry,
)
from products.hub.ui.automation_page import IfTriggerDialog, TaskEditorDialog
from products.hub.ui.twitch_chat_input import (
    TwitchChatInputController,
    install_chat_variable_picker,
)
from products.hub.ui.variable_autocomplete import VariableAutocomplete


def definition(name, availability=VariableAvailability.GLOBAL):
    return VariableDefinition(
        name, "Shared label", "A test description", VariableDataType.TEXT,
        "Test", "Test", availability=availability,
    )


@pytest.fixture
def registry():
    result = VariableRegistry()
    result.register(CallbackVariableProvider(
        "Test", (
            definition("stream.title"),
            definition("stream.uptime"),
            definition("user.display_name", VariableAvailability.CONTEXTUAL),
        ),
        Mock(side_effect=AssertionError("Autocomplete must not resolve values")),
    ))
    return result


@pytest.fixture
def dialog():
    app = QApplication.instance() or QApplication([])
    window = QDialog()
    QVBoxLayout(window)
    yield window
    window.close()
    app.processEvents()


def show_field(window, edit):
    window.show()
    window.activateWindow()
    edit.setFocus()
    QApplication.processEvents()
    assert edit.hasFocus()


def names(completer):
    popup = completer.popup
    assert popup.isVisible()
    return [
        popup.item(index).data(Qt.ItemDataRole.UserRole)
        for index in range(popup.count())
    ]


@pytest.mark.parametrize("key", (Qt.Key.Key_Return, Qt.Key.Key_Tab))
def test_brace_filter_and_keyboard_replace_only_the_fragment(dialog, registry, key):
    edit = QLineEdit("🐱 start  end", dialog)
    dialog.layout().addWidget(edit)
    completer = VariableAutocomplete(edit, registry)
    show_field(dialog, edit)
    edit.setCursorPosition(9)
    QTest.keyClicks(edit, "{")
    assert names(completer) == ["{stream.title}", "{stream.uptime}", "{user.display_name}"]
    QTest.keyClicks(edit, "STREAM.")
    assert names(completer) == ["{stream.title}", "{stream.uptime}"]
    QTest.keyClick(edit, Qt.Key.Key_Down)
    QTest.keyClick(edit, key)
    assert edit.text() == "🐱 start {stream.uptime} end"
    assert not completer.popup.isVisible()
    assert edit.hasFocus()
    edit.undo()
    assert edit.text() == "🐱 start {STREAM. end"


def test_unknown_escape_cursor_focus_and_completed_paste(dialog, registry):
    edit, other = QLineEdit(dialog), QLineEdit(dialog)
    dialog.layout().addWidget(edit)
    dialog.layout().addWidget(other)
    completer = VariableAutocomplete(edit, registry)
    show_field(dialog, edit)
    QApplication.clipboard().setText("{stream.title}")
    edit.paste()
    assert completer.popup is None
    edit.setCursorPosition(2)
    QTest.keyClicks(edit, "u")
    assert completer.popup is None  # Still inside existing braces.
    edit.clear()
    QTest.keyClicks(edit, "{z")
    assert not completer.popup.isVisible()
    QTest.keyClick(edit, Qt.Key.Key_Backspace)
    assert names(completer)
    QTest.keyClick(edit, Qt.Key.Key_Escape)
    assert not completer.popup.isVisible()
    assert dialog.isVisible()
    QTest.keyClicks(edit, "st")
    assert names(completer)
    QTest.keyClick(edit, Qt.Key.Key_Home)
    assert not completer.popup.isVisible()
    edit.setCursorPosition(len(edit.text()))
    QTest.keyClicks(edit, "r")
    assert names(completer)
    other.setFocus()
    assert not completer.popup.isVisible()
    edit.setFocus()
    QTest.keyClick(edit, Qt.Key.Key_Tab)
    assert other.hasFocus()


def test_multiline_mouse_selection_and_normal_newline(dialog, registry):
    edit = QTextEdit(dialog)
    edit.setPlainText("first line\n🐱 say  later")
    dialog.layout().addWidget(edit)
    completer = VariableAutocomplete(edit, registry)
    show_field(dialog, edit)
    cursor = edit.textCursor()
    cursor.setPosition(edit.document().lastBlock().position() + 7)
    edit.setTextCursor(cursor)
    QTest.keyClicks(edit, "{stream.u")
    assert names(completer) == ["{stream.uptime}"]
    popup = completer.popup
    QTest.mouseClick(
        popup.viewport(), Qt.MouseButton.LeftButton,
        pos=popup.visualItemRect(popup.item(0)).center(),
    )
    assert edit.toPlainText() == "first line\n🐱 say {stream.uptime} later"
    assert edit.hasFocus()
    QTest.keyClick(edit, Qt.Key.Key_Return)
    assert edit.toPlainText().endswith("{stream.uptime}\n later")


def test_chat_uses_picker_scope_no_value_resolution_and_resumes_send(dialog, registry):
    edit = QLineEdit(dialog)
    dialog.layout().addWidget(edit)
    controller = TwitchChatInputController(edit, lambda: ())
    picker = install_chat_variable_picker(registry, edit, dialog.layout(), dialog)
    show_field(dialog, edit)
    submit = Mock()
    edit.returnPressed.connect(submit)
    QTest.keyClicks(edit, "/me live for {")
    assert names(picker.autocomplete) == ["{stream.title}", "{stream.uptime}"]
    assert not controller.helper_visible
    QTest.keyClicks(edit, "upt")
    QTest.keyClick(edit, Qt.Key.Key_Return)
    assert edit.text() == "/me live for {stream.uptime}"
    submit.assert_not_called()
    QTest.keyClick(edit, Qt.Key.Key_Return)
    submit.assert_called_once_with()
    assert picker.isEnabled()


def test_task_and_if_trigger_follow_existing_picker_eligibility(registry):
    nested = TaskDefinition("nested", "core.if", "Nested If", {})
    parent = TaskDefinition("parent", "core.if", "Parent If", {}, then_tasks=[nested])
    editor = TaskEditorDialog("core.if", task=parent.then_tasks[0], variable_registry=registry)
    trigger = IfTriggerDialog(registry)
    try:
        row = editor.condition_builder.rows[0]
        show_field(editor, row.left_edit)
        QTest.keyClicks(row.left_edit, "{user")
        assert names(row.left_picker.autocomplete) == ["{user.display_name}"]
        QTest.keyClick(row.left_edit, Qt.Key.Key_Tab)
        assert editor.values()["config"]["conditions"][0]["left"] == "{user.display_name}"
        assert editor.name_edit.findChild(VariableAutocomplete) is None
        editor.close()

        row = trigger.condition_builder.rows[0]
        show_field(trigger, row.left_edit)
        QTest.keyClicks(row.left_edit, "{")
        assert names(row.left_picker.autocomplete) == ["{stream.title}", "{stream.uptime}"]
    finally:
        editor.close()
        trigger.close()


def test_catalog_refresh_deduplication_and_popup_teardown(dialog, registry):
    edit = QLineEdit(dialog)
    dialog.layout().addWidget(edit)
    completer = VariableAutocomplete(
        edit, registry, extra_definitions=(
            definition("stream.title"),
            definition("automation.result", VariableAvailability.TEMPORARY),
        ),
    )
    show_field(dialog, edit)
    QTest.keyClicks(edit, "{")
    assert names(completer).count("{stream.title}") == 1
    assert "{automation.result}" in names(completer)
    QTest.keyClick(edit, Qt.Key.Key_Escape)
    registry.register(CallbackVariableProvider(
        "Extra", (definition("custom.new"),), Mock()
    ))
    edit.clear()
    QTest.keyClicks(edit, "{")
    assert "{custom.new}" in names(completer)
    popup = completer.popup
    edit.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(popup)


def test_password_field_never_opens_autocomplete(dialog, registry):
    edit = QLineEdit(dialog)
    edit.setEchoMode(QLineEdit.EchoMode.Password)
    dialog.layout().addWidget(edit)
    completer = VariableAutocomplete(edit, registry)
    show_field(dialog, edit)
    QTest.keyClicks(edit, "{")
    assert completer.popup is None
