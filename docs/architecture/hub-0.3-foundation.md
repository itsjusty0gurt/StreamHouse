# Streamhouse Hub 0.3 modular foundation contracts

Status: **Core foundation implemented; OBS conversion ready for review on its
isolated feature branch**.

Streamhouse Hub `0.2.0-alpha` is released and frozen at tag
`v0.2.0-alpha`. Active development targets `0.3.0-alpha`. This document defines
the minimum modular contracts needed to reduce `MainWindow` coupling without
changing the current tab-first product experience or creating a third-party
plugin ecosystem.

The words **Core** and **module** describe ownership boundaries inside the
bundled Hub application. They do not imply downloaded code, a marketplace, or
hot loading.

The primary integration rule is:

> Streamhouse Hub is the integration layer between modules. Modules expose
> their capabilities and state to Hub Core, and other modules interact with
> them through Core contracts instead of direct module-to-module dependencies.

For example, the OBS module exposes OBS Variables, actions, and triggers to
Core. A future Deck module reads Core Variables and runs Core routines; it does
not import OBS implementation or establish a second OBS connection. Twitch,
OBS, PRISM, Broadcaster, Deck, and later modules remain peers around Core.

## Current implementation baseline

The contracts below are grounded in the current implementation:

- `streamhouse_hub.app` creates Qt, Twitch authentication/service, the trusted
  bundled `ModuleRegistry`, and `MainWindow`.
- `MainWindow` remains the Core composition root, but OBS construction,
  configuration, triggers, Variables, connection UI, event subscriptions, and
  shutdown now belong to `streamhouse.obs` and enter Core through registries.
- `TaskRegistry`, `VariableRegistry`, `AutomationService`, the routine/queue
  stores, `TriggerEvent`, `Events`, `BackupManager`, and `WindowStateStore`
  already provide useful Core seams.
- Trigger persistence is correctly domain-owned: Core, Twitch, OBS, Music, and
  Commands have focused stores rather than one universal trigger store.
- Navigation uses the existing main stack/button model. `QMainWindow` already
  persists window state and hosts one developer-tools `QDockWidget`, but there
  is no general dock-contribution contract.
- Backup orchestration remains Core-owned and accepts the OBS module's safe
  configuration contributor while preserving the published component and
  archive contract.

These are migration inputs, not reasons to keep domain construction in
`MainWindow` permanently.

## Decisions and deferred work

### Decided for the 0.3 foundation

- Hub Core is mandatory. Bundled optional modules depend on Core contracts, not
  on another optional module's implementation.
- Core owns all registries, the Automation/routine/queue runtime, the event bus,
  Backup orchestration, settings/secrets primitives, shell navigation, docking,
  and lifecycle ordering.
- Modules own their domain services, domain stores, configuration, tasks,
  triggers, Variables, UI contributions, and subscriptions.
- All identities used in persistence or references are stable IDs. Display
  names, labels, ordering, and icons are presentation data.
- Registration is deterministic and explicit. Duplicate stable IDs are errors;
  load order never chooses a winner.
- Missing or disabled providers leave persisted references intact and visibly
  unavailable. Hub does not silently substitute another provider or rebind by
  name.
- `VariableRegistry` remains the only Variables architecture. `TaskRegistry`
  remains the Automation task execution/catalog registry.
- `Events.emit()` remains synchronous on the emitting thread. Module boundaries
  do not weaken existing Qt/threading rules.
- The current main-tab navigation remains primary. Optional docks are for
  live-use panels, such as platform chat and Broadcaster panels.
- OBS is the first extraction/proof of concept, but existing public paths,
  schemas, stable task/trigger IDs, and data meanings remain unchanged unless a
  separately reviewed migration is required.
- Enabling or disabling a module requires restart in the initial foundation.
  Runtime hot unload is not supported.
- Module installation/discovery has one known bundled location,
  `products/hub/modules/bundled/`. Core scans that location but instantiates
  only factories in its trusted first-party allowlist. Directory contents are
  not dynamically imported.

### Deferred beyond this contract

- Third-party/downloaded plugins, discovery from arbitrary directories,
  marketplace/install/update flows, code signing/trust UI, sandboxing,
  dependency solving, version conflict resolution, and hot reload/unload.
