from __future__ import annotations

from products.hub.automation.control_tasks import CONTROL_TASK_LABELS
from products.hub.automation.core_tasks import CORE_TASK_LABELS
from products.hub.automation.file_tasks import FILE_TASK_LABELS
from products.hub.automation.logic_tasks import LOGIC_TASK_LABELS
from products.hub.automation.tasks import TaskMetadata
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
    )
    for task_type, label in _LABELS.items()
)
