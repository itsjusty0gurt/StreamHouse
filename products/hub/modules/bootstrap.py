from __future__ import annotations

import sys

from products.hub.modules.bundled.catalog import trusted_bundled_module_specs
from products.hub.modules.discovery import BUNDLED_MODULES_ROOT, BundledModuleDiscovery
from products.hub.modules.registry import ModuleRegistry
from products.hub.modules.settings import ModuleEnablementStore
from products.hub.version import HUB_VERSION


CORE_MODULE_CAPABILITIES = frozenset(
    {
        "actions",
        "automation",
        "automation_pages",
        "backup",
        "tabs",
        "tasks",
        "triggers",
        "variables",
    }
)


def create_bundled_module_registry() -> ModuleRegistry:
    registry = ModuleRegistry(
        core_version=HUB_VERSION,
        core_capabilities=CORE_MODULE_CAPABILITIES,
        enablement_store=ModuleEnablementStore(),
    )
    discovery = BundledModuleDiscovery(
        BUNDLED_MODULES_ROOT,
        trusted_bundled_module_specs(),
        embedded=bool(getattr(sys, "frozen", False)),
    ).discover()
    for module in discovery.modules:
        registry.register(module)
    return registry
