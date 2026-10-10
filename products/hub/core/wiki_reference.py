from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from products.hub.automation.core_triggers import (
    CORE_TRIGGER_TYPES,
    MINIMUM_TIMER_MILLISECONDS,
    TIMER_MODES,
    TIMER_UNIT_LABELS,
)
from products.hub.automation.tasks import TaskMetadata, TaskRegistry
from products.hub.automation.variable_outputs import (
    generated_output_definitions,
    output_config_key,
)
from products.hub.automation.variable_registry import VariableRegistry
from products.hub.counters.models import SCOPES
from products.hub.obs_service.triggers import OBS_TRIGGER_TYPES
from products.hub.integrations.music_triggers import MUSIC_TRIGGER_TYPES
from products.hub.twitch.automation_triggers import (
    ADS_TRIGGER_TYPES,
    KEYWORD_PHRASE_EVENT_TYPE,
    TWITCH_EVENT_AUTOMATION_TYPES,
    twitch_trigger_display_name,
)
from products.hub.twitch.default_commands import default_command_definitions
from products.hub.twitch.slash_commands import TWITCH_SLASH_COMMANDS
from products.hub.twitch.raid_contract import RAID_COUNTDOWN_SECONDS
from products.hub.integrations.music_player import MUSIC_VARIABLE_DEFINITIONS


WIKI_CATEGORIES = (
    "Getting Started",
    "Tasks",
    "Triggers",
    "Variables",
    "Queues & Control Flow",
    "Counters",
    "Commands",
    "Twitch",
    "OBS",
    "Integrations",
    "Examples",
)


@dataclass(frozen=True, slots=True)
class WikiSection:
    title: str
    lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WikiEntry:
    entry_id: str
    category: str
    title: str
    summary: str
    sections: tuple[WikiSection, ...] = ()
    keywords: tuple[str, ...] = ()
    copy_text: str = ""

    def search_text(self) -> str:
        return " ".join(
            (
                self.category,
                self.title,
                self.summary,
                *self.keywords,
                *(section.title for section in self.sections),
                *(line for section in self.sections for line in section.lines),
            )
        ).casefold()


def build_wiki_entries(
    task_registry: TaskRegistry,
    variable_registry: VariableRegistry,
    *,
    task_schemas: Mapping[str, Sequence[Mapping[str, object]]] | None = None,
) -> tuple[WikiEntry, ...]:
    """Build the read-only Hub reference from current runtime definitions."""
    schemas = task_schemas or {}
    entries: list[WikiEntry] = []
    entries.extend(_getting_started_entries())
    entries.extend(
        _task_entry(metadata, schemas.get(metadata.task_type, ()))
        for metadata in task_registry.visible_metadata()
    )
    entries.extend(_trigger_entries(variable_registry))
    entries.extend(_variable_entries(variable_registry))
    entries.extend(_control_flow_entries(task_registry))
    entries.extend(_counter_entries(variable_registry))
    entries.extend(_command_entries())
    entries.extend(_twitch_entries())
    entries.extend(_obs_entries(task_registry))
    entries.extend(_integration_entries())
    entries.extend(_example_entries())
    order = {category: index for index, category in enumerate(WIKI_CATEGORIES)}
    return tuple(
        sorted(
            entries,
            key=lambda entry: (
                order.get(entry.category, len(order)),
                entry.title.casefold(),
                entry.entry_id,
            ),
        )
    )


def _task_entry(
    metadata: TaskMetadata,
    schema: Sequence[Mapping[str, object]],
) -> WikiEntry:
    sections = [
        WikiSection("What it does", (metadata.help_text or metadata.short_description,))
    ]
    if schema:
        inputs = []
        for spec in schema:
            key = str(spec.get("key", ""))
            label = str(spec.get("label") or spec.get("text") or key)
            detail = metadata.input_description(key) or _field_format(spec)
            if key in metadata.variable_inputs:
                detail = f"{detail.rstrip('.')} Supports canonical Variable placeholders."
            inputs.append(f"{label} — {detail}")
        sections.append(WikiSection("Inputs", tuple(inputs)))
    if metadata.variable_inputs:
        sections.append(
            WikiSection(
                "Variable placeholders",
                tuple(
                    f"{key} accepts canonical placeholders when their context is available."
                    for key in metadata.variable_inputs
                ),
            )
        )
    default_config = {
        str(spec.get("key", "")): spec.get("default") for spec in schema
    }
    outputs = generated_output_definitions(
        metadata.task_type,
        default_config,
        source=metadata.label,
    )
    if outputs:
        sections.append(
            WikiSection(
                "Outputs",
                tuple(f"{item.placeholder} — {item.description}" for item in outputs),
            )
        )
    elif output_config_key(metadata.task_type):
        namespace = (
            "custom.*"
            if metadata.task_type
            in {"core.create_global_variable", "core.create_session_variable"}
            else "automation.*"
        )
        sections.append(
            WikiSection(
                "Outputs",
                (f"Creates the configured {namespace} Variable.",),
            )
        )
    for title, values in (
        ("Requirements", metadata.requirements),
        ("Notes / Limitations", metadata.notes),
        ("Examples", metadata.examples),
    ):
        if values:
            sections.append(WikiSection(title, tuple(values)))
    return WikiEntry(
        entry_id=f"task:{metadata.task_type}",
        category="Tasks",
        title=metadata.label,
        summary=metadata.short_description,
        sections=tuple(sections),
        keywords=(metadata.task_type, metadata.category, metadata.search_text()),
    )


