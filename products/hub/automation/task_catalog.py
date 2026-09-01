from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from products.hub.automation.control_tasks import CONTROL_TASK_LABELS
from products.hub.automation.core_tasks import CORE_TASK_LABELS
from products.hub.automation.file_tasks import FILE_TASK_LABELS
from products.hub.automation.logic_tasks import (
    COMPARISON_CHOICES,
    LOGIC_TASK_LABELS,
    UNARY_OPERATORS,
)
from products.hub.automation.tasks import (
    TaskCardSummaryFormatter,
    TaskInputHelp,
    TaskMetadata,
    TaskReferenceResolver,
)
from products.hub.automation.variable_tasks import VARIABLE_MANAGEMENT_TASK_TYPES
from products.hub.counters.tasks import COUNTER_TASK_LABELS
from products.hub.obs_service.tasks import OBS_TASK_LABELS
from products.hub.twitch.tasks import TWITCH_TASK_LABELS


_SHORT_DESCRIPTIONS = {
    "twitch.send_chat_message": (
        "Sends a message to Twitch chat using the configured chat account. "
        "The message supports Variables."
    ),
    "twitch.resolve_user": (
        "Finds a Twitch account from a user ID, login, or Variable. The account "
        "details become routine-scoped automation.* outputs for later tasks."
    ),
    "twitch.get_stream_information": (
        "Retrieves the channel's current Twitch stream details. The results become "
        "routine-scoped automation.* outputs for later tasks."
    ),
    "twitch.get_follow_relationship": (
        "Checks whether a selected Twitch user follows the channel. The result "
        "becomes routine-scoped automation.* output data for later tasks."
    ),
    "twitch.build_command_list": (
        "Builds a viewer-appropriate list of enabled Chat Commands. The text "
        "becomes a routine-scoped automation.* output for later tasks."
    ),
    "twitch.build_social_links_message": (
        "Builds the configured social-links message. The text becomes a "
        "routine-scoped automation.* output for later tasks."
    ),
    "twitch.send_pinned_message": (
        "Sends a Twitch chat message and pins it when the channel supports pinned "
        "messages. The message supports Variables."
    ),
    "twitch.run_commercial": (
        "Starts a Twitch commercial using the selected supported duration."
    ),
    "twitch.snooze_ad": (
        "Snoozes the next scheduled Twitch ad break when a snooze is available."
    ),
    "twitch.update_stream_title": (
        "Changes the channel's current Twitch stream title. The title supports "
        "Variables."
    ),
    "twitch.update_stream_category": (
        "Changes the channel's current Twitch category by name."
    ),
    "twitch.moderate_user": (
        "Performs the selected moderation action for a Twitch user or chat "
        "message, such as timeout, ban, unban, or delete."
    ),
    "twitch.update_redemption": (
        "Marks a Channel Point redemption as fulfilled or refunded."
    ),
    "counter.increase": (
        "Adds an amount to the selected Counter. The amount may be a number or "
        "a Variable."
    ),
    "counter.decrease": (
        "Subtracts an amount from the selected Counter. The amount may be a "
        "number or a Variable."
    ),
    "counter.set_value": (
        "Replaces the selected Counter's value with a number or resolved Variable."
    ),
    "counter.reset": (
        "Restores the selected Counter to its configured reset value."
    ),
    "core.launch_application": (
        "Starts an application with optional arguments and working-directory "
        "settings."
    ),
    "core.close_application": (
        "Closes a running application selected by its executable name."
    ),
    "core.wait": (
        "Waits for a specified duration before the routine continues. Supports "
        "milliseconds, seconds, minutes, and Variables while Hub stays responsive."
    ),
    "core.random_delay": (
        "Waits for a randomly selected duration within the configured range."
    ),
    "core.wait_for_service": (
        "Waits until Twitch or OBS is connected, or until the configured timeout "
        "is reached."
    ),
    "core.open_target": (
        "Opens a file, folder, or URL using the default Windows application."
    ),
    "core.show_notification": (
        "Shows a desktop notification from Streamhouse Hub. The notification "
        "text supports Variables."
    ),
    "core.run_python_script": (
        "Runs a trusted local Python script in a separate process. Use only scripts "
        "you understand and trust."
    ),
    "core.play_audio": (
        "Plays a local audio file through the selected output device."
    ),
    "core.create_global_variable": (
        "Creates or replaces a persisted custom.* Variable for use across Hub "
        "launches."
    ),
    "core.create_session_variable": (
        "Creates or replaces a custom.* Variable that lasts until Streamhouse Hub "
        "closes."
    ),
    "core.create_routine_variable": (
        "Creates a routine-scoped automation.* output for later tasks in the "
        "current execution."
    ),
    "core.delete_variable": (
        "Deletes a writable custom Variable from its owning Variable store."
    ),
    "core.adjust_variable": (
        "Adds or subtracts a numeric amount from a writable custom Variable."
    ),
    "core.toggle_variable": (
        "Switches a writable Boolean custom Variable between true and false."
    ),
    "core.run_routine": (
        "Runs another routine as part of the current execution. Nested tasks share "
        "the current routine context."
    ),
    "core.logic_break": (
        "Stops the current routine at this task without running later tasks."
    ),
    "core.logic_get_input": (
        "Asks for text while the routine runs. The response becomes a "
        "routine-scoped automation.* output for later tasks."
    ),
    "core.logic_random_number": (
        "Generates an integer or decimal within the configured range as a "
        "routine-scoped automation.* output."
    ),
    "core.logic_random_choice": (
        "Chooses one configured branch by weight and runs its routine."
    ),
    "core.logic_if_else": (
        "Compares values and runs either the matching or fallback routine. Values "
        "may use Variables."
    ),
    "core.logic_switch": (
        "Matches one value against configured cases and runs the selected routine."
    ),
    "core.logic_while": (
        "Repeats a nested routine while a condition remains true, within the "
        "configured safety limit."
    ),
    "core.file_read": (
        "Reads text from a file into a routine-scoped automation.* output for "
        "later tasks."
    ),
    "core.file_random_line": (
        "Reads a random line from a text file into a routine-scoped automation.* "
        "output."
    ),
    "core.file_specific_line": (
        "Reads a selected line from a text file into a routine-scoped automation.* "
        "output."
    ),
    "core.file_write": (
        "Writes or appends text to a local file. The path and text support Variables."
    ),
    "core.path_exists": (
        "Checks whether a local file or folder exists and stores the Boolean result "
        "as a routine-scoped automation.* output."
    ),
    "core.file_count_lines": (
        "Counts lines in a text file and stores the number as a routine-scoped "
        "automation.* output."
    ),
    "core.set_routine_state": (
        "Enables, disables, or toggles another routine."
    ),
    "core.set_task_state": (
        "Enables, disables, or toggles a task in another routine."
    ),
    "core.set_queue_state": (
        "Pauses, resumes, or toggles an Automation queue."
    ),
    "core.clear_queue": (
        "Removes all pending runs from the selected Automation queue."
    ),
    "core.format_duration": (
        "Formats a duration or timestamp as readable text and exposes it through "
        "routine-scoped automation.* outputs."
    ),
    "core.select_text": (
        "Chooses text from configured value rules and exposes it as a "
        "routine-scoped automation.* output."
    ),
    "obs.set_program_scene": "Changes the live OBS program scene.",
    "obs.set_preview_scene": "Changes the OBS preview scene in Studio Mode.",
    "obs.set_scene_item_enabled": (
        "Shows, hides, or toggles a source within an OBS scene."
    ),
    "obs.set_input_mute": "Mutes, unmutes, or toggles an OBS audio input.",
    "obs.set_input_volume": "Sets the volume of an OBS audio input.",
    "obs.set_source_filter_state": (
        "Enables, disables, or toggles a filter on an OBS source."
    ),
    "obs.set_scene_filter_state": (
        "Enables, disables, or toggles a filter on an OBS scene."
    ),
    "obs.set_text_source": (
        "Changes the text of an OBS text source. The text supports Variables."
    ),
    "obs.set_image_source": (
        "Changes the file used by an OBS image source. The path supports Variables."
    ),
    "obs.stream_control": "Starts or stops streaming through OBS.",
    "obs.record_control": "Starts, stops, pauses, or resumes OBS recording.",
    "obs.replay_buffer_control": "Starts, stops, or saves the OBS replay buffer.",
    "obs.media_control": "Controls playback for an OBS media source.",
    "obs.trigger_hotkey": "Triggers a configured OBS hotkey by name.",
    "obs.set_studio_mode": "Enables or disables OBS Studio Mode.",
    "obs.raw_request": (
        "Sends an advanced OBS WebSocket request using a request type and JSON "
        "payload."
    ),
}


