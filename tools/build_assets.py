#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ZapTV.org
#
# This file is part of ClipTV. ClipTV is free software: you can redistribute
# it and/or modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. It is distributed WITHOUT
# ANY WARRANTY; see the GNU General Public License (LICENSE) for details.

"""Regenerate ClipTV's logo artwork from the SVG sources in artwork/.

    python3 tools/build_assets.py [--check]

Renders with rsvg-convert (brew install librsvg) so the source of truth
stays vector. macOS `qlmanage` can also render SVG but flattens the alpha
channel, which is useless here. The sources are the ZapTV family files,
copied in with their C2PA <metadata> block stripped and nothing else
changed:

    artwork/cliptv-logo.svg       the TV mark (viewBox 0 0 634 567);
                                  rsvg-convert ignores the clapper
                                  animation in its <style> and draws the
                                  static pose, clapper open
    artwork/cliptv-wordmark.svg   the CLIPTV wordmark: #111 ink over a cream
                                  #f8f5eb halo, so it reads on light and
                                  dark backgrounds alike

Outputs, all RGBA PNG with transparent corners (alpha 0, except that the
8 px icon's mark fills 7 of its 8 rows, so its bottom corners keep a faint
trace of edge alpha, under 5%):

    org.zaptv.cliptv/icon_64x64.png   launcher icon
    store_icons/icon-64x64.png        app store icon set, the same artwork:
    store_icons/icon-32x32.png        each size is downscaled from one large
    store_icons/icon-16x16.png        render of the SVG, never from the 64 px
    store_icons/icon-8x8.png          icon
    org.zaptv.cliptv/res/cliptv_lockup.png
                                      the About screen's logo (256x78)

Every icon size uses the ZapTV family layout: the whole SVG viewBox fitted
to the tile width and centred vertically, so at 64 px the mark is 64x57 at
y=3. The launcher draws the icon straight onto the desktop, and the mark's
own cream outline separates it from dark backgrounds, so no synthetic halo.

The lockup is composed the way the other ZapTV apps compose theirs: the
mark on the left at 2.2x the wordmark's viewBox height, a gap of 0.35x that
height, then the wordmark centred vertically on the mark. It is 256 px
wide (the About screen draws it 44 px tall, so a bigger source would only
cost decode memory); the height rounds up to whole pixels, with the spare
fraction of a row split evenly above and below the artwork. If its size
ever changes, give it a new file name (and update cliptv_settings.py):
LVGL caches image headers by path until reboot, so a same-named file
replaced in place is drawn at the old size.

infinity_12.png and infinity_16.png are hand-made UI glyphs, not logo
artwork; this tool leaves them alone.

--check reports what would change without writing anything.
"""

import math
import os
import re
import subprocess
import sys
import tempfile

from PIL import Image, ImageChops

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ART = os.path.join(ROOT, "artwork")
LOGO = os.path.join(ART, "cliptv-logo.svg")
WORDMARK = os.path.join(ART, "cliptv-wordmark.svg")

# Render big and downscale once: keeps the edges clean at every size.
RENDER_PX = 1200

ICONS = (
    ("org.zaptv.cliptv/icon_64x64.png", 64),
    ("store_icons/icon-64x64.png", 64),
    ("store_icons/icon-32x32.png", 32),
    ("store_icons/icon-16x16.png", 16),
    ("store_icons/icon-8x8.png", 8),
)

LOCKUP = "org.zaptv.cliptv/res/cliptv_lockup.png"
LOCKUP_W = 256
LOCKUP_SUPERSAMPLE = 8       # render at 8x, then one LANCZOS downscale
# Lockup proportions, in units of the wordmark's viewBox height.
LOCKUP_MARK_H = 2.2
LOCKUP_GAP = 0.35


def render(svg, width, height=None):
    """SVG file -> RGBA image at the requested width (and height; without
    one, the height follows from the viewBox)."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        path = tmp.name
    size = ["-w", str(width)] + (["-h", str(height)] if height else [])
    try:
        subprocess.run(["rsvg-convert"] + size + ["-o", path, svg],
                       check=True, capture_output=True)
        img = Image.open(path).convert("RGBA").copy()
    finally:
        os.unlink(path)
    if height and img.size != (width, height):
        sys.exit(f"rsvg-convert gave {img.size}, wanted {(width, height)}")
    return img


def read_svg(path):
    """(viewBox as four floats, markup inside the root <svg>) of a file."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    # Defensive: the copies in artwork/ already have it removed.
    text = re.sub(r"<metadata>.*?</metadata>", "", text, flags=re.S)
    vb = [float(v) for v in re.search(r'viewBox="([^"]+)"', text).group(1).split()]
    start = text.index(">", text.index("<svg")) + 1
    return vb, text[start:text.rindex("</svg>")]


def fmt(v):
    return ("%.4f" % v).rstrip("0").rstrip(".")


def build_lockup():
    """The mark and the wordmark side by side, LOCKUP_W wide."""
    lvb, lbody = read_svg(LOGO)
    wvb, wbody = read_svg(WORDMARK)
    ww, wh = wvb[2], wvb[3]
    mh = LOCKUP_MARK_H * wh
    mw = mh * lvb[2] / lvb[3]
    gap = LOCKUP_GAP * wh
    width, height = mw + gap + ww, mh
    # Round the pixel height up and pad the viewBox evenly above and below
    # to that exact aspect, so the artwork is never stretched.
    rows = math.ceil(LOCKUP_W * height / width - 1e-9)
    pad = (width * rows / LOCKUP_W - height) / 2

    def nest(x, y, w, h, vb, body):
        return ('<svg x="%s" y="%s" width="%s" height="%s" viewBox="%s">%s</svg>'
                % (fmt(x), fmt(y), fmt(w), fmt(h), " ".join(fmt(v) for v in vb), body))

    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 %s %s %s">'
           % (fmt(-pad), fmt(width), fmt(height + 2 * pad))
           + nest(0, 0, mw, mh, lvb, lbody)
           + nest(mw + gap, (mh - wh) / 2, ww, wh, wvb, wbody)
           + "</svg>")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "lockup.svg")
        with open(path, "w", encoding="utf-8") as f:
            f.write(svg)
        big = render(path, LOCKUP_W * LOCKUP_SUPERSAMPLE, rows * LOCKUP_SUPERSAMPLE)
    return big.resize((LOCKUP_W, rows), Image.LANCZOS)


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
                and ImageChops.difference(old.convert("RGBA"), img).getbbox(alpha_only=False) is None):
            print(f"  unchanged   {relpath}")
            return False
    if check:
        print(f"  WOULD WRITE {relpath} {img.size}")
        return True
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path, optimize=True)
    print(f"  wrote       {relpath} {img.size} ({os.stat(path).st_size} bytes)")
    return True


def main():
    check = "--check" in sys.argv
    for source in (LOGO, WORDMARK):
        if not os.path.exists(source):
            sys.exit(f"missing source: {source}")
    try:
        subprocess.run(["rsvg-convert", "--version"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        sys.exit("rsvg-convert not found; run: brew install librsvg")

    print(f"building from {os.path.relpath(ART, ROOT)}")
    art = render(LOGO, RENDER_PX)
    changed = False
    for relpath, size in ICONS:
        changed |= write(build_icon(art, size), relpath, check)
    changed |= write(build_lockup(), LOCKUP, check)
    print("done:", "changes pending" if (check and changed) else
          ("updated" if changed else "everything already current"))


if __name__ == "__main__":
    main()
