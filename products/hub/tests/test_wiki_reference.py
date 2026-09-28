import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from products.hub.automation.core_triggers import (
    MINIMUM_TIMER_MILLISECONDS,
    TIMER_MODES,
    TIMER_UNIT_LABELS,
)
from products.hub.automation.task_catalog import BUILTIN_TASK_METADATA
from products.hub.automation.tasks import TaskRegistry
from products.hub.automation.variable_providers import context_provider
from products.hub.automation.variable_registry import VariableRegistry
from products.hub.core.wiki_reference import WIKI_CATEGORIES, build_wiki_entries
from products.hub.twitch.slash_commands import TWITCH_SLASH_COMMANDS
from products.hub.ui.wiki_page import WikiPage


def _reference_sources() -> tuple[TaskRegistry, VariableRegistry]:
    tasks = TaskRegistry(BUILTIN_TASK_METADATA)
    variables = VariableRegistry()
    variables.register(context_provider())
    variables.register_alias("legacy.command_data", "command.data")
    return tasks, variables


def test_catalog_derives_tasks_triggers_variables_and_commands() -> None:
    tasks, variables = _reference_sources()
    entries = build_wiki_entries(tasks, variables)
    ids = {entry.entry_id for entry in entries}

    assert {entry.category for entry in entries} == set(WIKI_CATEGORIES)
    assert {
        entry.entry_id.removeprefix("task:")
        for entry in entries
        if entry.category == "Tasks"
    } == {metadata.task_type for metadata in tasks.visible_metadata()}
    assert "trigger:channel.raid" in ids
    assert "trigger:CurrentProgramSceneChanged" in ids
    assert "variable:command.data" in ids
    assert "variable:legacy.command_data" not in ids
    assert "variables:automation-outputs" in ids
    assert "commands:overview" in ids
    assert "integration:touch-portal" in ids


def test_contextual_variables_are_documented_without_active_context() -> None:
    tasks, variables = _reference_sources()
    entries = build_wiki_entries(tasks, variables)
    command_data = next(
        entry for entry in entries if entry.entry_id == "variable:command.data"
    )

    assert command_data.copy_text == "{command.data}"
    assert "chat command routine" in command_data.search_text()
    assert "routine" in command_data.search_text()


def test_timer_reference_documents_exact_random_units_and_resampling() -> None:
    tasks, variables = _reference_sources()
    timer = next(
        entry
        for entry in build_wiki_entries(tasks, variables)
        if entry.entry_id == "trigger:timer"
    )
    text = timer.search_text()
    sections = {section.title: section.lines for section in timer.sections}

    assert TIMER_MODES["fixed"].casefold() in text
    assert TIMER_MODES["random"].casefold() in text
    for label in TIMER_UNIT_LABELS.values():
        assert label.casefold() in text
    assert str(MINIMUM_TIMER_MILLISECONDS) in text
    assert "numeric interval" in text
    assert "numeric minimum and maximum" in text
    assert "minimum must not exceed maximum" in text
    assert "after every firing" in text
    assert "normal queue" in text
    assert "cancels its scheduled firing" in text
    assert "editing an enabled timer replaces the old schedule" in text
    assert set(("Configuration", "Validation", "Scheduling", "Examples")) <= set(
        sections
    )
    assert any("10 Minutes" in line for line in sections["Examples"])
    assert any("5 Minutes to 10 Minutes" in line for line in sections["Examples"])


def test_twitch_slash_reference_is_derived_from_command_registry() -> None:
    tasks, variables = _reference_sources()
    entry = next(
        item
        for item in build_wiki_entries(tasks, variables)
        if item.entry_id == "twitch:slash-commands"
    )
    supported = next(
        section.lines
        for section in entry.sections
        if section.title == "Supported commands"
    )

    assert len(supported) == len(TWITCH_SLASH_COMMANDS)
    for command in TWITCH_SLASH_COMMANDS:
        assert any(
            command.syntax in line
            and command.description in line
            and command.required_scope in line
            for line in supported
        )
    assert "chatters/users context menu" in entry.search_text()


def test_raid_page_reference_documents_runtime_finder_and_permissions() -> None:
    tasks, variables = _reference_sources()
    entry = next(
        item
        for item in build_wiki_entries(tasks, variables)
        if item.entry_id == "twitch:raid-page"
    )
    text = entry.search_text()

    assert "your channel → raid" in text
    assert "currently live" in text
    assert "viewer count" in text
    assert "uptime" in text
    assert "search" in text
    assert "refresh" in text
    assert "confirmation" in text
    assert "user:read:follows" in text
    assert "channel:manage:raids" in text
    assert "does not save" in text
    assert "raid message" in text
    assert "sent through hub's normal twitch chat" in text
    assert "never sends the message automatically" in text
    assert "90-second" in text
    assert "cancel raid" in text
    assert "twitch automatically executes the raid" in text
    assert "raid now" not in text
    assert "outgoing channel raid event" in text


def test_wiki_search_is_local_case_insensitive_and_does_not_mutate_sources() -> None:
    application = QApplication.instance() or QApplication([])
    tasks, variables = _reference_sources()
    task_snapshot = tasks.visible_metadata()
    variable_snapshot = variables.all_definitions()
    page = WikiPage(tasks, variables)

    for query, expected_id in (
        ("CoMmAnD.DaTa", "variable:command.data"),
        ("viewer_stream_total", "counter:scopes"),
        ("raid", "trigger:channel.raid"),
        ("OBS scene", "obs:capabilities"),
    ):
        assert expected_id in {entry.entry_id for entry in page.matching_entries(query)}

    page.search_edit.setText("no-such-reference-term")
    application.processEvents()
    assert page.entry_list.count() == 0
    assert "No matching" in page.title_label.text()
    assert tasks.visible_metadata() == task_snapshot
    assert variables.all_definitions() == variable_snapshot
    page.show()
    page.resize(700, 800)
    application.processEvents()
    assert page.splitter.orientation() == Qt.Orientation.Vertical
    page.resize(1100, 800)
    application.processEvents()
    assert page.splitter.orientation() == Qt.Orientation.Horizontal
    page.close()
    page.deleteLater()


def test_wiki_tasks_render_existing_task_library_reference_metadata() -> None:
    application = QApplication.instance() or QApplication([])
    tasks, variables = _reference_sources()
    page = WikiPage(tasks, variables)

    tasks_category = page.category_list.findItems("Tasks", Qt.MatchFlag.MatchExactly)[0]
    page.category_list.setCurrentItem(tasks_category)
    application.processEvents()
    assert page.select_entry("task:core.wait")
    application.processEvents()
    rendered = page.browser.toPlainText()
    assert "What it does" in rendered
    assert "Inputs" in rendered
    assert "Duration" in rendered
    assert "Variable placeholders" in rendered
    page.deleteLater()


def test_touch_portal_reference_documents_the_narrow_local_boundary() -> None:
    tasks, variables = _reference_sources()
    entry = next(
        item
        for item in build_wiki_entries(tasks, variables)
        if item.entry_id == "integration:touch-portal"
    )
    text = entry.search_text()

    assert "run streamhouse routine" in text
    assert "stable routine id" in text
    assert "normal queue" in text
    assert "loopback" in text
    assert "experimental" in text
    assert "does not fabricate" in text
    assert "command.*" in text