def _field_format(spec: Mapping[str, object]) -> str:
    kind = str(spec.get("kind", "text"))
    names = {
        "bool": "On/off option",
        "choice": "Choice",
        "counter": "Counter selection",
        "user_group": "User group selection",
        "file": "Local file",
        "folder": "Local folder",
        "json": "JSON object",
        "multiline": "Text",
        "number": "Number",
        "queue": "Queue selection",
        "routine": "Routine selection",
        "text": "Text",
    }
    details = [names.get(kind, kind.replace("_", " ").title())]
    if spec.get("required"):
        details.append("Required")
    raw_choices = spec.get("choices", ())
    choices = (
        tuple(raw_choices)
        if kind == "choice" and isinstance(raw_choices, (list, tuple))
        else ()
    )
    if choices:
        details.append("Options: " + ", ".join(str(label) for label, _ in choices))
    return ". ".join(details) + "."


def _trigger_entries(variable_registry: VariableRegistry) -> tuple[WikiEntry, ...]:
    definitions = variable_registry.definitions()

    def variables(*prefixes: str) -> tuple[str, ...]:
        return tuple(
            item.placeholder
            for item in definitions
            if any(item.name.startswith(prefix) for prefix in prefixes)
        )

    entries = [
        _trigger_entry(
            "command",
            "Command",
            "Fires when an enabled chat command is invoked.",
            variables("command.", "user."),
            ("command data", "chat command", "aliases"),
        ),
        _trigger_entry(
            KEYWORD_PHRASE_EVENT_TYPE,
            "Keyword / Phrase",
            "Fires when a Twitch chat message matches the configured text rule.",
            variables("keyword.", "user."),
            ("contains", "exact message", "before", "after"),
        ),
    ]
    for event_type, label in CORE_TRIGGER_TYPES.items():
        summary = (
            "Fires on an exact interval or a random interval range."
            if event_type == "timer"
            else f"Fires when Hub reports {label.lower()}."
        )
        entries.append(_trigger_entry(event_type, label, summary, (), ("core",)))
    for event_type in TWITCH_EVENT_AUTOMATION_TYPES:
        prefixes = ["event.", "user."]
        if "subscription" in event_type or event_type == "channel.subscribe":
            prefixes.append("subscription.")
        if "raid" in event_type:
            prefixes.append("raid.")
        if "channel_points" in event_type:
            prefixes.append("channel_points.")
        label = twitch_trigger_display_name(event_type)
        entries.append(
            _trigger_entry(
                event_type,
                f"Twitch — {label}",
                f"Fires when Hub receives the supported {label.lower()} event.",
                variables(*prefixes),
                ("Twitch", "EventSub"),
            )
        )
    for event_type, label in ADS_TRIGGER_TYPES.items():
        entries.append(
            _trigger_entry(
                event_type,
                f"Twitch — {label}",
                f"Fires when Hub reports {label.lower()}.",
                variables("ads.", "event."),
                ("Twitch", "ads"),
            )
        )
    obs_summaries = {
        "ConnectionOpened": "Fires after Hub establishes a usable OBS WebSocket connection.",
        "ConnectionClosed": "Fires when a previously usable OBS connection is lost or closed.",
        "CurrentProgramSceneChanged": "Fires when the current OBS program scene changes.",
        "CurrentPreviewSceneChanged": "Fires when the OBS Studio Mode preview scene changes.",
        "StreamStateChanged": "Fires when OBS reports a streaming output state change.",
        "RecordStateChanged": "Fires when OBS reports a recording output state change.",
        "ReplayBufferStateChanged": "Fires when OBS reports a replay-buffer state change.",
        "SceneItemEnableStateChanged": "Fires when a source item is shown or hidden in an OBS scene.",
        "InputMuteStateChanged": "Fires when an OBS input is muted or unmuted.",
        "InputVolumeChanged": "Fires when an OBS input volume changes.",
        "MediaInputPlaybackStarted": "Fires when an OBS media input starts playback.",
        "MediaInputPlaybackEnded": "Fires when an OBS media input reaches the end of playback.",
        "StudioModeStateChanged": "Fires when OBS Studio Mode is enabled or disabled.",
        "ExitStarted": "Fires when OBS begins shutting down.",
    }
    for event_type, label in OBS_TRIGGER_TYPES.items():
        entries.append(
            _trigger_entry(
                event_type,
                f"OBS — {label}",
                obs_summaries[event_type],
                variables("obs."),
                ("OBS", event_type),
            )
        )
    music_summaries = {
        "track.changed": "Fires once when the authoritative current track identity changes.",
        "playback.started": "Fires when playback transitions into playing.",
        "playback.paused": "Fires when playback transitions into paused.",
        "playback.stopped": "Fires when playback transitions into stopped.",
        "volume.changed": "Fires when volume or mute state changes.",
        "player.connected": "Fires after the local player completes authentication and protocol negotiation.",
        "player.disconnected": "Fires once when a previously usable player connection is lost.",
    }
    for event_type, label in MUSIC_TRIGGER_TYPES.items():
        entries.append(
            _trigger_entry(
                event_type,
                f"Music — {label}",
                music_summaries[event_type],
                variables("music."),
                ("Music Player", "local integration", event_type),
            )
        )
    return tuple(entries)