- Generic provider routing such as choosing among OBS, PRISM, or another
  broadcaster provider at runtime.
- A user-facing module-management redesign beyond the minimum bundled-module
  enabled state.
- Conversion of Twitch or Music. OBS is the first provider converted to the
  contracts; later conversions should use its tested lifecycle and unavailable
  provider behavior rather than adding direct module coupling.
- Cross-module extension APIs beyond Core registries. A future need must be
  solved through a new Core-owned capability contract, not direct imports.
- Migration of Twitch or Music after OBS. Their eventual extraction should use
  evidence from the OBS proof of concept.

## Contract sketches

The following Python is documentation-only pseudocode. It intentionally omits
framework implementation details.

### Module descriptor and lifecycle

```python
@dataclass(frozen=True, slots=True)
class ModuleDescriptor:
    module_id: str                 # stable, namespaced: "streamhouse.obs"
    display_name: str
    version: str
    category: str
    bundled: bool
    enabled_by_default: bool
    minimum_core_version: str
    maximum_core_version: str | None
    required_core_capabilities: frozenset[str]
    capabilities: frozenset[str]
    ui_label: str = ""
    ui_icon: str = ""
    optional_capabilities: frozenset[str] = frozenset()


class HubModule(Protocol):
    descriptor: ModuleDescriptor

    def register(self, context: ModuleContext) -> None: ...
    def configure(self, context: ModuleContext) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
```

`required_core_capabilities` describes hard requirements supplied by mandatory
Core. `optional_capabilities` is descriptive capability information only in the
0.3 foundation. Neither is a package dependency solver or license for one
optional module to import another. A module that can use an optional capability
queries a Core registry and handles absence explicitly. The descriptor records
the author-supplied default; the mutable **currently enabled** state belongs to
`ModuleRegistry` and its `ModuleStatus`, not to immutable module metadata.

Lifecycle states are `registered`, `configured`, `started`, and `stopped`:

1. **Register** is deterministic and side-effect-light. A module contributes
   descriptors, handlers, providers, editors, tabs, docks, actions, and Backup
   contracts. It does not connect sockets or create visible widgets.
2. **Configure** loads the module's current-schema settings/stores and validates
   compatibility. It does not start external connections.
3. **Start** begins services and subscriptions after all enabled modules have
   registered and configured.
4. **Stop** rejects new work, cancels or completes owned work according to its
   service contract, unsubscribes, closes resources, and is called in reverse
   start order.

Registration or configuration failure prevents that module from starting and
marks its contributions unavailable. A start failure stops the partially
started module, records an actionable failure, and leaves other independent
modules usable. Core failure remains fatal. A module may not publish a partial
set of registrations and then continue; registration is committed atomically or
rolled back. Enable/disable changes are persisted by Core and take effect after
restart.

### Module context

```python
@dataclass(frozen=True, slots=True)
class ModuleContext:
    core_version: str
    events: EventBusContract
    variables: VariableRegistry
    tasks: TaskRegistry
    actions: ActionRegistry
    triggers: TriggerRegistration
    automation: AutomationGateway
    settings: ModuleSettings
    secrets: ModuleSecrets
    backup: BackupRegistration
    tabs: TabRegistration
    docks: DockRegistration
    local_api: LocalApiRegistration
    logger: ModuleLogger
```

`ModuleContext` contains Core-owned, deliberately narrow contracts. It never
exposes `MainWindow`, raw global settings, another module instance, or a generic
service locator. The Automation gateway may publish a validated `TriggerEvent`,
run a routine through normal queue semantics, and query availability; it may
not bypass queues or mutate routine stores. Settings and secrets are namespaced
to `descriptor.module_id`. Secrets never enter ordinary settings, logs, Backup,
or diagnostics.

The implemented first slice exposes the existing event, Variable, Task, and new
Action contracts plus registration commit/rollback callbacks. The remaining
fields above are the target surface and are added only alongside a concrete
Core registry adapter. The currently exposed fields receive concrete existing
registries at composition time; no generic service map pretends an
unimplemented contract exists.

### Module registry

