#!/usr/bin/env python3
"""Render resources/icon.svg into the application icon. Build-time only.

The result is committed as resources/AppIcon.icns, so building the app needs no
SVG renderer. Re-run this after editing the artwork; it needs `rsvg-convert`
(`brew install librsvg`).
"""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "resources/icon.svg"
OUTPUT = ROOT / "resources/AppIcon.icns"
# Apple's iconset contents: (point size, scale).
SIZES = [(size, scale) for size in (16, 32, 128, 256, 512) for scale in (1, 2)]
# Below this the play mark inside the droplet is too small to read.
DETAIL_THRESHOLD = 32


def main():
    if not shutil.which("rsvg-convert"):
        raise SystemExit("rsvg-convert is required: brew install librsvg")
    artwork = SOURCE.read_text()
    simplified = re.sub(r'\s*<path id="launch-mark".*?/>', "", artwork, flags=re.S)
    if simplified == artwork:
        raise SystemExit("icon.svg no longer contains the launch-mark element")
    with tempfile.TemporaryDirectory() as scratch:
        iconset = Path(scratch) / "AppIcon.iconset"
        iconset.mkdir()
        for size, scale in SIZES:
            pixels = size * scale
            suffix = "" if scale == 1 else f"@{scale}x"
            source = Path(scratch) / f"source-{pixels}.svg"
            source.write_text(simplified if pixels <= DETAIL_THRESHOLD else artwork)
            subprocess.run(
                ["rsvg-convert", "-w", str(pixels), "-h", str(pixels), str(source),
                 "-o", str(iconset / f"icon_{size}x{size}{suffix}.png")],
                check=True,
            )
        subprocess.run(["iconutil", "--convert", "icns", str(iconset), "--output", str(OUTPUT)], check=True)
    print(f"Built {OUTPUT} ({OUTPUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
