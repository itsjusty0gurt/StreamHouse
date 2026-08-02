from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from tools.assets.build_windows_icon import build_windows_icon, png_size


class StreamhouseIconAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[2]
        cls.icon_root = cls.root / "shared" / "assets" / "streamhouse-icons"

    def test_runtime_product_icons_are_valid_256_pixel_pngs(self) -> None:
        for name in ("streamhouse-hub.png", "streamhouse-ai.png"):
            payload = (self.icon_root / name).read_bytes()
            self.assertEqual(png_size(payload), (256, 256))

    def test_windows_product_icons_contain_four_png_sizes(self) -> None:
        expected_sizes = [32, 64, 128, 256]
        for name in ("streamhouse-hub.ico", "streamhouse-ai.ico"):
            payload = (self.icon_root / "windows" / name).read_bytes()
            reserved, image_type, count = struct.unpack("<HHH", payload[:6])
            self.assertEqual((reserved, image_type, count), (0, 1, 4))
            sizes = []
            for index in range(count):
                offset = 6 + index * 16
                width, height = struct.unpack("<BB", payload[offset : offset + 2])
                sizes.append(256 if width == 0 else width)
                self.assertEqual(height, width)
            self.assertEqual(sizes, expected_sizes)

    def test_icon_builder_round_trips_png_payload(self) -> None:
        source = self.icon_root / "streamhouse-hub.png"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "test.ico"
            build_windows_icon(output, [source])
            payload = output.read_bytes()
        _reserved, _image_type, count = struct.unpack("<HHH", payload[:6])
        _width, _height, _colors, _reserved, _planes, _bits, size, offset = (
            struct.unpack("<BBBBHHII", payload[6:22])
        )
        self.assertEqual(count, 1)
        self.assertEqual(payload[offset : offset + size], source.read_bytes())

    def test_build_and_runtime_references_use_product_icons(self) -> None:
        hub_build = (self.root / "tools" / "build" / "build_hub.ps1").read_text(
            encoding="utf-8"
        )
        ai_build = (self.root / "tools" / "build" / "build_ai.ps1").read_text(
            encoding="utf-8"
        )
        hub_app = (
            self.root / "products" / "hub" / "streamhouse_hub" / "app.py"
        ).read_text(encoding="utf-8")
        ai_app = (
            self.root / "products" / "ai" / "streamhouse_ai" / "app.py"
        ).read_text(encoding="utf-8")
        self.assertIn("streamhouse-hub.ico", hub_build)
        self.assertIn("streamhouse-ai.ico", ai_build)
        self.assertIn("streamhouse-hub.png;assets\\streamhouse-icons", hub_build)
        self.assertNotIn("streamhouse-ai.png;assets\\streamhouse-icons", hub_build)
        self.assertIn("streamhouse-ai.png;assets\\streamhouse-icons", ai_build)
        self.assertNotIn("streamhouse-hub.png;assets\\streamhouse-icons", ai_build)
        self.assertIn("streamhouse-hub.png", hub_app)
        self.assertIn("streamhouse-ai.png", ai_app)
        for text in (hub_build, ai_build, hub_app, ai_app):
            self.assertNotIn("sally-icon", text)


if __name__ == "__main__":
    unittest.main()