VARIABLE_INPUT_FIELDS: dict[str, tuple[str, ...]] = {
    "core.wait": ("duration",),
    "twitch.send_chat_message": ("message",),
    "twitch.send_pinned_message": ("message",),
    "twitch.update_stream_title": ("title",),
    "twitch.update_stream_category": ("category",),
    "twitch.moderate_user": ("user", "reason", "message_id"),
    "twitch.update_redemption": ("reward_id", "redemption_id"),
    "twitch.resolve_user": ("reference",),
    "twitch.get_follow_relationship": ("user_id",),
    "core.create_global_variable": ("value",),
    "core.create_session_variable": ("value",),
    "core.create_routine_variable": ("value",),
    "core.format_duration": ("start", "end", "seconds"),
    "core.select_text": ("selector", "cases", "default"),
    "core.logic_get_input": ("title", "prompt", "default"),
    "core.logic_if_else": ("left", "right"),
    "core.logic_switch": ("input",),
    "core.logic_while": ("left", "right"),
    "core.run_python_script": ("script", "arguments", "working_directory"),
    "core.show_notification": ("title", "message"),
    "core.file_read": ("path",),
    "core.file_random_line": ("path",),
    "core.file_specific_line": ("path", "line_number"),
    "core.file_write": ("path", "text"),
    "core.path_exists": ("path",),
    "core.file_count_lines": ("path",),
    "obs.set_text_source": ("text",),
    "obs.set_image_source": ("file",),
    "counter.increase": ("amount",),
    "counter.decrease": ("amount",),
    "counter.set_value": ("value",),
}


