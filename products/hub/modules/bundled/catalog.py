from __future__ import annotations

from products.hub.modules.bundled.obs import OBS_MODULE_ID, ObsModule
from products.hub.modules.discovery import BundledModuleSpec


def trusted_bundled_module_specs() -> tuple[BundledModuleSpec, ...]:
    """Return Core's explicit first-party executable module allowlist."""

    return (BundledModuleSpec(OBS_MODULE_ID, "obs", ObsModule),)