def _trigger_entry(
    trigger_id: str,
    title: str,
    summary: str,
    variables: tuple[str, ...],
    keywords: tuple[str, ...],
) -> WikiEntry:
    sections = [WikiSection("When it fires", (summary,))]
    configuration: tuple[str, ...] = ()
    additional_sections: tuple[WikiSection, ...] = ()
    if trigger_id == "command":
        configuration = (
            "Command name and aliases.",
            "Permissions plus global and per-user cooldowns.",
            "Enabled state and the attached routine.",
        )
    elif trigger_id == KEYWORD_PHRASE_EVENT_TYPE:
        configuration = (
            "Phrase and match mode: Contains, Exact Message, Starts With, or Ends With.",
            "Case sensitivity and whole-word matching.",
        )
    elif trigger_id == "timer":
        configuration = (
            f"{TIMER_MODES['fixed']} — enter one numeric Interval and choose its unit.",
            f"{TIMER_MODES['random']} — enter numeric Minimum and Maximum values and choose each value's unit independently.",
            "Supported units — " + ", ".join(TIMER_UNIT_LABELS.values()) + ".",
        )
        additional_sections = (
            WikiSection(
                "Validation",
                (
                    "Values must be positive finite numbers that resolve exactly to whole milliseconds.",
                    f"The minimum safe interval is {MINIMUM_TIMER_MILLISECONDS} milliseconds.",
                    "For Random mode, Minimum must not exceed Maximum.",
                ),
            ),
            WikiSection(
                "Scheduling",
                (
                    "Random mode chooses a new delay after every firing.",
                    "Disabling a Timer cancels its scheduled firing; enabling it starts a fresh delay.",
                    "Editing an enabled Timer replaces the old schedule with a fresh delay from the new configuration.",
                    "Timer routines use their normal queue, and Hub startup begins a fresh delay without catching up missed runs.",
                ),
            ),
            WikiSection(
                "Timers page",
                (
                    "The Timers page lists every Timer trigger and its live next-run status, recurring state, and linked routine.",
                    "Create a Timer there for an existing or new routine, or continue adding Timer triggers from the Routines page.",
                    "Both pages edit the same Timer trigger; enable, disable, edit, and delete changes apply immediately to its normal routine queue.",
                    "Open Routine selects the linked routine by stable ID, expands its group if needed, and scrolls it into view.",
                ),
            ),
            WikiSection(
                "Examples",
                (
                    f"{TIMER_MODES['fixed']}: 10 Minutes — runs every 10 minutes.",
                    f"{TIMER_MODES['random']}: 5 Minutes to 10 Minutes — chooses a new 5–10 minute delay after each firing.",
                ),
            ),
        )
    elif trigger_id == "channel.chat.first_message":
        configuration = (
            "Tracks each viewer's first message for the current authoritative Twitch stream.",
            "Hub resets the seen-viewer state automatically when Twitch reports a new stream ID.",
            "Your Channel → Users can enable or disable the existing First Message triggers and reset the current stream for testing.",
        )
        additional_sections = (
            WikiSection(
                "Manual reset",
                (
                    "Reset First Words for Current Stream lets viewers trigger again during the same stream.",
                    "The reset does not delete users, groups, roles, counters, or other chatter data.",
                    "Incoming-raid suppression is separate and remains active after a manual reset.",
                ),
            ),
        )
    elif trigger_id == "channel.raid.outgoing":
        configuration = (
            "No fields are required; attach the trigger to a routine and enable it.",
            "It fires only from Twitch's authoritative outgoing channel.raid EventSub confirmation.",
        )
        additional_sections = (
            WikiSection(
                "Completion semantics",
                (
                    "Starting a raid, the local countdown reaching zero, and cancelling a raid do not fire this trigger.",
                    "Raid Landing is a separate consumer of the same Twitch confirmation event.",
                ),
            ),
        )
    elif "channel_points" in trigger_id:
        configuration = ("Any custom reward or one selected reward.",)
    elif trigger_id in OBS_TRIGGER_TYPES:
        configuration = (
            "Optional OBS event-field filters such as scene, source, or input name.",
        )
    elif trigger_id in MUSIC_TRIGGER_TYPES:
        configuration = (
            "No event fields are required; attach the trigger to a routine and enable it.",
            "The first state snapshot after connecting establishes a baseline and does not create a track, playback, or volume event.",
            "Periodic playback-position synchronization does not fire Automation triggers.",
        )
        if trigger_id == "track.changed":
            additional_sections = (
                WikiSection(
                    "Examples",
                    (
                        "Track Changed → OBS Set Text → {music.artist} - {music.title}",
                        "Track Changed → update a Now Playing overlay.",
                    ),
                ),
            )
        elif trigger_id == "player.disconnected":
            additional_sections = (
                WikiSection("Example", ("Player Disconnected → run an optional notification or log routine.",)),
            )
    elif trigger_id in TWITCH_EVENT_AUTOMATION_TYPES or trigger_id in ADS_TRIGGER_TYPES:
        configuration = ("Optional event filters supported by this trigger editor.",)
    if configuration:
        sections.append(WikiSection("Configuration", configuration))
    sections.extend(additional_sections)
    if variables:
        sections.append(
            WikiSection(
                "Available Variables",
                tuple(f"{name} — available for this triggered routine." for name in variables),
            )
        )
    return WikiEntry(
        f"trigger:{trigger_id}",
        "Triggers",
        title,
        summary,
        tuple(sections),
        keywords,
    )