_HELP_TEXT = {
    "twitch.resolve_user": (
        "Looks up one Twitch account and makes its stable ID, login, display name, "
        "creation date, and lookup status available to later tasks."
    ),
    "twitch.get_stream_information": (
        "Reads the current channel state from Twitch, including live status, title, "
        "category, start time, viewer count, and stream identifiers."
    ),
    "twitch.moderate_user": (
        "Runs one explicit moderation action. Timeout, ban, unban, and message "
        "deletion use only the fields needed by the selected action."
    ),
    "core.wait": (
        "Pauses this routine for the selected duration without freezing Streamhouse "
        "Hub. Other eligible queues and the interface keep working."
    ),
    "core.run_routine": (
        "Runs another routine inline, then returns to the next task in this routine. "
        "The child shares the current trigger data and cancellation state."
    ),
    "core.logic_if_else": (
        "Compares two values and optionally runs one routine when the comparison is "
        "true and another when it is false."
    ),
    "core.logic_switch": (
        "Matches one value against a list of cases and runs the routine assigned to "
        "the first matching case, or the optional default routine."
    ),
    "core.logic_while": (
        "Repeats a selected routine while a comparison remains true. Iteration and "
        "time limits prevent an accidental endless loop."
    ),
    "core.run_python_script": (
        "Starts a trusted local Python script as a separate process. It can wait for "
        "completion and record captured output in Run History."
    ),
    "core.select_text": (
        "Chooses a text template by matching an input value, then stores the rendered "
        "text in a routine-scoped automation.* output."
    ),
    "obs.set_scene_item_enabled": (
        "Shows or hides a source in a specific scene. Hub first finds that scene "
        "item, then waits for OBS to confirm the requested state."
    ),
    "obs.raw_request": (
        "Sends one advanced OBS WebSocket request and waits for its response. Use "
        "this only when a dedicated OBS task does not cover the operation."
    ),
}


