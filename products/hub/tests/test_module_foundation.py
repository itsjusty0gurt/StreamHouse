from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from products.hub.modules import (
    ActionDescriptor,
    ActionExecutionContext,
    ActionParameter,
    ActionRegistry,
    ActionResult,
    Availability,
    BundledModuleDiscovery,
    BundledModuleSpec,
    ModuleContext,
    ModuleDescriptor,
    ModuleEnablementStore,
    ModuleLifecycle,
    ModuleRegistry,
)
from products.hub.modules import bootstrap as module_bootstrap


@dataclass
class _FakeModule:
    descriptor: ModuleDescriptor
    calls: list[str] = field(default_factory=list)
    fail_at: str = ""

    def register(self, _context: ModuleContext) -> None:
        self.calls.append("register")
        if self.fail_at == "register":
            raise RuntimeError("registration failed")

    def configure(self, _context: ModuleContext) -> None:
        self.calls.append("configure")
        if self.fail_at == "configure":
            raise RuntimeError("configuration failed")

    def start(self) -> None:
        self.calls.append("start")
        if self.fail_at == "start":
            raise RuntimeError("startup failed")

    def stop(self) -> None:
        self.calls.append("stop")


def _descriptor(module_id: str, **values: object) -> ModuleDescriptor:
    arguments = {
        "module_id": module_id,
        "display_name": module_id,
        "version": "0.1.0",
        "category": "Integration",
    }
    arguments.update(values)
    return ModuleDescriptor(**arguments)  # type: ignore[arg-type]


def _registry(path: Path, **values: object) -> ModuleRegistry:
    arguments = {
        "core_version": "0.3.0-alpha",
        "core_capabilities": frozenset({"automation", "variables"}),
        "enablement_store": ModuleEnablementStore(path),
    }
    arguments.update(values)
    return ModuleRegistry(**arguments)  # type: ignore[arg-type]


def _context(
    descriptor: ModuleDescriptor,
    calls: list[str] | None = None,
) -> ModuleContext:
    transaction_calls = calls if calls is not None else []
    return ModuleContext(
        core_version="0.3.0-alpha",
        events=object(),
        variables=object(),
        tasks=object(),
        actions=ActionRegistry(),
        commit_registration=lambda: transaction_calls.append(f"commit:{descriptor.module_id}"),
        rollback_registration=lambda: transaction_calls.append(f"rollback:{descriptor.module_id}"),
    )


def test_descriptor_requires_namespaced_stable_id() -> None:
    with pytest.raises(ValueError, match="namespaced"):
        _descriptor("obs")


def test_registry_rejects_non_bundled_module_in_first_party_foundation(tmp_path: Path) -> None:
    registry = _registry(tmp_path / "modules.json")
    with pytest.raises(ValueError, match="trusted bundled"):
        registry.register(_FakeModule(_descriptor("external.obs", bundled=False)))


def test_action_registry_rejects_duplicates_and_validates_invocation() -> None:
    registry = ActionRegistry()
    received: list[tuple[dict[str, object], dict[str, object]]] = []

    def handler(parameters: object, context: ActionExecutionContext) -> ActionResult:
        received.append((dict(parameters), dict(context.values)))  # type: ignore[arg-type]
        return ActionResult(True, outputs={"scene": parameters["scene"]})  # type: ignore[index]

    descriptor = ActionDescriptor(
        action_id="obs.scene.switch",
        display_name="Switch Scene",
        owner_module_id="streamhouse.obs",
        parameters=(ActionParameter("scene", "Scene", "text", variable_aware=True),),
        handler=handler,
    )
    registry.register(descriptor)

    with pytest.raises(ValueError, match="already registered"):
        registry.register(descriptor)
    assert registry.invoke("obs.scene.switch", {}).succeeded is False
    assert registry.invoke("obs.scene.switch", {"scene": "Main", "extra": 1}).succeeded is False
    result = registry.invoke(
        "obs.scene.switch",
        {"scene": "Main"},
        ActionExecutionContext({"routine_id": "routine-1"}),
    )
    assert result.succeeded is True
    assert dict(result.outputs) == {"scene": "Main"}
    assert received == [({"scene": "Main"}, {"routine_id": "routine-1"})]


def test_action_registry_honors_availability() -> None:
    registry = ActionRegistry()
    registry.register(
        ActionDescriptor(
            action_id="obs.scene.switch",
            display_name="Switch Scene",
            owner_module_id="streamhouse.obs",
            handler=lambda _parameters, _context: ActionResult(True),
            availability=lambda: Availability(False, "OBS is disconnected."),
        )
    )
    result = registry.invoke("obs.scene.switch", {})
    assert result == ActionResult(False, "OBS is disconnected.")


def test_discovery_loads_only_allowlisted_bundled_factories(tmp_path: Path) -> None:
    (tmp_path / "obs").mkdir()
    (tmp_path / "downloaded-plugin").mkdir()
    module = _FakeModule(_descriptor("streamhouse.obs"))
    result = BundledModuleDiscovery(
        tmp_path,
        (BundledModuleSpec("streamhouse.obs", "obs", lambda: module),),
    ).discover()
    assert result.modules == (module,)
    assert result.missing_trusted == ()
    assert result.untrusted_entries == ("downloaded-plugin",)


