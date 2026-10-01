#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ZapTV.org
#
# This file is part of ClipTV. ClipTV is free software: you can redistribute
# it and/or modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. It is distributed WITHOUT
# ANY WARRANTY; see the GNU General Public License (LICENSE) for details.

"""Regenerate ClipTV's logo artwork from the SVG source in artwork/.

    python3 tools/build_assets.py [--check]

Renders with rsvg-convert (brew install librsvg) so the source of truth
stays vector. macOS `qlmanage` can also render SVG but flattens the alpha
channel, which is useless here. The source is artwork/cliptv-logo.svg, the
ZapTV family TV mark, copied in with its C2PA <metadata> block stripped;
rsvg-convert ignores the clapper animation in its <style> and draws the
static pose, clapper open.

Outputs, all RGBA PNG with transparent corners (alpha 0, except that the
8 px icon's mark fills 7 of its 8 rows, so its bottom corners keep a faint
trace of edge alpha, under 5%):

    org.zaptv.cliptv/icon_64x64.png   launcher icon
    store_icons/icon-64x64.png        app store icon set, the same artwork
    store_icons/icon-32x32.png        rendered once per size from the SVG,
    store_icons/icon-16x16.png        never downscaled from the 64 px icon
    store_icons/icon-8x8.png

Every size uses the ZapTV family layout: the whole SVG viewBox fitted to the
tile width and centred vertically, so at 64 px the mark is 64x57 at y=3.
The launcher draws the icon straight onto the desktop, and the mark's own
cream outline separates it from dark backgrounds, so no synthetic halo.

infinity_12.png and infinity_16.png are hand-made UI glyphs, not logo
artwork; this tool leaves them alone.

--check reports what would change without writing anything.
"""

import os
import subprocess
import sys
import tempfile

from PIL import Image, ImageChops

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ART = os.path.join(ROOT, "artwork")
LOGO = os.path.join(ART, "cliptv-logo.svg")

# Render big and downscale once: keeps the edges clean at every size.
RENDER_PX = 1200

ICONS = (
    ("org.zaptv.cliptv/icon_64x64.png", 64),
    ("store_icons/icon-64x64.png", 64),
    ("store_icons/icon-32x32.png", 32),
    ("store_icons/icon-16x16.png", 16),
    ("store_icons/icon-8x8.png", 8),
)


def render(svg, width):
    """SVG -> RGBA image at the requested width (height from the viewBox)."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        path = tmp.name
    try:
        subprocess.run(["rsvg-convert", "-w", str(width), "-o", path, svg],
                       check=True, capture_output=True)
        return Image.open(path).convert("RGBA").copy()
    finally:
        os.unlink(path)


def corner_alpha(img):
    w, h = img.size
    return sum(img.getpixel(c)[3] for c in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)))


def build_icon(art, size):
    """Fit the whole viewBox to the tile width, centred vertically.

    When the spare rows are odd the mark cannot sit exactly in the middle;
    take the upper position (64 px: y=3, the family layout) unless the
    lower one keeps the tile corners clearer. That only matters at 8 px,
    where the 7 px tall mark would otherwise put the TV's top corners in
    the tile's top corners.
    """
    h = max(1, round(size * art.size[1] / art.size[0]))
    mark = art.resize((size, h), Image.LANCZOS)
    tiles = []
    for y in sorted({(size - h) // 2, (size - h + 1) // 2}):
        tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        tile.alpha_composite(mark, (0, y))
        tiles.append(tile)
    return min(tiles, key=corner_alpha)   # min() keeps the first on a tie


def write(img, relpath, check):
    path = os.path.join(ROOT, relpath)
    if os.path.exists(path):
        old = Image.open(path)
        if (old.mode == img.mode and old.size == img.size
                and ImageChops.difference(old.convert("RGBA"), img).getbbox() is None):
            print(f"  unchanged   {relpath}")
            return False
    if check:
        print(f"  WOULD WRITE {relpath} {img.size}")
        return True
    img.save(path, optimize=True)
    print(f"  wrote       {relpath} {img.size} ({os.stat(path).st_size} bytes)")
    return True


def main():
    check = "--check" in sys.argv
    if not os.path.exists(LOGO):
        sys.exit(f"missing source: {LOGO}")
    try:
        subprocess.run(["rsvg-convert", "--version"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        sys.exit("rsvg-convert not found; run: brew install librsvg")

    print(f"building from {os.path.relpath(ART, ROOT)}")
    art = render(LOGO, RENDER_PX)
    changed = False
    for relpath, size in ICONS:
        changed |= write(build_icon(art, size), relpath, check)
    print("done:", "changes pending" if (check and changed) else
          ("updated" if changed else "everything already current"))


if __name__ == "__main__":
    main()