def _variable_entries(variable_registry: VariableRegistry) -> tuple[WikiEntry, ...]:
    definitions = tuple(
        WikiEntry(
            entry_id=f"variable:{definition.name}",
            category="Variables",
            title=definition.name,
            summary=definition.description,
            sections=(
                WikiSection(
                    "Reference",
                    (
                        f"Placeholder — {definition.placeholder}",
                        f"Type — {definition.data_type.value}",
                        f"Lifetime — {definition.lifetime_label}",
                        f"Availability — {definition.availability.value}",
                        f"Access — {'Writable' if definition.writable else 'Read-only'}",
                        f"Namespace — {definition.name.split('.', 1)[0]}.*",
                        f"Category — {definition.category}",
                        f"Source — {definition.source}",
                    ),
                ),
                WikiSection(
                    "Available when",
                    (
                        definition.context_label
                        or (
                            "Matching routine context is active."
                            if definition.required_context
                            else "Hub is running and its owning service has a value."
                        ),
                    ),
                ),
            ),
            keywords=(
                definition.display_name,
                definition.category,
                definition.source,
                definition.availability.value,
                *definition.required_context,
            ),
            copy_text=definition.placeholder,
        )
        for definition in variable_registry.definitions()
    )
    namespaces = (
        WikiEntry(
            "variables:automation-outputs",
            "Variables",
            "automation.* task outputs",
            "Routine-scoped values created by output-capable Tasks.",
            (
                WikiSection(
                    "Lifetime",
                    (
                        "An automation.* output becomes available after its producing Task in the current root routine execution.",
                        "Nested routines may inherit the current root context; the output is cleared when that root execution ends.",
                        "Each Task entry lists the concrete outputs it can create.",
                    ),
                ),
            ),
            ("temporary", "routine output", "output variable"),
        ),
        WikiEntry(
            "variables:custom-values",
            "Variables",
            "custom.* persistent values",
            "User-defined persistent Variables use canonical custom.* names.",
            (
                WikiSection(
                    "Ownership",
                    (
                        "Create and change custom.* values through the current Variable tasks and Variables tooling.",
                        "Wiki documents the namespace but does not edit values.",
                    ),
                ),
            ),
            ("custom variable", "persistent variable"),
        ),
    )
    return (*definitions, *namespaces)