_INPUT_HELP: dict[str, dict[str, str]] = {
    "twitch.send_chat_message": {
        "message": "The chat message to send.",
        "as_bot": "Uses the configured bot account instead of the broadcaster account.",
    },
    "twitch.resolve_user": {
        "reference": "A Twitch user ID, login, @login, or Variable containing one.",
    },
    "twitch.moderate_user": {
        "user": "The stable Twitch user ID or login to moderate.",
        "duration_seconds": "How long a timeout lasts; ignored by other actions.",
        "message_id": "The Twitch message ID; used only for Delete message.",
    },
    "counter.increase": {
        "scope": "Which stored value is changed: shared, current stream, or triggering viewer.",
        "amount": "The number to add. It may resolve from a Variable.",
    },
    "counter.decrease": {
        "scope": "Which stored value is changed: shared, current stream, or triggering viewer.",
        "amount": "The number to subtract. It may resolve from a Variable.",
    },
    "counter.set_value": {
        "scope": "Which stored value is replaced: shared, current stream, or triggering viewer.",
        "value": "The new numeric value. It may resolve from a Variable.",
    },
    "counter.reset": {
        "scope": "Which stored value returns to the Counter's configured reset value.",
    },
    "core.wait": {
        "duration": "How long the routine waits before continuing.",
        "unit": "Interprets the duration as milliseconds, seconds, or minutes.",
    },
    "core.wait_for_service": {
        "service": "The connection Hub waits for.",
        "timeout_seconds": "The longest time to wait before the task fails.",
    },
    "core.play_audio": {
        "wait_for_completion": "When enabled, later tasks wait until playback finishes.",
        "timeout_seconds": "Stops waiting if playback does not finish in time.",
    },
    "core.create_global_variable": {
        "name": "The canonical custom.* name without the custom. prefix.",
        "value": "The persisted value; Variables in this field resolve when the task runs.",
    },
    "core.create_session_variable": {
        "name": "The canonical custom.* name without the custom. prefix.",
        "value": "The value kept until Hub closes.",
    },
    "core.create_routine_variable": {
        "name": "The automation.* output name without the automation. prefix.",
        "value": "The value available to later tasks and nested routines in this run.",
    },
    "core.run_routine": {
        "stop_on_failure": "Stops the parent routine when the nested routine fails.",
    },
    "core.clear_queue": {
        "queue_id": "The queue whose waiting items are removed. Its active routine is not stopped.",
    },
    "core.logic_get_input": {
        "name": "The automation.* output name used by later tasks.",
        "break_on_cancel": "Stops the routine when the input window is cancelled.",
    },
    "core.logic_random_number": {
        "name": "The automation.* output name used by later tasks.",
        "mode": "Generates either an integer in a range or a decimal from 0 to 1.",
    },
    "core.logic_while": {
        "max_iterations": "Maximum repeats even if the condition remains true.",
        "timeout_seconds": "Maximum total time spent repeating this loop.",
    },
    "core.run_python_script": {
        "python_executable": "Optional Python program; blank uses Hub's Python when available.",
        "wait_for_completion": "Waits for the process and uses its exit status as the task result.",
        "capture_output": "Includes the script's output in Run History.",
    },
    "core.file_write": {
        "mode": "Replaces the file or adds the new text to its end.",
        "create_parent_folders": "Creates missing folders leading to the file.",
    },
    "core.format_duration": {
        "start": "Start date/time. Use this with End, or leave both blank and provide seconds.",
        "seconds": "A duration in seconds used instead of Start and End.",
        "output_variable": "The automation.* base name exposed to later tasks.",
    },
    "core.select_text": {
        "cases": "A JSON object mapping input values to text templates.",
        "default": "Text used when no configured value matches.",
        "output_variable": "The automation.* name exposed to later tasks.",
    },
    "obs.set_scene_item_enabled": {
        "scene": "The OBS scene containing the source item.",
        "source": "The source item to show or hide in that scene.",
        "action": "Show and Hide are deterministic; Toggle reverses the current state.",
    },
    "obs.set_source_filter_state": {
        "source": "The OBS source that owns the filter.",
        "filter": "The filter to enable or disable.",
        "action": "Enable and Disable are deterministic; Toggle reverses the current state.",
    },
    "obs.set_scene_filter_state": {
        "scene": "The OBS scene that owns the filter.",
        "filter": "The filter to enable or disable.",
        "action": "Enable and Disable are deterministic; Toggle reverses the current state.",
    },
    "obs.raw_request": {
        "request_type": "The exact OBS WebSocket request type.",
        "request_data": "A JSON object containing that request's data.",
    },
}


