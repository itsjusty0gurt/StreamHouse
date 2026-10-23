import os
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

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
from products.hub.integrations.music_player import MusicVariableProvider
from products.hub.obs_service.triggers import OBS_TRIGGER_TYPES
from products.hub.twitch.slash_commands import TWITCH_SLASH_COMMANDS
from products.hub.ui.wiki_page import WikiPage


def _reference_sources() -> tuple[TaskRegistry, VariableRegistry]:
    tasks = TaskRegistry(BUILTIN_TASK_METADATA)
    variables = VariableRegistry()
    variables.register(context_provider())
    variables.register(
        MusicVariableProvider(Mock(connected=False, playback_state=None))
    )
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
    assert "variable:music.title" in ids
    assert "commands:overview" in ids
    assert "integration:touch-portal" in ids


def test_generated_reference_has_unique_entries_and_exact_task_labels() -> None:
    tasks, variables = _reference_sources()
    entries = build_wiki_entries(tasks, variables)
    ids = [entry.entry_id for entry in entries]
    category_titles = [(entry.category, entry.title) for entry in entries]
    task_entries = {
        entry.entry_id.removeprefix("task:"): entry.title
        for entry in entries
        if entry.category == "Tasks"
    }

    assert len(ids) == len(set(ids))
    assert len(category_titles) == len(set(category_titles))
    assert task_entries == {
        metadata.task_type: metadata.label
        for metadata in tasks.visible_metadata()
    }


def test_getting_started_teaches_twitch_automation_and_variable_picker() -> None:
    tasks, variables = _reference_sources()
    entries = {
        entry.entry_id: entry
        for entry in build_wiki_entries(tasks, variables)
    }
    guide = entries["start:first-routine"].search_text()
    page_guide = entries["start:wiki-vs-pages"].search_text()

    assert "sign in with twitch" in guide
    assert "bot account is optional" in guide
    assert "trigger = what starts something" in guide
    assert "routine = the automation being run" in guide
    assert "task = something the routine does" in guide
    assert "twitch — first message of stream" in guide
    assert "twitch — send chat message" in guide
    assert "{user.display_name}" in guide
    assert "variable picker" in guide
    for destination in (
        "automation",
        "variables",
        "commands",
        "channel points",
        "counters",
        "recipes",
    ):
        assert destination in page_guide


def test_recipes_are_difficulty_labeled_and_use_current_ui_names() -> None:
    tasks, variables = _reference_sources()
    recipes = {
        entry.entry_id: entry
        for entry in build_wiki_entries(tasks, variables)
        if entry.category == "Recipes"
    }

    assert "Examples" not in WIKI_CATEGORIES
    assert "Recipes" in WIKI_CATEGORIES
    difficulties = {
        section.lines[0]
        for entry in recipes.values()
        for section in entry.sections
        if section.title == "Difficulty"
    }
    assert difficulties == {
        "Beginner",
        "Intermediate",
        "Advanced",
    }
    first_message = recipes["example:first-message-welcome"].search_text()
    assert "twitch — first message of stream" in first_message
    assert "twitch — send chat message" in first_message
    assert "{user.display_name}" in first_message

    channel_points = recipes["example:channel-point-response"].search_text()
    assert "twitch — channel point redemption" in channel_points
    assert "filtered to the chosen reward" in channel_points


def test_counter_and_first_redemption_recipes_document_twitch_stream_limits() -> None:
    tasks, variables = _reference_sources()
    entries = {
        entry.entry_id: entry.search_text()
        for entry in build_wiki_entries(tasks, variables)
    }

    coffee = entries["example:coffee-counter"]
    assert "limit per viewer per stream" in coffee
    assert "not once per calendar day" in coffee
    assert "per-viewer current-stream totals" in coffee
    assert "channel all-time total" in coffee
    assert "counter — increase" in coffee
    assert "{counter.coffee.user.stream}" in coffee

    first = entries["example:first-redemption"]
    assert "limit per stream" in first
    assert "set it to 1" in first
    assert "twitch then enforces one redemption for that stream" in first
    assert "no extra hub condition is needed" in first