def _control_flow_entries(task_registry: TaskRegistry) -> tuple[WikiEntry, ...]:
    names = tuple(
        metadata.label
        for metadata in task_registry.visible_metadata()
        if metadata.task_type
        in {
            "core.wait",
            "core.if",
            "core.run_routine",
            "core.end_routine",
            "core.stop_current_routine",
            "core.stop_queue",
        }
    )
    return (
        WikiEntry(
            "control:queues",
            "Queues & Control Flow",
            "Queues and routine execution",
            "Queues serialize routine work; routines without a custom assignment use Default Queue.",
            (
                WikiSection(
                    "Queue model",
                    (
                        "Default Queue is the fallback for routines without a selected custom queue.",
                        "Custom queues let independent classes of work run without blocking one another.",
                        "Nested routines participate in the current execution and retain normal cancellation behavior.",
                    ),
                ),
                WikiSection("Control-flow tasks", names),
            ),
            ("default queue", "nested routine", "wait", "if else", "cancel"),
        ),
    )


def _counter_entries(variable_registry: VariableRegistry) -> tuple[WikiEntry, ...]:
    descriptions = {
        "channel_total": "Lifetime total for the channel.",
        "stream_total": "Total for the current confirmed Twitch stream.",
        "viewer_total": "Lifetime total for one viewer, owned by stable Twitch user ID.",
        "viewer_stream_total": "That viewer's total during the current confirmed Twitch stream.",
    }
    scope_lines = tuple(f"{scope} — {descriptions[scope]}" for scope in SCOPES)
    counter_variables = tuple(
        definition.placeholder
        for definition in variable_registry.definitions()
        if definition.name.startswith("counter.")
    )
    return (
        WikiEntry(
            "counter:scopes",
            "Counters",
            "Counter scopes",
            "Counters can track lifetime and current-stream totals for the channel or an individual viewer.",
            (
                WikiSection("Scopes", scope_lines),
                WikiSection(
                    "Values",
                    (
                        "Integer Counters accept whole numbers; decimal Counters retain exact decimal values.",
                        "Minimums, units, and display precision come from the Counter definition.",
                        "Current-stream scopes require a confirmed Twitch stream ID.",
                    ),
                ),
                WikiSection("Canonical Variables", counter_variables),
            ),
            ("integer", "decimal", "minimum", "units", *SCOPES),
        ),
    )


def _command_entries() -> tuple[WikiEntry, ...]:
    built_ins = default_command_definitions()
    command_lines = tuple(
        f"!{item.name} — "
        + (
            f"Setup-dependent ({item.setup_requirement})."
            if item.setup_requirement
            else "Ready out of the box."
        )
        for item in built_ins
    )
    return (
        WikiEntry(
            "commands:overview",
            "Commands",
            "Chat Commands",
            "Command triggers start routines from Twitch chat without changing routine organization or queue ownership.",
            (
                WikiSection(
                    "Command context",
                    (
                        "{command.name} is the normalized command name without the exclamation mark.",
                        "{command.data} is all remaining command text with outer whitespace removed.",
                        "{command.target} is the first argument with an optional @ removed.",
                    ),
                ),
                WikiSection(
                    "Organization",
                    (
                        "New command routines default to the Commands group, but the user may move them anywhere.",
                        "Commands use normal routine queues; no custom assignment means Default Queue.",
                        "Open Routine selects the command's exact linked routine by stable ID, expands its current group if needed, and scrolls it into view.",
                    ),
                ),
                WikiSection("Built-in Commands", command_lines),
            ),
            tuple(item.name for item in built_ins) + ("command.data", "aliases"),
        ),
    )