_NOTES = {
    "twitch.send_pinned_message": ("Pinned messages may not be available for every channel.",),
    "twitch.run_commercial": ("Twitch cooldowns and channel eligibility still apply.",),
    "twitch.snooze_ad": ("The task fails if no snooze is currently available.",),
    "twitch.update_stream_category": ("The category name must match a category Twitch can resolve.",),
    "counter.increase": ("Viewer values require a triggering viewer with a stable Twitch user ID.",),
    "counter.decrease": ("Viewer values require a triggering viewer with a stable Twitch user ID.",),
    "counter.set_value": ("Viewer values require a triggering viewer with a stable Twitch user ID.",),
    "counter.reset": ("Reset uses the Counter's configured starting/reset value.",),
    "core.wait": ("Stopping the current routine interrupts an active Wait.",),
    "core.close_application": ("Force close can discard unsaved work in the target application.",),
    "core.run_routine": ("Routine nesting is limited to ten levels and recursive loops are blocked.",),
    "core.clear_queue": ("This removes pending items only; use Stop Queue in the Queues tab to stop the active routine too.",),
    "core.logic_break": ("Tasks after End Routine do not run.",),
    "core.logic_while": ("The repeated routine runs inline and shares the current routine context.",),
    "core.run_python_script": ("Only run scripts you understand and trust.",),
    "obs.set_scene_item_enabled": ("Prefer Show or Hide when the final state matters.",),
    "obs.set_input_mute": ("Prefer Mute or Unmute when the final state matters.",),
    "obs.set_source_filter_state": ("Prefer Enable or Disable when the final state matters.",),
    "obs.set_scene_filter_state": ("Prefer Enable or Disable when the final state matters.",),
    "obs.stream_control": ("Stop streaming ends the live broadcast immediately after OBS confirms it.",),
    "obs.raw_request": ("Malformed local JSON fails before anything is sent to OBS.",),
}


_EXAMPLES = {
    "twitch.send_chat_message": ("Send: Thanks for the follow, {user.display_name}!",),
    "twitch.resolve_user": ("Resolve {command.data}, then use {automation.target_user_id} in a later task.",),
    "twitch.build_social_links_message": ("Build the included links, then send {automation.social_links_message} to chat.",),
    "counter.increase": ("Increase Deaths by 1.",),
    "counter.set_value": ("Set Coffee Drank to {command.data}.",),
    "core.wait": ("Show overlay → Wait 8 seconds → Hide overlay.",),
    "core.random_delay": ("Wait between 2 and 5 seconds before sending a response.",),
    "core.show_notification": ("Show “Raid incoming” when a raid trigger runs.",),
    "core.create_routine_variable": ("Create automation.winner, then use {automation.winner} in later tasks.",),
    "core.run_routine": ("Run “Play raid alert”, then continue this routine.",),
    "core.logic_if_else": ("If {event.viewers} is greater than 20, run the large-raid routine.",),
    "core.logic_switch": ("Match {event.reward} and run the routine assigned to that reward.",),
    "core.file_random_line": ("Read one quote, then send {automation.random_line} to chat.",),
    "core.file_write": ("Append {user.display_name} to a giveaway entries file.",),
    "core.format_duration": ("Format {ads.remaining}, then use the generated output in chat.",),
    "obs.set_program_scene": ("Change the live scene to BRB.",),
    "obs.set_scene_item_enabled": ("Show Raid Overlay → Wait 8 seconds → Hide Raid Overlay.",),
    "obs.set_source_filter_state": ("Enable Glow → Wait 1.5 seconds → Disable Glow.",),
    "obs.set_text_source": ("Set a text source to Now playing: {stream.category}.",),
    "obs.set_image_source": ("Change an image source file, then show that source.",),
}