```python
class ModuleRegistry(Protocol):
    def register(self, module: HubModule) -> None: ...
    def descriptor(self, module_id: str) -> ModuleDescriptor | None: ...
    def descriptors(self) -> tuple[ModuleDescriptor, ...]: ...
    def is_enabled(self, module_id: str) -> bool: ...
    def set_enabled(self, module_id: str, enabled: bool) -> RestartRequired: ...
    def configure_enabled(self, context_factory: ContextFactory) -> None: ...
    def start_enabled(self) -> None: ...
    def stop_started(self) -> None: ...
```

The registry owns module identity, descriptor validation, Core-version
compatibility, deterministic order, enabled state, lifecycle state, contribution
transactions, failure status, and reverse shutdown order. It is not a module
downloader, marketplace, dependency solver, dynamic importer, hot reloader, or
service locator.

Duplicate module IDs are rejected before configuration. Duplicate contribution
IDs are rejected by the owning Core registry and fail the contributing module's
registration. A missing bundled module or incompatible Core range is surfaced
as unavailable; Core does not guess. A disabled module is never configured or
started, but its persisted data and references remain untouched.

### Actions

```python
@dataclass(frozen=True, slots=True)
class ActionParameter:
    parameter_id: str
    label: str
    value_kind: str              # UI-facing simple kind, not Python type UI
    required: bool = True
    variable_aware: bool = False


@dataclass(frozen=True, slots=True)
class ActionDescriptor:
    action_id: str               # e.g. "obs.scene.switch"
    display_name: str
    owner_module_id: str
    category: str
    capability: str
    parameters: tuple[ActionParameter, ...]
    handler: ActionHandler
    availability: AvailabilityProbe
    permissions: frozenset[str] = frozenset()
    exposure: frozenset[str] = frozenset({"automation"})


class ActionRegistry(Protocol):
    def register(self, descriptor: ActionDescriptor) -> None: ...
    def get(self, action_id: str) -> ActionDescriptor | None: ...
    def invoke(self, action_id: str, values: Mapping[str, object],
               execution: ActionExecutionContext) -> ActionResult: ...
```

`ActionRegistry` lets Automation, Deck, UI buttons, and the Local API consume
one provider operation contract. It does **not** replace `TaskRegistry`.
Automation tasks retain their stable types, completion/cancellation behavior,
editor metadata, and persisted configuration. A task may later be a thin adapter
that resolves Variables and delegates to an action.

Provider actions use provider-owned IDs such as `obs.scene.switch`. A future
generic action such as `broadcast.scene.switch` must route through an explicitly
selected active provider stored by Core. It must never choose OBS, PRISM, or a
Broadcaster provider by registration/load order. Those providers remain peers.
Generic routing is deferred; only provider-specific actions are required for the
OBS proof of concept.

### Navigation, tabs, and docks

The shell navigation target has two vertical regions. The upper, scrollable
region contains Dashboard/Core working pages, Automation, enabled module tabs,
and other day-to-day pages. Users may drag these entries into a preferred order,
which Core persists as presentation state only. That order never affects module
discovery, registration, configuration, startup, or shutdown ordering.

The bottom region is pinned, does not scroll away, and always appears in this
order: **Modules**, **Settings**, **Wiki**. The Modules entry is a Core page; it
is not contributed by an optional module. The 0.3 foundation records this shell
contract but does not redesign the current navigation before a module actually
contributes UI.

```python
@dataclass(frozen=True, slots=True)
class TabContribution:
    tab_id: str
    owner_module_id: str
    label: str
    order: int
    group: str
    factory: Callable[[QWidget], QWidget]
    icon: str = ""
    availability: AvailabilityProbe | None = None


@dataclass(frozen=True, slots=True)
class DockContribution:
    dock_id: str
    owner_module_id: str
    title: str
    factory: Callable[[QWidget], QWidget]
    allowed_areas: frozenset[str]
    default_area: str
    floatable: bool = True
    visible_by_default: bool = False
    persistence_id: str = ""      # stable QMainWindow object identity
```