def _getting_started_entries() -> tuple[WikiEntry, ...]:
    return (
        WikiEntry(
            "start:first-routine",
            "Getting Started",
            "Build your first routine",
            "Connect a service, create a routine, choose what starts it, then add the work it should perform.",
            (
                WikiSection(
                    "Steps",
                    (
                        "1. Connect Twitch and, if needed, OBS from Connections.",
                        "2. Open Automation and create a Routine.",
                        "3. Add a Trigger such as Command, Timer, Twitch event, or OBS event.",
                        "4. Add Tasks in the order they should run.",
                        "5. Use canonical Variable placeholders to carry trigger data into task inputs.",
                        "6. Keep Default Queue or choose a custom queue, then test the routine.",
                    ),
                ),
            ),
            ("connect Twitch", "connect OBS", "routine", "test"),
        ),
        WikiEntry(
            "start:wiki-vs-pages",
            "Getting Started",
            "Wiki, Variables, and Automation",
            "The Wiki explains Hub; operational pages remain the place to inspect or change live configuration.",
            (
                WikiSection(
                    "Where to work",
                    (
                        "Automation authors routines, triggers, tasks, and queues.",
                        "Variables inspects current values and availability.",
                        "Counters configures Counter definitions and values.",
                        "Wiki is read-only reference documentation.",
                    ),
                ),
            ),
            ("manual", "reference", "inspector"),
        ),
    )


def _twitch_entries() -> tuple[WikiEntry, ...]:
    return (
        WikiEntry(
            "twitch:capabilities",
            "Twitch",
            "Twitch in Hub",
            "Hub connects Twitch chat and supported EventSub activity to Commands, moderation, Variables, and Automation.",
            (
                WikiSection(
                    "Current capabilities",
                    (
                        "Broadcaster and optional bot authentication.",
                        "Live chat, Command and Keyword / Phrase triggers, and current moderation actions.",
                        "Supported follows, subscriptions, cheers, raids, stream state, First Message, ads, and Channel Points events.",
                        "Twitch chat content is transient and is not stored as durable chat history.",
                    ),
                ),
            ),
            ("EventSub", "chat", "moderation", "raids", "Channel Points", "ads"),
        ),
        WikiEntry(
            "twitch:slash-commands",
            "Twitch",
            "Twitch chat slash commands",
            "Hub dispatches supported slash commands through Twitch APIs; unsupported slash text is never sent as ordinary chat.",
            (
                WikiSection(
                    "Supported commands",
                    tuple(
                        f"{command.syntax} — {command.description}. Permission: {command.required_scope}."
                        for command in TWITCH_SLASH_COMMANDS
                    ),
                ),
                WikiSection(
                    "User roles",
                    (
                        "Moderator and VIP actions use the same Twitch action path as the Chatters/Users context menu.",
                    ),
                ),
                WikiSection(
                    "Durations",
                    (
                        "Timeout and Slow accept seconds, minutes, or hours such as 30s, 10m, or 2h; bare numbers are seconds.",
                        "Followers-only accepts a whole-minute duration; bare numbers are minutes.",
                    ),
                ),
            ),
            tuple(
                keyword
                for command in TWITCH_SLASH_COMMANDS
                for keyword in (command.name, command.syntax, command.required_scope)
            ),
        ),
        WikiEntry(
            "twitch:user-groups",
            "Twitch",
            "User Groups",
            "Organize Twitch users in one shared system for Hub behavior and Automation.",
            (
                WikiSection(
                    "Groups and membership",
                    (
                        "Bots and Regulars are protected system groups. Bots drives Hub's bot filtering; Regulars is maintained automatically from observed participation.",
                        "Automatic was a classification mode, not a group, and Viewers is the natural fallback for users without a more specific display group.",
                        "A Twitch user may belong to several groups. Create, rename, delete, assign, and remove custom-group memberships from Your Channel → Users.",
                        "Membership uses stable Twitch user IDs; renaming a group keeps its stable internal identity and existing Automation references.",
                    ),
                ),
                WikiSection(
                    "Automation",
                    (
                        "In an If task, choose User Is In Group and select a system or custom group. A missing triggering user evaluates false; a deleted group is shown as missing and fails safely.",
                        "Example: First Message → User Is In Group: Auto Shoutout → Twitch — Shoutout User {user.id}.",
                    ),
                ),
            ),
            ("groups", "membership", "Auto Shoutout", "user.id", "First Message"),
        ),
        WikiEntry(
            "twitch:raid-page",
            "Twitch",
            "Your Channel → Raid",
            "Prepare a raid message, find followed channels that are currently live, and manage Twitch's pending raid countdown in Hub.",
            (
                WikiSection(
                    "Raid message",
                    (
                        "The session-only Raid Message can be edited, copied, or sent through Hub's normal Twitch chat connection before starting a raid.",
                        "Sending is always explicit; starting a raid never sends the message automatically. Raid Messages are plain text and do not currently resolve Variables.",
                    ),
                ),
                WikiSection(
                    "Live channel cards",
                    (
                        "Each card shows the channel, category, stream title, viewer count, uptime, and Twitch thumbnail when available.",
                        "Search matches channel name, login, category, and stream title. Results can be sorted by viewers or channel name.",
                        "Opening the Raid page automatically refreshes the followed channels that are currently live; Refresh can request another update.",
                    ),
                ),
                WikiSection(
                    "Start and countdown",
                    (
                        "Refresh asks Twitch for the followed channels that are live now; Hub does not save the results.",
                        f"Raid asks for confirmation, then starts Twitch's {RAID_COUNTDOWN_SECONDS}-second pending raid countdown. The active target is marked and other Raid actions remain unavailable until it completes or is cancelled.",
                        "Twitch automatically executes the raid when the countdown ends.",
                        "Cancel Raid uses Twitch's real cancellation API and returns the page to normal target selection only after Twitch accepts the cancellation.",
                        "An outgoing Channel Raid event clears the active state immediately when Twitch confirms completion.",
                    ),
                ),
                WikiSection(
                    "Requirements",
                    (
                        "Connect the Main / Broadcaster Account and grant followed-channel read and raid-management permissions.",
                    ),
                ),
                WikiSection(
                    "Raid Landing",
                    (
                        "Open Raid Landing after raid is an optional session-only setting and is off by default.",
                        "After Twitch confirms the outgoing raid, Raid Landing opens a separate local companion window for the target channel. Starting or cancelling a raid does not open it.",
                        "The window keeps a temporary, read-only target chat separate from your main Hub chat. Closing it releases that temporary chat session and does not affect the raid or your main Twitch connection.",
                        "Use Open on Twitch for video and viewing. V1 intentionally has no embedded video or Streamhouse-hosted cloud dependency.",
                        "Retry Chat reconnects a failed target-chat session. Copy Channel Link copies the target's Twitch URL.",
                        "Always on Top keeps the companion visible; Close dismisses it. A later confirmed raid replaces an existing landing window.",
                    ),
                ),
            ),
            (
                "raid",
                "followed live channels",
                "user:read:follows",
                "channel:manage:raids",
                "viewer count",
                "uptime",
                "raid message",
                "countdown",
                "cancel raid",
                "raid landing",
                "open on twitch",
                "always on top",
                "target chat",
            ),
        ),
    )