def _requirements(task_type: str) -> tuple[str, ...]:
    if task_type.startswith("obs."):
        requirements = ["Requires an active OBS connection."]
        if task_type == "obs.set_preview_scene":
            requirements.append("OBS Studio Mode must be available.")
        return tuple(requirements)
    if task_type.startswith("counter."):
        return ("Requires a configured Counter.",)
    if task_type in {"twitch.send_chat_message", "twitch.send_pinned_message"}:
        return ("Requires Twitch chat and the selected sending account.",)
    if task_type in {
        "twitch.build_command_list",
        "twitch.build_social_links_message",
    }:
        return ()
    if task_type.startswith("twitch."):
        return ("Requires Twitch broadcaster authorization for this action.",)
    if task_type.startswith("core.file_") or task_type == "core.path_exists":
        return ("Requires access to the selected local file or folder.",)
    if task_type == "core.play_audio":
        return ("Requires a readable local audio file and output device.",)
    if task_type == "core.run_python_script":
        return ("Requires a trusted local Python script.",)
    return ()


def _leaf_label(label: str) -> str:
    for separator in (" — ", " - "):
        if separator in label:
            return label.split(separator, 1)[1].strip()
    return label.strip()


def _category(task_type: str) -> str:
    if task_type.startswith("twitch."):
        return "Twitch"
    if task_type.startswith("counter."):
        return "Counters"
    if task_type.startswith("obs."):
        return "OBS"
    if task_type == "core.run_python_script":
        return "Core / Scripts"
    if task_type in VARIABLE_MANAGEMENT_TASK_TYPES:
        return "Core / Variables"
    if task_type in LOGIC_TASK_LABELS:
        return "Core / Logic"
    if task_type in FILE_TASK_LABELS:
        return "Core / Files"
    if task_type in CONTROL_TASK_LABELS:
        return "Core / Control"
    return "Core"


_CARD_SUMMARY_SKIP_KEYS = frozenset(
    {
        "as_bot",
        "enabled",
        "stop_on_failure",
        "wait_for_completion",
    }
)