def test_advanced_recipe_composes_counter_condition_chat_and_obs() -> None:
    tasks, variables = _reference_sources()
    recipe = next(
        entry
        for entry in build_wiki_entries(tasks, variables)
        if entry.entry_id == "example:redemption-milestone"
    )
    text = recipe.search_text()

    assert recipe.title.startswith("Advanced —")
    assert "counter — increase" in text
    assert "core — if" in text
    assert "twitch — send chat message" in text
    assert "obs — show, hide, or toggle source" in text
    assert "variable picker" in text


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
    assert "/shoutout <user>" in entry.search_text()
    assert "moderator:manage:shoutouts" in entry.search_text()


def test_shoutout_task_reference_is_derived_from_task_metadata() -> None:
    tasks, variables = _reference_sources()
    entry = next(
        item
        for item in build_wiki_entries(tasks, variables)
        if item.entry_id == "task:twitch.shoutout_user"
    )
    text = entry.search_text()

    assert "real twitch shoutout" in text
    assert "{command.data}" in text
    assert "{user.name}" in text
    assert "{user.id}" in text
    assert "twitch controls shoutout cooldowns" in text


def test_get_user_clip_reference_is_derived_from_task_metadata() -> None:
    tasks, variables = _reference_sources()
    entry = next(
        item
        for item in build_wiki_entries(tasks, variables)
        if item.entry_id == "task:twitch.get_user_clip"
    )
    text = entry.search_text()

    assert "normal shareable clip page url" in text
    assert "obs/browser-source iframe url" in text
    assert "parent=localhost" in text
    assert "autoplay=true" in text
    assert "muted=false" in text
    assert "random featured" in text
    assert "{automation.clip_url}" in text
    assert "{automation.clip_embed_url}" in text
    assert "{automation.clip_media_url}" not in text
    assert "same root routine execution" in text


def test_get_user_info_reference_documents_grouped_outputs_and_context_safety() -> None:
    tasks, variables = _reference_sources()
    entry = next(
        item
        for item in build_wiki_entries(tasks, variables)
        if item.entry_id == "task:twitch.get_user_info"
    )
    text = entry.search_text()

    assert "{automation.user.display_name}" in text
    assert "{automation.user.channel_url}" in text
    assert "triggering user context" in text
    assert "current or last configured" in text


def test_obs_browser_source_url_reference_is_derived_from_task_metadata() -> None:
    tasks, variables = _reference_sources()
    entry = next(
        item
        for item in build_wiki_entries(tasks, variables)
        if item.entry_id == "task:obs.set_browser_source_url"
    )
    text = entry.search_text()

    assert "selected obs browser source" in text
    assert "canonical variable" in text
    assert "{automation.clip_embed_url}" in text
    assert "active obs connection" in text


def test_obs_batch_one_references_are_derived_from_task_metadata() -> None:
    tasks, variables = _reference_sources()
    entries = {
        item.entry_id: item
        for item in build_wiki_entries(tasks, variables)
    }

    assert "local-file setting" in entries["task:obs.set_media_source_file"].search_text()
    audio_routing = entries["task:obs.set_source_audio_track"]
    assert audio_routing.title == "Set Source Audio Track Routing"
    assert "track 1 through track 6" in audio_routing.search_text()
    assert "advanced audio properties" in audio_routing.search_text()
    assert "multiple tracks" in audio_routing.search_text()
    assert "#rrggbb" in entries["task:obs.set_color_source_color"].search_text()
    assert "restart" in entries["task:obs.restart_media_source"].search_text()
    assert "scene transition" in entries["task:obs.set_transition"].search_text()


def test_obs_batch_two_references_are_derived_from_task_metadata() -> None:
    tasks, variables = _reference_sources()
    entries = {
        item.entry_id: item.search_text()
        for item in build_wiki_entries(tasks, variables)
    }

    screenshot = entries["task:obs.take_screenshot"]
    assert "source or scene" in screenshot
    assert "{automation.screenshot_path}" in screenshot
    assert "same root routine execution" in screenshot
    assert "scene-item id" in entries["task:obs.set_source_transform"]
    assert "blank to preserve" in entries["task:obs.set_source_transform"]
    assert "active recording" in entries["task:obs.create_record_chapter"]


def test_unified_user_groups_reference_documents_system_and_custom_groups() -> None:
    tasks, variables = _reference_sources()
    entry = next(
        item
        for item in build_wiki_entries(tasks, variables)
        if item.entry_id == "twitch:user-groups"
    )
    text = entry.search_text()

    assert "protected system groups" in text
    assert "bots drives hub's bot filtering" in text
    assert "regulars is maintained automatically" in text
    assert "viewers is the natural fallback" in text
    assert "stable twitch user ids" in text
    assert "user is in group" in text
    assert "auto shoutout" in text
    assert "{user.id}" in text


