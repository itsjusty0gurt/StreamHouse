"""Build a multi-resolution Windows ICO from existing PNG icon files."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def png_size(payload: bytes) -> tuple[int, int]:
    """Return PNG dimensions after validating the signature and IHDR chunk."""

    if len(payload) < 24 or not payload.startswith(PNG_SIGNATURE):
        raise ValueError("Input is not a valid PNG file.")
    if payload[12:16] != b"IHDR":
        raise ValueError("PNG file does not begin with an IHDR chunk.")
    return struct.unpack(">II", payload[16:24])


def build_windows_icon(output: Path, sources: list[Path]) -> None:
    """Write an ICO containing the supplied square PNG images."""

    if not sources:
        raise ValueError("At least one PNG source is required.")

    images: list[tuple[int, bytes]] = []
    seen_sizes: set[int] = set()
    for source in sources:
        payload = source.read_bytes()
        width, height = png_size(payload)
        if width != height:
            raise ValueError(f"Icon source must be square: {source}")
        if width > 256:
            raise ValueError(f"ICO PNG dimensions cannot exceed 256px: {source}")
        if width in seen_sizes:
            raise ValueError(f"Duplicate {width}px icon source: {source}")
        seen_sizes.add(width)
        images.append((width, payload))

    images.sort(key=lambda item: item[0])
    offset = 6 + 16 * len(images)
    entries = bytearray()
    payloads = bytearray()
    for size, payload in images:
        encoded_size = 0 if size == 256 else size
        entries.extend(
            struct.pack(
                "<BBBBHHII",
                encoded_size,
                encoded_size,
                0,
                0,
                1,
                32,
                len(payload),
                offset,
            )
        )
        payloads.extend(payload)
        offset += len(payload)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(
        struct.pack("<HHH", 0, 1, len(images)) + entries + payloads
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("sources", nargs="+", type=Path)
    arguments = parser.parse_args()
    build_windows_icon(arguments.output, arguments.sources)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