def _summary_text(value: object, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else f"{text[: limit - 1].rstrip()}…"


def _resolved(
    config: Mapping[str, Any],
    key: str,
    kind: str,
    resolver: TaskReferenceResolver | None,
) -> str:
    value = _summary_text(config.get(key, ""))
    if not value or resolver is None:
        return value
    return _summary_text(resolver(kind, value) or value)


def _unit_abbreviation(unit: object) -> str:
    return {
        "milliseconds": "ms",
        "seconds": "sec",
        "minutes": "min",
        "hours": "hr",
    }.get(str(unit).strip().casefold(), _summary_text(unit))


def _condition_summary(config: Mapping[str, Any]) -> str:
    left = _summary_text(config.get("left", "value")) or "value"
    operator = str(config.get("operator", "equals"))
    label = next(
        (text for text, value in COMPARISON_CHOICES if value == operator),
        operator.replace("_", " ").title(),
    )
    if operator in UNARY_OPERATORS:
        return f"{left} {label.casefold()}"
    right = _summary_text(config.get("right", ""))
    symbols = {
        "equals": "=",
        "not_equals": "≠",
        "less_than": "<",
        "less_or_equal": "≤",
        "greater_than": ">",
        "greater_or_equal": "≥",
    }
    return f"{left} {symbols.get(operator, label)} {right}".strip()


def _generic_card_summary(config: Mapping[str, Any]) -> str:
    visible = [
        (str(key), value)
        for key, value in config.items()
        if key not in _CARD_SUMMARY_SKIP_KEYS
        and value not in (None, "", [], {})
        and not isinstance(value, (dict, list))
    ]
    if not visible:
        return ""
    primary_keys = (
        "message",
        "text",
        "title",
        "scene",
        "source",
        "input",
        "filter",
        "path",
        "target",
        "name",
        "value",
        "action",
    )
    ordered = sorted(
        visible,
        key=lambda item: (
            primary_keys.index(item[0]) if item[0] in primary_keys else len(primary_keys),
            item[0],
        ),
    )
    return " · ".join(_summary_text(value, 60) for _key, value in ordered[:2])


def _card_summary(
    task_type: str,
    config: Mapping[str, Any],
    resolver: TaskReferenceResolver | None,
) -> str:
    if task_type == "core.wait":
        return f"{_summary_text(config.get('duration', ''))} {_unit_abbreviation(config.get('unit', 'seconds'))}".strip()
    if task_type == "core.random_delay":
        unit = _unit_abbreviation(config.get("unit", "seconds"))
        return f"{config.get('minimum', '')}–{config.get('maximum', '')} {unit}".strip()
    if task_type in {"twitch.send_chat_message", "twitch.send_pinned_message"}:
        message = _summary_text(config.get("message", ""))
        return f'“{message}”' if message else ""
    if task_type == "obs.set_scene_item_enabled":
        scene = _summary_text(config.get("scene", ""))
        source = _summary_text(config.get("source", ""))
        action = _summary_text(config.get("action", "show")).title()
        target = " / ".join(value for value in (scene, source) if value)
        return f"{target} → {action}" if target else action
    if task_type in {"counter.increase", "counter.decrease"}:
        counter = _resolved(config, "counter_id", "counter", resolver) or "Counter"
        amount = _summary_text(config.get("amount", "1")).lstrip("+-")
        sign = "+" if task_type == "counter.increase" else "−"
        return f"{counter} {sign}{amount}"
    if task_type == "counter.set_value":
        counter = _resolved(config, "counter_id", "counter", resolver) or "Counter"
        return f"{counter} → {_summary_text(config.get('value', ''))}".rstrip()
    if task_type == "counter.reset":
        counter = _resolved(config, "counter_id", "counter", resolver) or "Counter"
        return f"{counter} → Reset"
    if task_type == "core.run_routine":
        return _resolved(config, "routine_id", "routine", resolver)
    if task_type == "core.logic_break":
        return "Stop this routine here"
    if task_type == "core.logic_if_else":
        return _condition_summary(config)
    if task_type in {"obs.set_program_scene", "obs.set_preview_scene"}:
        return _summary_text(config.get("scene", ""))
    if task_type in {"obs.set_source_filter_state", "obs.set_scene_filter_state"}:
        owner = _summary_text(config.get("source", config.get("scene", "")))
        filter_name = _summary_text(config.get("filter", ""))
        action = _summary_text(config.get("action", "enable")).title()
        target = " / ".join(value for value in (owner, filter_name) if value)
        return f"{target} → {action}" if target else action
    return _generic_card_summary(config)


def _card_summary_formatter(task_type: str) -> TaskCardSummaryFormatter:
    return lambda config, resolver: _card_summary(task_type, config, resolver)


_LABELS = {
    **TWITCH_TASK_LABELS,
    **COUNTER_TASK_LABELS,
    **CORE_TASK_LABELS,
    **OBS_TASK_LABELS,
}

BUILTIN_TASK_METADATA = tuple(
    TaskMetadata(
        task_type=task_type,
        label=_leaf_label(label),
        short_description=_SHORT_DESCRIPTIONS.get(task_type, ""),
        category=_category(task_type),
        help_text=_HELP_TEXT.get(
            task_type,
            _SHORT_DESCRIPTIONS.get(task_type, ""),
        ),
        input_help=tuple(
            TaskInputHelp(key, description)
            for key, description in _INPUT_HELP.get(task_type, {}).items()
        ),
        variable_inputs=VARIABLE_INPUT_FIELDS.get(task_type, ()),
        requirements=_requirements(task_type),
        notes=_NOTES.get(task_type, ()),
        examples=_EXAMPLES.get(task_type, ()),
        card_summary_formatter=_card_summary_formatter(task_type),
    )
    for task_type, label in _LABELS.items()
)
