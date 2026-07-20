# Sally platform architecture

Sally is a Python and PySide6 standalone AI platform. It is not a
Streamer.bot replacement. The product goal is a modular desktop AI platform
that other streamers can use without architectural rewrites.

## Build priorities

1. Logging
2. Final UI shell
3. Removable developer tools
4. Core event system
5. Configuration
6. Twitch
7. AI
8. Memory
9. Voice
10. Automation
11. Vision
12. Plugins

Plugins remain last so stable service, trigger, routine, and task contracts
exist before third-party integrations depend on them.

## UX rules

- Left navigation contains major systems.
- Top tabs contain subpages of the selected system.
- Icons are monochrome.
- New work extends the final UI shell instead of creating temporary developer
  pages.
- Developer tools remain removable from the normal streaming workspace.
- Basic workflows are visible by default; advanced options use progressive
  disclosure.
- New features fit existing navigation rather than forcing redesigns.

## Canonical terminology

- **Service**: a feature provider such as Twitch, Voice, OBS, Timer, AI, or
  Vision.
- **Trigger**: an event originating from a service, such as a Twitch command,
  follow, keyword, raid, or channel-point redemption.
- **Routine**: an ordered workflow selected by a trigger.
- **Task**: one executable step inside a routine, such as sending a Twitch chat
  message, changing an OBS scene, or asking AI for a response.

Use these names in domain classes, persisted data, event names, documentation,
and UI labels where the advanced concepts are exposed. A basic UI may combine
a trigger and its managed routine into one form, but the stored architecture
must keep their responsibilities separate.

## Routine editing and organization

Routines support persistent custom groups with stable IDs and a built-in
Ungrouped section. Group order, names, and collapsed state are editor metadata;
deleting a group moves its routines to Ungrouped and never deletes workflows.

The routine store owns general routine and task CRUD, including duplication and
ordered task movement. Service-managed routines mark their primary task with a
stable managed key. Generic editor actions may add, edit, duplicate, and reorder
other tasks, but cannot delete the managed routine, remove its primary task, or
silently change its service trigger. Service editors update only their managed
task and preserve every additional task in the workflow.

Older routine files migrate automatically to the grouped, multi-trigger
version-three format without changing routine or task IDs.

The Automation page uses a grouped routine browser on the left and the selected
routine editor on the right. Triggers, ordered tasks, settings, and session run
history remain together. Twitch Commands provides a command-focused shortcut
and an Open Routine action, but both pages edit the same trigger and routine
records.

Routines may link multiple trigger IDs. A Twitch command, Twitch EventSub
events, and Sally Core program events can coexist on the same ordered task
workflow. Each service owns its trigger-specific matching and persisted
configuration; Automation receives only normalized `TriggerEvent` objects.

The Core service currently publishes `application.started` after the main
window enters the Qt event loop and `application.closing` before services are
torn down. Their bindings persist in `automation/core_triggers.json` and are
included in backup, restore, and legacy-data migration.

The OBS service speaks the built-in OBS WebSocket 5.x protocol. Connection
settings live in `obs/connection.json`; the password is encrypted separately
with Windows DPAPI and is deliberately excluded from backups. Sally reconnects
automatically after a manual connection, while connecting at application
startup is an opt-in setting that defaults off. Sally translates OBS events into normalized triggers,
and stores routine bindings in `obs/triggers.json`. OBS tasks cover scenes,
source visibility, audio, streaming, recording, replay buffer, media sources,
hotkeys, Studio Mode, and an advanced raw request escape hatch.

Core task providers can launch and close applications, wait without freezing
the Qt event loop, wait for Twitch or OBS to connect, and open a file, folder,
or web address. This supports startup and shutdown workflows without embedding
platform-specific scripts inside routine definitions. Trusted local Python
scripts are also available under Core > Scripts. Sally runs them in a separate
process, supplies normalized trigger context through `SALLY_*` environment
variables, supports templated arguments, applies an optional timeout, and can
capture bounded output in run history. Packaged builds use a configured or
system Python interpreter rather than treating the Sally executable as Python.

Twitch task providers cover normal and pinned chat messages, commercials, ad
snoozing, user moderation, and fulfilling or refunding reward redemptions.
Custom reward creation and configuration remain in Your Channel > Channel
Points rather than automation tasks. EventSub trigger context preserves
viewer, message, reward, and redemption IDs so a routine can safely act on the
event that invoked it. The editor exposes these as structured fields and keeps
the Twitch tasks grouped under the Twitch service.

## Trigger and automation pipeline

Service integrations publish a normalized `TriggerEvent` through
`AutomationService`. The service emits both a general and typed event:

- `trigger_fired`
- `trigger_fired.<service>.<trigger_type>`

Matching `RoutineDefinition` objects run their enabled `TaskDefinition` steps
in order through `TaskRegistry`. Runtime lifecycle events are:

- `routine_started`
- `routine_completed`
- `routine_failed`
- `task_started`
- `task_completed`
- `task_failed`

Service-specific trigger state—matching, permissions, cooldowns, and usage
statistics—does not execute tasks directly.

Automation editing supports versioned task copy/paste and portable
`.sally-routine.json` bundles. Imports regenerate internal IDs and validate
task-provider availability, trigger types, and Twitch command conflicts before
creating a routine. Selected tasks can be tested independently with editable
sample trigger context; their results and duration enter the same run history
as full routines. Task editors expose only the variables supplied by the
routine's triggers, with descriptions, insertion controls, and rendered test
previews.

## AI

Sally is model-agnostic. Ollama with Qwen3:14B is the Stage 1 local brain.
Qwen decides whether a message warrants a response or should be escalated to a
future cloud provider. Sally Core owns routing, privacy, cooldown, and safety
rules so model providers remain replaceable.

## Future avatar and Twitch extension

The future avatar is a browser-source overlay hosted by Sally for OBS. Initial
mouth animation uses PNG frames driven by TTS audio amplitude, with phoneme
lip-sync as a later capability. OBS setup should eventually be one click.

The future Twitch Extension is a viewer interaction panel rather than a simple
soundboard. Extension interactions enter Sally through a backend service and
publish triggers into the same routine pipeline used by desktop integrations.
