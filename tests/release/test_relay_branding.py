from __future__ import annotations

import unittest
from pathlib import Path


class RelayBrandingRegressionTests(unittest.TestCase):
    def test_legacy_infrastructure_names_are_confined_to_explicit_allowlist(self) -> None:
        root = Path(__file__).resolve().parents[2]
        patterns = (
            "sally-soundboard-relay",
            "sally_relay",
            "x-sally",
            "/api/sally",
            "sally relay",
            "sally soundboard",
            "sally extension",
        )
        allowed = {
            "docs/architecture/overview.md",
            "docs/deployment/relay-brand-migration.md",
            "docs/deployment/relay-brand-inventory.md",
            "extensions/twitch/app/relay_server.py",
            "extensions/twitch/app/viewer.js",
            "products/hub/soundboard/relay.py",
            "products/hub/tests/test_soundboard.py",
            "shared/streamhouse_runtime/relay_config.py",
            "shared/streamhouse_shared/protocol.py",
            "shared/tests/test_relay_config.py",
            "tests/release/test_relay_branding.py",
            "tests/release/test_release_tools.py",
        }
        found: set[str] = set()
        roots = (
            root / "extensions" / "twitch",
            root / "products" / "hub" / "soundboard",
            root / "products" / "hub" / "tests",
            root / "shared",
            root / "tests" / "release",
            root / "docs",
            root / "tools",
        )
        candidates = [root / "render.yaml"]
        for scan_root in roots:
            candidates.extend(path for path in scan_root.rglob("*") if path.is_file())
        for path in candidates:
            if "__pycache__" in path.parts or path.suffix.lower() in {".png", ".ico"}:
                continue
            try:
                text = path.read_text(encoding="utf-8").casefold()
            except UnicodeDecodeError:
                continue
            if any(pattern in text for pattern in patterns):
                found.add(path.relative_to(root).as_posix())
        unexpected = sorted(found - allowed)
        self.assertEqual(unexpected, [], f"Unclassified legacy relay names: {unexpected}")

    def test_current_extension_and_render_configuration_are_modern(self) -> None:
        root = Path(__file__).resolve().parents[2]
        build_script = (root / "extensions/twitch/tools/build_extension.ps1").read_text(
            encoding="utf-8"
        )
        blueprint = (root / "render.yaml").read_text(encoding="utf-8")
        self.assertIn("window.STREAMHOUSE_RELAY_BASE", build_script)
        self.assertNotIn("window.SALLY_RELAY_BASE", build_script)
        self.assertIn("name: streamhouse-soundboard-relay", blueprint)
        self.assertIn("key: STREAMHOUSE_RELAY_KEYS", blueprint)
        self.assertIn("key: STREAMHOUSE_RELAY_DB", blueprint)
        self.assertNotIn("value:", blueprint)
        listing_builder = (
            root / "extensions/twitch/tools/build_listing_assets.py"
        ).read_text(encoding="utf-8")
        self.assertIn("streamhouse-brand-s.svg", listing_builder)
        self.assertNotIn("sally-icon", listing_builder.casefold())

        viewer = (root / "extensions/twitch/app/viewer.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("STREAMHOUSE_RELAY_BASE is authoritative", viewer)
        self.assertIn("SALLY_RELAY_BASE is deprecated", viewer)
        self.assertIn('endpoint("/api/streamhouse/trigger")', viewer)
        self.assertIn('endpoint(`/api/streamhouse/config${query}`)', viewer)
        relay_server = (root / "extensions/twitch/app/relay_server.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('origin.endswith(".ext-twitch.tv")', relay_server)
        self.assertIn("TWITCH_EXTENSION_SECRET is required", relay_server)


if __name__ == "__main__":
    unittest.main()