def test_discovery_reports_missing_trusted_module_without_importing(tmp_path: Path) -> None:
    factory_called = False

    def factory() -> _FakeModule:
        nonlocal factory_called
        factory_called = True
        return _FakeModule(_descriptor("streamhouse.obs"))

    result = BundledModuleDiscovery(
        tmp_path,
        (BundledModuleSpec("streamhouse.obs", "obs", factory),),
    ).discover()
    assert result.modules == ()
    assert result.missing_trusted == ("streamhouse.obs",)
    assert factory_called is False


def test_embedded_discovery_loads_allowlisted_module_without_source_directory(
    tmp_path: Path,
) -> None:
    module = _FakeModule(_descriptor("streamhouse.obs"))
    result = BundledModuleDiscovery(
        tmp_path,
        (BundledModuleSpec("streamhouse.obs", "obs", lambda: module),),
        embedded=True,
    ).discover()

    assert result.modules == (module,)
    assert result.missing_trusted == ()


def test_frozen_bootstrap_discovers_trusted_obs_without_source_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(module_bootstrap, "BUNDLED_MODULES_ROOT", tmp_path / "missing")
    monkeypatch.setattr(
        module_bootstrap,
        "ModuleEnablementStore",
        lambda: ModuleEnablementStore(tmp_path / "modules.json"),
    )

    registry = module_bootstrap.create_bundled_module_registry()

    assert registry.descriptor("streamhouse.obs") is not None


def test_enablement_change_is_persisted_for_next_restart_only(tmp_path: Path) -> None:
    settings_path = tmp_path / "modules.json"
    registry = _registry(settings_path)
    registry.register(_FakeModule(_descriptor("streamhouse.obs")))

    restart = registry.set_enabled("streamhouse.obs", False)
    status = registry.status("streamhouse.obs")
    assert restart.required is True
    assert status is not None
    assert status.enabled is True
    assert status.desired_enabled is False
    assert status.restart_required is True

    restarted = _registry(settings_path)
    restarted.register(_FakeModule(_descriptor("streamhouse.obs")))
    restarted_status = restarted.status("streamhouse.obs")
    assert restarted_status is not None
    assert restarted_status.lifecycle is ModuleLifecycle.DISABLED
    assert restarted_status.enabled is False


def test_registry_lifecycle_is_deterministic_and_shutdown_is_reversed(tmp_path: Path) -> None:
    calls: list[str] = []

    class OrderedModule(_FakeModule):
        def register(self, context: ModuleContext) -> None:
            calls.append(f"register:{self.descriptor.module_id}")

        def configure(self, context: ModuleContext) -> None:
            calls.append(f"configure:{self.descriptor.module_id}")

        def start(self) -> None:
            calls.append(f"start:{self.descriptor.module_id}")

        def stop(self) -> None:
            calls.append(f"stop:{self.descriptor.module_id}")

    registry = _registry(tmp_path / "modules.json")
    registry.register(OrderedModule(_descriptor("streamhouse.zeta")))
    registry.register(OrderedModule(_descriptor("streamhouse.alpha")))
    registry.register_enabled(lambda descriptor: _context(descriptor, calls))
    registry.configure_enabled()
    registry.start_enabled()
    registry.stop_started()

    assert calls == [
        "register:streamhouse.alpha",
        "commit:streamhouse.alpha",
        "register:streamhouse.zeta",
        "commit:streamhouse.zeta",
        "configure:streamhouse.alpha",
        "configure:streamhouse.zeta",
        "start:streamhouse.alpha",
        "start:streamhouse.zeta",
        "stop:streamhouse.zeta",
        "stop:streamhouse.alpha",
    ]


def test_registration_failure_rolls_back_only_failed_module(tmp_path: Path) -> None:
    transactions: list[str] = []
    failed = _FakeModule(_descriptor("streamhouse.alpha"), fail_at="register")
    healthy = _FakeModule(_descriptor("streamhouse.zeta"))
    registry = _registry(tmp_path / "modules.json")
    registry.register(failed)
    registry.register(healthy)
    registry.register_enabled(lambda descriptor: _context(descriptor, transactions))
    registry.configure_enabled()
    registry.start_enabled()

    assert transactions == ["rollback:streamhouse.alpha", "commit:streamhouse.zeta"]
    assert registry.status("streamhouse.alpha").lifecycle is ModuleLifecycle.FAILED  # type: ignore[union-attr]
    assert registry.status("streamhouse.zeta").lifecycle is ModuleLifecycle.STARTED  # type: ignore[union-attr]


def test_incompatible_or_disabled_module_never_enters_lifecycle(tmp_path: Path) -> None:
    settings_path = tmp_path / "modules.json"
    store = ModuleEnablementStore(settings_path)
    store.set_enabled("streamhouse.disabled", False)
    registry = ModuleRegistry(
        core_version="0.3.0-alpha",
        core_capabilities=frozenset(),
        enablement_store=store,
    )
    disabled = _FakeModule(_descriptor("streamhouse.disabled"))
    incompatible = _FakeModule(
        _descriptor(
            "streamhouse.incompatible",
            required_core_capabilities=frozenset({"missing"}),
        )
    )
    registry.register(disabled)
    registry.register(incompatible)
    registry.register_enabled(_context)
    registry.configure_enabled()
    registry.start_enabled()
    assert disabled.calls == []
    assert incompatible.calls == []
    assert registry.status("streamhouse.disabled").lifecycle is ModuleLifecycle.DISABLED  # type: ignore[union-attr]
    assert registry.status("streamhouse.incompatible").lifecycle is ModuleLifecycle.INCOMPATIBLE  # type: ignore[union-attr]