Core tabs remain statically available and module tabs are merged into the same
existing navigation model by stable `tab_id` and deterministic `order`.
`MainWindow` eventually consumes already-registered contributions; it does not
import provider page classes. Factories run on the UI thread only after module
configuration and before window-state restoration.

Core owns each `QDockWidget`, allowed placement, layout persistence, and missing
dock recovery. A module owns only the dock's content widget. `persistence_id`
must remain stable across releases. State restoration tolerates a disabled or
missing dock: its saved bytes are preserved where practical, ignored safely for
that run, and must not corrupt other dock placement. Re-enabling the same stable
dock can restore its prior layout. Docks do not replace primary tabs.

### Trigger providers

```python
@dataclass(frozen=True, slots=True)
class TriggerProviderContribution:
    provider_id: str
    owner_module_id: str
    trigger_types: tuple[TriggerTypeMetadata, ...]
    store: TriggerStoreContract
    evaluator: TriggerEvaluator
    editor_factory: TriggerEditorFactory
    context_normalizer: TriggerContextNormalizer
    variable_definitions: tuple[VariableDefinition, ...]
```

Trigger stores and editors remain domain-owned. Core registers provider/type
metadata and publishes normalized `TriggerEvent` objects through the existing
`AutomationService`; it does not create one giant trigger store. The established
`TriggerEvent` fields—stable trigger ID, service, type, immutable causal context,
event ID, and occurrence time—remain the provider boundary. A provider must
normalize context before publication so queued routines see the event-causing
values rather than later live state.

References to a missing or disabled trigger provider remain in routines and
Backup. They display as unavailable and do not execute. They are never rebound
by label or deleted merely because the module is absent.

### Variables

There is exactly one Core-owned `VariableRegistry`. Each module registers
definitions/providers for namespaces it owns (for example `obs.*` and
`music.*`). Namespace ownership is declared during module registration.
Duplicate canonical names are registration errors, including collisions between
Core and a module. No registration-order override, flat alias family, or second
output catalog is permitted. `automation.*` continues to be execution-scoped and
owned by Core's Automation output contract.

### Backup contributors

```python
@dataclass(frozen=True, slots=True)
class BackupContributor:
    contributor_id: str
    owner_module_id: str
    display_name: str
    schema_version: int
    dependencies: tuple[str, ...]
    export: Callable[[], JsonObject]
    validate: Callable[[JsonObject], None]
    restore: Callable[[JsonObject, RestoreContext], None]
    contains_secrets: bool = False
```

`BackupManager` remains the Core mechanism: it selects contributors, validates
the complete archive, redacts/rejects secrets, stages restore, creates a safety
backup, orders dependencies, commits atomically, and rolls back on failure.
Modules own their component schema and export/validate/restore implementation.
Contributor IDs and schema versions are written into the manifest.

Data for a disabled or temporarily unavailable module is preserved in archives
and on disk. Restore must retain a valid unavailable component rather than
silently drop it; applying it may be deferred until its owner is available.
Routines that reference missing actions, tasks, or triggers are restored intact
and reported unavailable. Deleting module data is a separate, explicit,
confirmed user operation—never a side effect of disable, uninstall, or restore.
`contains_secrets=True` is rejected for portable Backup in the initial contract;
credentials remain under the protected credential policy.

## Events and threading

Public module events use documented, namespaced names such as
`obs.connection.changed`; their payload schema and owner are part of the
contract. Private Qt signals remain internal implementation details and are not
automatically public events.

`Events.emit()` is synchronous and invokes subscribers on the emitter's thread.
The event contract therefore requires:

- each subscription is recorded against its owning module/lifecycle token;
- stop/failed-start cleanup unsubscribes every owned callback;
- one subscriber exception is isolated and logged without preventing later
  subscribers, matching current `Events` behavior;
- worker-originated callbacks cross a subsystem Qt queued signal/slot bridge
  before touching widgets; and
- handlers do not perform blocking network, filesystem, subprocess, long-wait,
  or heavy CPU work on the Qt UI thread.

Modules must use their owning service/worker for blocking work and marshal
results through Qt-safe signals. Shutdown invalidates late callbacks and waits
only within bounded existing shutdown policies. Module contracts must not retain
deleted `QObject` wrappers or use delayed callbacks that outlive their owner.