def _obs_entries(task_registry: TaskRegistry) -> tuple[WikiEntry, ...]:
    tasks = tuple(
        metadata.label
        for metadata in task_registry.visible_metadata()
        if metadata.task_type.startswith("obs.")
    )
    return (
        WikiEntry(
            "obs:capabilities",
            "OBS",
            "OBS in Hub",
            "Hub uses the configured OBS WebSocket connection for current OBS tasks and event triggers.",
            (
                WikiSection(
                    "Connection",
                    (
                        "Configure host, port, and protected password in Connections.",
                        "Tasks that require OBS report an unavailable state when OBS is disconnected.",
                    ),
                ),
                WikiSection("Current task reference", tasks),
                WikiSection(
                    "Triggers",
                    tuple(OBS_TRIGGER_TYPES.values()),
                ),
            ),
            ("scene", "source", "input", "WebSocket", *OBS_TRIGGER_TYPES.values()),
        ),
    )


def _integration_entries() -> tuple[WikiEntry, ...]:
    return (
        WikiEntry(
            "integration:touch-portal",
            "Integrations",
            "Touch Portal (Experimental)",
            "Run an enabled Streamhouse Hub Routine from Touch Portal on the same PC.",
            (
                WikiSection(
                    "Current capability",
                    (
                        "The Streamhouse Hub Touch Portal plugin provides one action: Run Streamhouse Routine.",
                        "Hub must be running. The plugin discovers enabled routines and keeps each routine's stable ID with the displayed selection.",
                    ),
                ),
                WikiSection(
                    "Execution",
                    (
                        "Touch Portal sends the selected stable routine ID to Hub; names and groups are presentation only.",
                        "Hub submits the request through AutomationService and the routine's normal queue, including Default Queue fallback.",
                        "Renaming or moving a routine keeps its stable ID. A deleted or disabled routine is rejected instead of being rebound by name.",
                    ),
                ),
                WikiSection(
                    "Local and experimental",
                    (
                        "Version 1 listens only on this computer's loopback interface and does not provide LAN or remote access.",
                        "This is an Experimental / Local Integration for Alpha testing; Hub remains the owner of routine data and execution.",
                    ),
                ),
                WikiSection(
                    "Variable context",
                    (
                        "A Touch Portal launch does not fabricate user.*, command.*, keyword.*, or Twitch event context.",
                        "Contextual Variables remain unavailable unless the routine's current execution genuinely provides them.",
                    ),
                ),
            ),
            (
                "Touch Portal",
                "Run Routine",
                "local integration",
                "stable routine ID",
                "localhost",
                "experimental",
            ),
        ),
        WikiEntry(
            "integration:music-player",
            "Integrations",
            "Music Player (Local)",
            "Connect Streamhouse Hub to the independent standalone Music Player on the same PC.",
            (
                WikiSection(
                    "Connection",
                    (
                        "Hub automatically discovers the standalone player while it is running. No manual port or token setup is normally required, and Hub works normally while the player is unavailable.",
                        "Hub uses versioned WebSocket protocol 1 on 127.0.0.1 only. No Streamhouse cloud service or YouTube backend API is involved.",
                    ),
                ),
                WikiSection(
                    "Variables",
                    tuple(
                        f"{definition.placeholder} — {definition.description}"
                        for definition in MUSIC_VARIABLE_DEFINITIONS
                    )
                    + (
                        "Definitions remain discoverable while disconnected; their live values are unavailable until the player supplies state.",
                    ),
                ),
                WikiSection(
                    "Automation tasks",
                    (
                        "Music tasks support Play, Pause, Play/Pause, Next Track, Previous Track, Set Volume, and Set Muted when advertised by the connected player.",
                        "Each task waits for its command result and does not pretend the playback state changed; later state snapshots remain authoritative.",
                    ),
                ),
                WikiSection(
                    "Examples",
                    (
                        "A trigger or Command routine can run Music — Next Track.",
                        "An OBS text task can use: Now Playing: {music.artist} - {music.title}",
                    ),
                ),
            ),
            (
                "music player",
                "localhost websocket",
                "now playing",
                *(definition.name for definition in MUSIC_VARIABLE_DEFINITIONS),
            ),
        ),
    )