def test_first_message_reference_documents_stream_controls_and_data_safety() -> None:
    tasks, variables = _reference_sources()
    entry = next(
        item
        for item in build_wiki_entries(tasks, variables)
        if item.entry_id == "trigger:channel.chat.first_message"
    )
    text = entry.search_text()

    assert "authoritative twitch stream" in text
    assert "new stream id" in text
    assert "your channel → users" in text
    assert "reset first words for current stream" in text
    assert "trigger again during the same stream" in text
    assert "does not delete users" in text
    assert "raid suppression is separate" in text


def test_outgoing_raid_completed_reference_uses_authoritative_confirmation() -> None:
    tasks, variables = _reference_sources()
    entry = next(
        item
        for item in build_wiki_entries(tasks, variables)
        if item.entry_id == "trigger:channel.raid.outgoing"
    )
    text = entry.search_text()

    assert entry.title == "Twitch — Outgoing Raid Completed"
    assert "authoritative outgoing channel.raid eventsub confirmation" in text
    assert "starting a raid" in text
    assert "countdown reaching zero" in text
    assert "cancelling a raid" in text
    assert "default-browser action is a separate consumer" in text
    for variable in (
        "raid.target.id",
        "raid.target.login",
        "raid.target.name",
        "raid.viewers",
    ):
        assert variable in text


def test_python_script_context_helpers_and_now_playing_example_are_documented() -> None:
    tasks, variables = _reference_sources()
    entries = build_wiki_entries(tasks, variables)
    task = next(
        item for item in entries if item.entry_id == "task:core.run_python_script"
    )
    example = next(
        item for item in entries if item.entry_id == "example:python-now-playing"
    )

    task_text = task.search_text()
    example_text = example.search_text()
    assert "hub.set_output" in task_text
    assert "hub.get_variable" in task_text
    assert "hub.log" in task_text
    assert "same root execution" in task_text
    assert "braces are optional" in task_text
    assert "unavailable values raise a lookup error" in task_text
    assert "normal log" in task_text
    assert "only after a successful waited script" in task_text
    assert "{automation.artist} - {automation.song}" in example_text
    assert "no intermediate file" in example_text


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
    assert "opening the raid page automatically refreshes" in text
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
    assert "open raided channel in browser after raid" in text
    assert "normal default browser" in text
    assert "temporary target chat" in text
    assert "starting a raid, countdown expiry, cancellation" in text


def test_command_and_timer_references_document_stable_open_routine_navigation() -> None:
    tasks, variables = _reference_sources()
    entries = {
        item.entry_id: item.search_text()
        for item in build_wiki_entries(tasks, variables)
    }

    for entry_id in ("commands:overview", "trigger:timer"):
        text = entries[entry_id]
        assert "open routine" in text
        assert "stable id" in text
        assert "expands" in text
        assert "scrolls it into view" in text


def test_all_current_obs_triggers_have_exact_ui_names_and_event_descriptions() -> None:
    tasks, variables = _reference_sources()
    entries = {
        item.entry_id: item
        for item in build_wiki_entries(tasks, variables)
    }

    expected_summaries = {
        "ConnectionOpened": "usable obs websocket connection",
        "ConnectionClosed": "connection is lost or closed",
        "CurrentProgramSceneChanged": "program scene changes",
        "CurrentPreviewSceneChanged": "preview scene changes",
        "StreamStateChanged": "streaming output state change",
        "RecordStateChanged": "recording output state change",
        "ReplayBufferStateChanged": "replay-buffer state change",
        "SceneItemEnableStateChanged": "shown or hidden",
        "InputMuteStateChanged": "muted or unmuted",
        "InputVolumeChanged": "input volume changes",
        "MediaInputPlaybackStarted": "starts playback",
        "MediaInputPlaybackEnded": "end of playback",
        "StudioModeStateChanged": "enabled or disabled",
        "ExitStarted": "begins shutting down",
    }
    assert set(expected_summaries) == set(OBS_TRIGGER_TYPES)
    for event_type, label in OBS_TRIGGER_TYPES.items():
        entry = entries[f"trigger:{event_type}"]
        assert entry.title == f"OBS — {label}"
        assert expected_summaries[event_type] in entry.search_text()
        assert "friendly scene, input, source, and state controls" in entry.search_text()
        assert "remain editable while obs is disconnected" in entry.search_text()
        assert "advanced filters preserve canonical obs event-data field names" in entry.search_text()


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