## Settings, secrets, and data ownership

Core provides namespaced settings and protected-secret primitives. A module may
read/write only its namespace through `ModuleContext`; it must not receive the
raw application `QSettings` object or another module's credential store.
Machine-local UI state remains separate from portable domain data.

Modules own domain data and current-schema stores. Core owns cross-domain
identity/reference rules, Backup orchestration, and global settings policy.
Secrets are excluded from logs, diagnostics, Support Bundles, and portable
Backup unless an existing protected-credential policy explicitly permits that
class.

Core stores desired module enablement in a dedicated current-schema manifest,
`config/modules.json`, rather than extending provider schemas or raw QSettings.
The immutable descriptor supplies the default. A user change writes only the
desired next-start override; the active process remains unchanged and reports
that restart is required. Disabling a module never deletes its domain data.
This new 0.3-only manifest is not a migration of a publicly shipped schema.

## Startup and shell target

The target startup sequence is:

1. acquire the Hub data-root/single-writer boundary and initialize diagnostics;
2. create Qt and Core settings, event, Variable, Task, Action, Trigger, Backup,
   Automation, routine, and queue services;
3. scan the known bundled module location and match entries against Core's
   explicit trusted first-party factory allowlist;
4. validate descriptors and the persisted next-start enabled state;
5. atomically register contributions for all enabled compatible modules;
6. configure module stores/settings and migrations;
7. start enabled modules in deterministic order;
8. create the shell and render Core plus module tab/dock contributions;
9. restore geometry/dock state after all available docks exist; and
10. enter the Qt event loop.

Shutdown rejects new work, saves Core window state, stops started modules in
reverse order, cancels queues and owned requests safely, then clears Core event
listeners and diagnostics.

`MainWindow` remains the shell/composition consumer, not a module context. Its
target responsibilities are navigation, registered tab/dock rendering, global
window state, shell-level status, and Qt signal wiring. It should stop:

- constructing Twitch, OBS, or Music services and their domain stores;
- registering provider-specific tasks, Variables, actions, or trigger types;
- evaluating OBS/Music/Twitch trigger stores directly;
- knowing provider schema paths or configuration models; and
- manually ordering provider shutdown.

The application bootstrap plus `ModuleRegistry` becomes the composition root.
This is an incremental target; it does not justify a single large rewrite.
The current foundation implements descriptor validation, trusted bundled
discovery, enablement persistence/restart semantics, deterministic lifecycle,
registration commit/rollback hooks, and the Core `ActionRegistry`. It is not yet
wired into product startup because no existing provider has been converted.

## OBS first-module extraction boundary

The bundled OBS module will eventually own:

- `ObsWebSocketService` lifecycle and reconnect behavior;
- OBS connection configuration and protected password access;
- provider-specific actions and existing OBS Automation task handlers/metadata;
- `ObsTriggerStore`, trigger metadata/editors/evaluation, and subscriptions;
- `obs.*` Variable provider registrations;
- OBS settings/connections UI and any OBS-contributed primary tab or live dock;
- OBS event subscriptions and cleanup; and
- its Backup contributor for portable, non-secret OBS configuration.

Core continues to own:

- routines, queues, Automation execution/cancellation, and `TaskRegistry`;
- `VariableRegistry`, `ActionRegistry`, `ModuleRegistry`, and trigger-provider
  registration;
- the event bus and Qt/threading policy;
- Backup archive orchestration, safety copies, restore transactions, and secret
  exclusions;
- the shell, navigation, dock persistence, settings/secret primitives, and
  Local API exposure policy.

Existing task types, trigger IDs/types, Variable names, connection paths,
password protection, and Backup meanings remain stable during extraction. OBS
task handlers may be registered by the module without changing persisted task
configuration. OBS event evaluation moves behind the provider contribution,
then emits the same normalized `TriggerEvent` into Core Automation.

## Failure and availability semantics