def _example_entries() -> tuple[WikiEntry, ...]:
    recipes = (
        (
            "command-data",
            "Respond with command.data",
            "Create a Command trigger, then add Send Chat Message with: You said {command.data}",
            ("command.data", "Send Chat Message"),
        ),
        (
            "viewer-counter",
            "Increment a viewer Counter",
            "Create a Counter with viewer_total enabled, then use Increase Counter from a viewer-triggered routine.",
            ("viewer_total", "Counter"),
        ),
        (
            "raid",
            "React to an incoming raid",
            "Add the Twitch Incoming Raid trigger and use {raid.source.name} and {raid.viewers} in later tasks.",
            ("raid", "Twitch"),
        ),
        (
            "obs-scene",
            "Change an OBS scene",
            "Connect OBS, add Set Program Scene to a routine, and choose the destination scene.",
            ("OBS scene", "Set Program Scene"),
        ),
        (
            "if-else",
            "Branch with If / Else",
            "Place If after the task that produces the Variable, define the condition, and add tasks to each branch.",
            ("If", "Else", "automation output"),
        ),
        (
            "nested-routine",
            "Run a nested Routine",
            "Use Run Routine to reuse another routine while retaining the current root execution context.",
            ("nested routine", "Run Routine"),
        ),
        (
            "python-now-playing",
            "Publish Python results to later tasks",
            "In Run Python Script, call hub.set_output(\"artist\", artist) and hub.set_output(\"song\", song). A later OBS text task can use {automation.artist} - {automation.song}; no intermediate file or persistent Variable is needed.",
            (
                "Python Script Context",
                "hub.set_output",
                "hub.get_variable",
                "hub.log",
                "Now Playing",
                "automation.artist",
                "automation.song",
            ),
        ),
    )
    return tuple(
        WikiEntry(
            f"example:{key}",
            "Examples",
            title,
            detail,
            (WikiSection("Recipe", (detail,)),),
            keywords,
        )
        for key, title, detail, keywords in recipes
    )