def test_wiki_refresh_does_not_invalidate_retained_list_item_wrappers() -> None:
    application = QApplication.instance() or QApplication([])
    tasks, variables = _reference_sources()
    page = WikiPage(tasks, variables)
    retained = page.entry_list.item(0)
    retained_id = retained.data(Qt.ItemDataRole.UserRole)

    assert page.select_entry("variable:command.data")
    application.processEvents()

    assert isValid(retained)
    assert retained.data(Qt.ItemDataRole.UserRole) == retained_id
    page.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_wiki_search_selection_resize_and_destruction_stress() -> None:
    application = QApplication.instance() or QApplication([])
    tasks, variables = _reference_sources()
    queries = ("command.data", "raid", "OBS scene", "counter", "")
    entry_ids = (
        "variable:command.data",
        "trigger:channel.raid",
        "task:core.wait",
    )

    for cycle in range(20):
        page = WikiPage(tasks, variables)
        page.show()
        for iteration in range(100):
            page.search_edit.setText(queries[iteration % len(queries)])
            page.resize(680 if iteration % 2 else 1_080, 760)
            if iteration % 5 == 4:
                assert page.select_entry(entry_ids[iteration % len(entry_ids)])
            if iteration % 20 == 19:
                application.processEvents()
        page.close()
        page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not isValid(page), f"Wiki page survived deferred deletion in cycle {cycle}"


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


def test_music_player_reference_uses_current_variables_and_tasks() -> None:
    tasks, variables = _reference_sources()
    entries = build_wiki_entries(tasks, variables)
    entry = next(
        item for item in entries if item.entry_id == "integration:music-player"
    )
    text = entry.search_text()

    assert "127.0.0.1" in text
    assert "protocol 1" in text
    assert "no streamhouse cloud" in text
    assert "automatically discovers" in text
    assert "no manual port or token setup" in text
    assert "music — next track" in text
    assert "{music.artist} - {music.title}" in text
    assert "unavailable" in text
    assert {
        metadata.task_type
        for metadata in tasks.visible_metadata()
        if metadata.category == "Music"
    } == {
        "music.play",
        "music.pause",
        "music.play_pause",
        "music.next",
        "music.previous",
        "music.set_volume",
        "music.set_muted",
    }


def test_music_trigger_reference_documents_v1_transitions_and_baseline() -> None:
    tasks, variables = _reference_sources()
    entries = {
        item.entry_id: item for item in build_wiki_entries(tasks, variables)
    }
    expected = {
        "track.changed",
        "playback.started",
        "playback.paused",
        "playback.stopped",
        "volume.changed",
        "player.connected",
        "player.disconnected",
    }
    assert expected == {
        event_type
        for event_type in expected
        if f"trigger:{event_type}" in entries
    }
    track_text = entries["trigger:track.changed"].search_text()
    assert "first state snapshot" in track_text
    assert "position synchronization does not fire" in track_text
    assert "{music.artist} - {music.title}" in track_text


def test_twitch_moderation_trigger_reference_documents_types_context_and_filters() -> None:
    tasks, variables = _reference_sources()
    entries = {
        item.entry_id: item for item in build_wiki_entries(tasks, variables)
    }
    expected = {
        "channel.ban.permanent": "Twitch — User Banned",
        "channel.ban.timeout": "Twitch — User Timed Out",
        "channel.unban": "Twitch — User Unbanned",
        "channel.moderator.add": "Twitch — Moderator Added",
        "channel.moderator.remove": "Twitch — Moderator Removed",
        "channel.unban_request.create": "Twitch — Unban Request Created",
        "channel.unban_request.resolve": "Twitch — Unban Request Resolved",
    }

    for event_type, title in expected.items():
        entry = entries[f"trigger:{event_type}"]
        assert entry.title == title
        text = entry.search_text()
        assert "{user.id}" in text
        if event_type != "channel.unban_request.create":
            assert "{moderator.id}" in text
        assert "user — leave as any" in text
    resolved = entries["trigger:channel.unban_request.resolve"].search_text()
    assert "status — any, approved, or denied" in resolved
    assert "{event.resolution_text}" in resolved
