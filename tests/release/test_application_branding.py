from __future__ import annotations

import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from products.ai import ai_main
from products.hub import hub_main
from products.ai import streamhouse_ai
from products.hub import streamhouse_hub
from shared import streamhouse_shared
from shared.streamhouse_runtime.qt_settings import (
    AI_APPLICATION_NAME,
    HUB_APPLICATION_NAME,
    ORGANIZATION_NAME,
)
from products.ai.streamhouse_ai.app import configure_application as configure_ai
from products.hub.streamhouse_hub.app import configure_application as configure_hub


class ApplicationBrandingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])
        cls.root = Path(__file__).resolve().parents[2]

    def test_new_entry_points_and_packages_import(self) -> None:
        self.assertTrue(callable(hub_main.main))
        self.assertTrue(callable(ai_main.main))
        self.assertTrue(Path(streamhouse_hub.__file__).exists())
        self.assertTrue(Path(streamhouse_ai.__file__).exists())
        self.assertTrue(Path(streamhouse_shared.__file__).exists())
        self.assertFalse((self.root / "main.py").exists())
        self.assertFalse((self.root / "companion_main.py").exists())

    def test_hub_qt_metadata_uses_streamhouse_brand(self) -> None:
        configure_hub(self.application)
        self.assertEqual(self.application.applicationName(), HUB_APPLICATION_NAME)
        self.assertEqual(self.application.organizationName(), ORGANIZATION_NAME)

    def test_ai_qt_metadata_uses_streamhouse_brand(self) -> None:
        configure_ai(self.application)
        self.assertEqual(self.application.applicationName(), AI_APPLICATION_NAME)
        self.assertEqual(self.application.organizationName(), ORGANIZATION_NAME)

    def test_build_and_release_names_are_independent(self) -> None:
        build = (self.root / "tools" / "build" / "build_hub.ps1").read_text(
            encoding="utf-8"
        ) + (self.root / "tools" / "build" / "build_ai.ps1").read_text(
            encoding="utf-8"
        )
        release = "".join(
            (self.root / "tools" / "release" / name).read_text(encoding="utf-8")
            for name in ("package_hub.ps1", "package_ai.ps1", "package_all.ps1")
        )
        smoke = (
            self.root / "tools" / "smoke" / "smoke_packaged.ps1"
        ).read_text(encoding="utf-8")
        for text in (build, release, smoke):
            self.assertIn("StreamhouseHub", text)
            self.assertIn("StreamhouseAI", text)
            self.assertNotIn("SallyBot", text)
            self.assertNotIn("SallyAICompanion", text)
        self.assertIn('--exclude-module "products.ai.engine"', build)
        self.assertIn('--exclude-module "products.ai.streamhouse_ai"', build)
        self.assertIn('--exclude-module "products.hub"', build)


if __name__ == "__main__":
    unittest.main()