| Condition | Required behavior |
| --- | --- |
| Duplicate module/contribution/action/tab/dock ID | Reject registration deterministically; do not choose by load order. |
| Missing optional module | Start Core and independent modules; preserve and display its data/references as unavailable. |
| Disabled module | Do not configure/start it; retain data and stable references; require restart to change state. |
| Incompatible module/Core version | Do not start; show the compatibility reason; never attempt best-effort execution. |
| Module registration/configuration failure | Roll back that module's contribution transaction; mark unavailable. |
| Module start failure | Stop its partial runtime, unsubscribe, close resources, and keep independent modules usable. |
| Module shutdown failure | Log the owning module and error, continue reverse shutdown, and never leave new callbacks accepted. |
| Missing task/action/trigger provider | Routine remains editable and restorable but execution fails clearly at that item/reference. |
| Missing tab/dock contribution | Navigation/layout restores remaining contributions safely; no placeholder widget is required. |
| Missing Backup contributor on restore | Preserve the component; report it unavailable; do not discard or partially publish it. |
| Core registry/Automation/Backup failure | Abort startup or operation safely because Core invariants cannot be guaranteed. |

There is no silent fallback from a provider-specific action to a different
provider, no rebinding by display name, and no automatic deletion of unavailable
references.

## Public-Alpha migration constraints

`0.1` and `0.2` user data are supported upgrade inputs. Modularization is not a
reason to relocate stores or change schemas. The first implementation should
prefer changing construction/ownership while leaving durable paths and formats
intact.

If a later step truly changes a path, schema, or owning writer, it must:

1. verify the publicly shipped source versions;
2. create a recoverable safety backup before destructive work;
3. migrate old data to one current representation in an isolated boundary;
4. validate all stable IDs and cross-store references before publication;
5. publish atomically or leave the original data intact;
6. load only the current representation at runtime; and
7. remove the obsolete runtime path—no permanent dual reads/writes or shim.

For OBS specifically, extraction must not change `obs/connection.json`,
`obs/triggers.json`, protected password ownership, task/trigger IDs, or Backup
semantics merely to match a new package layout. Any unavoidable change requires
a separately reviewed migration plan and compatibility tests before code moves.

## Third-party boundary

The 0.3 foundation registers only explicitly bundled, trusted modules shipped
with Hub. Core may observe directory names in its known bundled location, but it
never imports an unknown entry: executable factories remain an explicit Core
allowlist. It does not execute downloaded code and provides no marketplace,
external plugin-folder scanning, install/update/uninstall workflow, permission
consent UI, dependency resolution, sandbox, trust/signature model, or hot
loading. Those are separate security/product projects and must not be implied
by the internal module vocabulary.

## Open questions for implementation review

These are genuine decisions still required before provider conversion:

1. What portable Backup policy, if any, should apply to the machine/runtime
   module enablement manifest once the Modules page exists?
2. Which existing registries need staging adapters behind the implemented
   registration commit/rollback hooks before OBS conversion can publish
   contributions atomically?
3. Should `TaskRegistry.register()` reject duplicate handlers as strictly as
   metadata does? The current handler registration overwrites by type.
4. How should unavailable task/action/trigger references be represented in the
   existing editors without adding persistent compatibility objects?
5. How should Backup retain an archive component whose contributor is absent
   during restore while preserving the current all-or-nothing publication rule?
6. Which existing OBS UI belongs in a primary tab, the Connections page, or no
   new contribution at all? Extraction must not force a navigation redesign.
7. What minimal availability result shape is shared by Action, tab, dock, and
   trigger catalogs without creating a generic service locator?
8. Should module stop be synchronous with strict bounded work, or may Core await
   a Qt signal/future under one global shutdown deadline?
9. What is the eventual user experience for selecting an active broadcaster
   provider before generic `broadcast.*` routing is introduced?
10. Does 0.3 need only one persisted dock layout, or a separately designed
    workspace model later?
11. If third-party modules are pursued after 0.3, what signing, trust,
    permission, and sandbox boundary is acceptable? This is not answered by the
    first-party module API.

These questions are deferred intentionally; implementations must not answer them
accidentally through ad hoc coupling.

## Implementation gate before OBS conversion

The first code slice now provides Core contract types and registry tests without
moving provider behavior. OBS extraction proceeds on its own branch in small
steps that preserve all public data contracts and keep each step testable and
reversible in source control. No provider conversion belongs in the foundation
commit.
