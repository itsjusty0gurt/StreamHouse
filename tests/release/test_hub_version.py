from __future__ import annotations

import re
import unittest
from pathlib import Path

from products.hub.version import (
    HUB_INSTALLER_BASENAME,
    HUB_VERSION,
    HUB_WINDOWS_VERSION,
)


class HubVersionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[2]

    def test_hub_release_version_is_product_owned(self) -> None:
        self.assertEqual(HUB_VERSION, "0.3.0-alpha")
        self.assertEqual(HUB_WINDOWS_VERSION, "0.3.0.0")
        self.assertEqual(
            HUB_INSTALLER_BASENAME,
            "StreamhouseHub-0.3.0-alpha-Setup",
        )

        package_hub = (
            self.root / "tools" / "release" / "package_hub.ps1"
        ).read_text(encoding="utf-8")
        package_ai = (
            self.root / "tools" / "release" / "package_ai.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("products\\hub\\version.py", package_hub)
        self.assertIn("HUB_VERSION", package_hub)
        self.assertIn('"StreamhouseHub-$version-windows-x64.zip"', package_hub)
        self.assertIn("shared\\streamhouse_runtime\\version.py", package_ai)
        self.assertNotIn("products\\hub\\version.py", package_ai)

    def test_installer_metadata_matches_hub_version_and_identity(self) -> None:
        installer = (
            self.root / "tools" / "packaging" / "StreamhouseHub.iss"
        ).read_text(encoding="utf-8")
        self.assertIn(f'#define AppVersion "{HUB_VERSION}"', installer)
        self.assertIn(
            f'#define AppNumericVersion "{HUB_WINDOWS_VERSION}"',
            installer,
        )
        self.assertIn(
            f'#define AppInstallerBasename "{HUB_INSTALLER_BASENAME}"',
            installer,
        )
        self.assertIn("OutputBaseFilename={#AppInstallerBasename}", installer)
        self.assertIn(
            "VersionInfoOriginalFileName={#AppInstallerBasename}.exe",
            installer,
        )
        self.assertIn("VersionInfoProductVersion={#AppNumericVersion}", installer)
        self.assertIn("VersionInfoProductTextVersion={#AppVersion}", installer)
        self.assertIn("VersionInfoVersion={#AppNumericVersion}", installer)
        self.assertIn("AppId={{B16DA343-4773-4819-B505-65911DA0675B}", installer)

    def test_windows_executable_metadata_matches_hub_version(self) -> None:
        metadata = (
            self.root / "tools" / "packaging" / "windows-hub-version-info.txt"
        ).read_text(encoding="utf-8")
        numeric_tuple = tuple(int(part) for part in HUB_WINDOWS_VERSION.split("."))
        self.assertIn(f"filevers={numeric_tuple}", metadata)
        self.assertIn(f"prodvers={numeric_tuple}", metadata)
        self.assertIn(f"u'FileVersion', u'{HUB_VERSION}'", metadata)
        self.assertIn(f"u'ProductVersion', u'{HUB_VERSION}'", metadata)

    def test_active_hub_release_files_have_no_stale_alpha_version(self) -> None:
        active_files = (
            self.root / "products" / "hub" / "version.py",
            self.root / "tools" / "release" / "package_hub.ps1",
            self.root / "tools" / "packaging" / "StreamhouseHub.iss",
            self.root / "tools" / "packaging" / "windows-hub-version-info.txt",
        )
        stale_patterns = (
            re.compile(r"0\.1\.0-alpha"),
            re.compile(r"0\.1\.0\.0"),
            re.compile(r"StreamhouseHub-0\.1\.0-alpha"),
            re.compile(r"0\.2\.0-alpha"),
            re.compile(r"0\.2\.0\.0"),
            re.compile(r"StreamhouseHub-0\.2\.0-alpha"),
        )
        for path in active_files:
            contents = path.read_text(encoding="utf-8")
            for pattern in stale_patterns:
                self.assertIsNone(pattern.search(contents), f"{path}: {pattern.pattern}")


if __name__ == "__main__":
    unittest.main()
