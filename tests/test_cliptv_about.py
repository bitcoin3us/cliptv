# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ZapTV.org
#
# This file is part of ClipTV. ClipTV is free software: you can redistribute
# it and/or modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. It is distributed WITHOUT
# ANY WARRANTY; see the GNU General Public License (LICENSE) for details.

"""The About screen follows the layout every ZapTV app shares.

Top to bottom: the logo lockup, LOGO_H (44 px) tall; three facts, each one
centred "Name: value" line with the name dimmed: the app version (read from
MANIFEST.JSON at runtime), the MicroPythonOS version and the hardware; the
site; and a footer with the credit, the legal notice and the third-party
line. It must fit a 320x240 screen without scrolling, even with the longest
board name, without any text running under the back button in the
bottom-right corner, and in the OS light or dark theme. What About used to
explain (buttons, playlists, wiring, clips) is now on Help, unchanged.

This is a graphical test for the MicroPythonOS desktop build. From a
MicroPythonOS checkout, with the app linked in:

    ln -s /path/to/org.zaptv.cliptv internal_filesystem/apps/
    ./scripts/test_runner.py /path/to/tests/test_cliptv_about.py
"""
import io
import json
import sys
import unittest

import lvgl as lv
from mpos import (
    AppearanceManager,
    DisplayMetrics,
    FontManager,
    Intent,
    SharedPreferences,
)
from mpos.activity_navigator import ActivityNavigator
from mpos.build_info import BuildInfo
from mpos.device_info import DeviceInfo
from mpos.ui.testing import wait_for_render
from mpos.ui.view import screen_stack

FULLNAME = "org.zaptv.cliptv"
APP = "apps/" + FULLNAME
if APP not in sys.path:
    sys.path.insert(0, APP)
import cliptv_common
import cliptv_settings

LONG_BOARD = "waveshare_esp32_s3_touch_lcd_3_5"
LONG_BOARD_PRETTY = "Waveshare ESP32 S3 Touch LCD 3 5"
SITE = "www.ZapTV.org"
CREDIT = "A fully open-source app\nby Richard Nakamoto"
LEGAL = "© 2026 ZapTV.org. Free software:\nGNU GPL v3 or later, no warranty."
THIRD_PARTY = "Sample clips: see clips/README.md."
SUBTITLE = "Version, credits and licence"
HELP_SUBTITLE = "Buttons, playlists, wiring and clips"
LOGO_H = 44
BACK = 50                                     # the corner square
THEME_PREFS = "com.micropythonos.settings"   # where MicroPythonOS keeps it
# Frames to let a screen settle: MicroPythonOS slides each new screen in
# over 500 ms, and wait_for_render advances 16 ms a frame, so 40 frames
# outlast the slide (coordinates taken mid-slide are shifted).
SETTLE = 40


def _help_text():
    """What the About page said before 0.12.3's family layout, word for
    word: (text, is_header) in order. Help must keep it unchanged."""
    c = cliptv_common
    return [
        ("%s %s" % (c.APP_NAME, c.APP_VERSION), True),
        ("A soundboard for audio and video clips. Assign clips from the "
         "SD card to buttons, then tap a button to play its clip. "
         "Long-press any button to edit it.", False),
        ("Buttons can also play a playlist of hand-picked clips, in "
         "order or shuffled, or random clips (audio, video, or anything). Random "
         "picks come from the built-in sample clips and %s and its "
         "subfolders. With Infinite loop enabled, a fixed clip repeats "
         "forever and a random button keeps rolling a new clip each time "
         "one finishes; buttons doing this show an infinity icon. Tap "
         "the button again (or Stop) to end it."
         % (c.clips_base_dir() or c.CLIPS_DIR), False),
        ("Audio output", True),
        ("Audio plays through a PCM5102A I2S DAC. Default wiring on the "
         "Waveshare ESP32-S3-Touch-LCD-2 header:", False),
        ("BCK -> GPIO%d" % c.DEFAULT_PIN_BCK, False),
        ("LRCK (LCK) -> GPIO%d" % c.DEFAULT_PIN_LRCK, False),
        ("DIN -> GPIO%d" % c.DEFAULT_PIN_DIN, False),
        ("VIN -> 3V3, GND -> GND, SCK -> GND", False),
        ("Pins can be changed in Settings.", False),
        ("Clips", True),
        ("ClipTV ships with a few sample sounds (a 1990s telephone "
         "ring, a dog, a pig and a cat) so there is something to play "
         "out of the box. Add your own on a FAT32 SD card, e.g. in %s. "
         "Audio: WAV (16-bit PCM). Video: MJPEG (.mjpeg, shown at its "
         "encoded size) or raw RGB565 (.rgb565, scaled to fit) named "
         "like clip_160x120_12fps.rgb565, with an optional companion "
         ".wav of the same name for sound. See the README for ffmpeg "
         "conversion commands." % c.CLIPS_DIR, False),
        ("Licence", True),
        ("ClipTV is free software under the GNU General Public License, "
         "version 3 or (at your option) any later version, with no "
         "warranty. The bundled sample clips carry their own licences, "
         "listed in clips/README.md.", False),
    ]


def _top():
    return screen_stack[-1][0]


def _manifest_version():
    with open(APP + "/MANIFEST.JSON") as handle:
        return json.load(handle)["version"]


def _coords(obj):
    area = lv.area_t()
    obj.get_coords(area)
    return area.x1, area.y1, area.x2, area.y2


def _overlap(a, b):
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def _floating(screen):
    for i in range(screen.get_child_count()):
        if screen.get_child(i).has_flag(lv.obj.FLAG.FLOATING):
            return screen.get_child(i)
    return None


def _content(screen):
    """The About screen top to bottom, as tuples: ("image",),
    ("label", text) or ("fact", name, value). Anything else comes back as
    ("recolour", text) or ("other", type name), so a layout change fails
    the comparison readably. The floating back button is returned on its
    own."""
    items, back = [], None
    for i in range(screen.get_child_count()):
        child = screen.get_child(i)
        if child.has_flag(lv.obj.FLAG.FLOATING):
            back = child
        elif isinstance(child, lv.image):
            items.append(("image",))
        elif isinstance(child, lv.label) and child.get_recolor():
            items.append(_fact(child) or ("recolour", child.get_text()))
        elif isinstance(child, lv.label):
            items.append(("label", child.get_text()))
        else:
            items.append(("other", type(child).__name__))
    return items, back


def _fact(label):
    """("fact", name, value) from a fact label's "#rrggbb Name:# value"
    text, or None if it is not one."""
    text = label.get_text()
    if not text.startswith("#") or ":# " not in text:
        return None
    colour, rest = text[1:].split(" ", 1)
    if len(colour) != 6 or any(c not in "0123456789abcdefABCDEF"
                               for c in colour):
        return None
    name, value = rest.split(":# ", 1)
    return ("fact", name, value)


def _dim(label):
    """The colour a fact label's recolour command gives its name."""
    return label.get_text()[1:7].lower()


def _mix60(text_rgb, bg_rgb):
    """text at 60% opacity over bg, as rrggbb."""
    return "".join("%02x" % ((t * 153 + b * 102 + 127) // 255)
                   for t, b in zip(text_rgb, bg_rgb))


def _drawn(obj, out):
    """Every label and image under obj, back button excluded."""
    for i in range(obj.get_child_count()):
        child = obj.get_child(i)
        if child.has_flag(lv.obj.FLAG.FLOATING):
            continue
        if isinstance(child, (lv.label, lv.image)):
            out.append(child)
        _drawn(child, out)
    return out


def _rgb(color):
    return color.red, color.green, color.blue


def _set_theme(light):
    AppearanceManager.set_light_mode(light, SharedPreferences(THEME_PREFS))
    wait_for_render(5)


class TestClipTVAbout(unittest.TestCase):

    def setUp(self):
        self.board = DeviceInfo.get_hardware_id()
        self.release = BuildInfo.version.release
        self.patched = {}

    def tearDown(self):
        self._close()
        DeviceInfo.set_hardware_id(self.board)
        BuildInfo.version.release = self.release
        module = cliptv_settings.__dict__
        for name, original in self.patched.items():
            if original is None:
                module.pop(name, None)
            else:
                module[name] = original
        _set_theme(True)

    def _patch(self, name, value):
        """Replace a global the About code looks up (open shadows the
        builtin) until tearDown."""
        module = cliptv_settings.__dict__
        if name not in self.patched:
            self.patched[name] = module.get(name)
        module[name] = value

    def _close(self):
        ours = tuple(getattr(cliptv_settings, name)
                     for name in ("AboutActivity", "HelpActivity",
                                  "CliptvSettings")
                     if hasattr(cliptv_settings, name))
        while type(_top()) in ours:
            _top().finish()
            wait_for_render(10)

    def _open_settings(self):
        intent = Intent(activity_class=cliptv_settings.CliptvSettings)
        intent.app_fullname = FULLNAME
        ActivityNavigator.startActivity(intent)
        wait_for_render(SETTLE)
        settings = _top()
        self.assertTrue(isinstance(settings, cliptv_settings.CliptvSettings))
        return settings

    def _tap_row(self, settings, title, activity_class):
        """Tap a settings row, as a user would, and return the screen."""
        rows = [s for s in settings.settings if s.get("title") == title]
        self.assertEqual(len(rows), 1, f"no {title!r} row in settings")
        rows[0]["cont"].send_event(lv.EVENT.CLICKED, None)
        wait_for_render(SETTLE)
        self.assertTrue(isinstance(_top(), activity_class))
        screen = lv.screen_active()
        screen.update_layout()
        return screen

    def _open_about(self):
        return self._tap_row(self._open_settings(), "About",
                             cliptv_settings.AboutActivity)

    def test_settings_rows_end_with_help_then_about(self):
        settings = self._open_settings()
        about, help_row = settings.settings[-1], settings.settings[-2]
        self.assertEqual(about["title"], "About")
        self.assertEqual(about["placeholder"], SUBTITLE)
        self.assertEqual(about["value_label"].get_text(), SUBTITLE)
        self.assertEqual(help_row["title"], "Help")
        self.assertEqual(help_row["placeholder"], HELP_SUBTITLE)
        self.assertEqual(help_row["value_label"].get_text(), HELP_SUBTITLE)

    def test_content_and_strings_in_order(self):
        DeviceInfo.set_hardware_id(LONG_BOARD)
        items, back = _content(self._open_about())
        self.assertEqual(items, [
            ("image",),
            ("fact", "ClipTV", _manifest_version()),
            ("fact", "MicroPythonOS", BuildInfo.version.release),
            ("fact", "Hardware", LONG_BOARD_PRETTY),
            ("label", SITE),
            ("label", CREDIT),
            ("label", LEGAL),
            ("label", THIRD_PARTY),
        ])
        self.assertTrue(back is not None, "no floating back button")

    def test_styles(self):
        screen = self._open_about()
        self.assertEqual(screen.get_style_pad_top(0),
                         DisplayMetrics.pct_of_width(2))
        self.assertEqual(screen.get_style_pad_row(0), 1)   # the family value
        logo = screen.get_child(0)
        x1, y1, x2, y2 = _coords(logo)
        self.assertEqual(y2 - y1 + 1, LOGO_H)
        self.assertTrue(abs((x1 + x2) - (DisplayMetrics.width() - 1)) <= 2,
                        f"logo not centred: {(x1, x2)}")
        fonts = {size: FontManager.getFont(size=size).line_height
                 for size in (12, 14, 16)}
        for i in (1, 2, 3):
            # One centred, wrapping line across the content width, at the
            # screen's full text strength; only the recoloured name is dim
            # (test_colours checks its colour).
            fact = screen.get_child(i)
            self.assertTrue(isinstance(fact, lv.label) and fact.get_recolor())
            self.assertEqual(fact.get_style_text_font(0).line_height,
                             fonts[14])
            self.assertEqual(fact.get_style_text_align(0),
                             lv.TEXT_ALIGN.CENTER)
            self.assertEqual(fact.get_long_mode(), lv.label.LONG_MODE.WRAP)
            self.assertEqual(fact.get_width(), screen.get_content_width())
            self.assertEqual(fact.get_style_text_line_space(0),
                             cliptv_settings.ABOUT_LINE_SPACE)
            self.assertEqual(fact.get_style_text_opa(0), lv.OPA.COVER)
            self.assertEqual(fact.get_style_opa(0), lv.OPA.COVER)
        self.assertEqual(
            screen.get_child(4).get_style_text_font(0).line_height, fonts[16])
        for i in (5, 6, 7):
            label = screen.get_child(i)
            self.assertEqual(label.get_style_text_font(0).line_height,
                             fonts[12])
            self.assertEqual(label.get_style_text_opa(0), lv.OPA._60)
            self.assertEqual(label.get_style_text_align(0),
                             lv.TEXT_ALIGN.CENTER)
            self.assertEqual(label.get_long_mode(), lv.label.LONG_MODE.WRAP)
            self.assertEqual(label.get_width(), DisplayMetrics.width() - 100)
            # Broken by hand: LVGL wraps none of the footer's lines.
            lines = label.get_text().count("\n") + 1
            pitch = fonts[12] + label.get_style_text_line_space(0)
            self.assertEqual(label.get_height(),
                             fonts[12] + (lines - 1) * pitch)
        self.assertEqual(screen.get_scrollbar_mode(), lv.SCROLLBAR_MODE.OFF)

    def test_version_is_read_from_the_manifest_at_runtime(self):
        tried = []

        def fake_open(path, *args):
            tried.append(path)
            if path.endswith("/MANIFEST.JSON"):
                return io.StringIO('{"version": "9.8.7-test"}')
            raise OSError(2)

        self._patch("open", fake_open)
        items, _ = _content(self._open_about())
        self.assertEqual(items[1], ("fact", "ClipTV", "9.8.7-test"))
        self.assertEqual(tried[0], f"/apps/{FULLNAME}/MANIFEST.JSON")

    def test_a_hash_in_a_fact_shows_it_plain(self):
        # LVGL measures its "##" escape as a command, so a fact with a '#'
        # in it is shown plain rather than mis-centred: no dimmed name.
        BuildInfo.version.release = "1.0#rc2"
        screen = self._open_about()
        fact = screen.get_child(2)
        self.assertFalse(fact.get_recolor())
        self.assertEqual(fact.get_text(), "MicroPythonOS: 1.0#rc2")
        self.assertTrue(screen.get_child(1).get_recolor())  # the others dim
        self.assertTrue(screen.get_child(3).get_recolor())

    def test_facts_fall_back_to_unknown(self):
        def no_file(path, *args):
            raise OSError(2)

        self._patch("open", no_file)
        # None, and the placeholder DeviceInfo keeps when no board
        # registered itself, both read "unknown".
        for board in (None, "missing-hardware-info"):
            DeviceInfo.set_hardware_id(board)
            BuildInfo.version.release = None
            items, _ = _content(self._open_about())
            self.assertEqual(items[1:4], [("fact", "ClipTV", "unknown"),
                                          ("fact", "MicroPythonOS", "unknown"),
                                          ("fact", "Hardware", "unknown")])
            self._close()

    def test_logo_falls_back_to_the_app_name(self):
        for path in (None, "M:" + APP + "/MANIFEST.JSON"):   # missing, not a PNG
            self._patch("resolve_res", lambda fullname, name, p=path: p)
            items, _ = _content(self._open_about())
            self.assertEqual(items[0], ("label", "ClipTV"))
            self.assertEqual(items[1][:2], ("fact", "ClipTV"))
            self._close()

    def test_fits_320x240_with_the_longest_board_name(self):
        self.assertEqual((DisplayMetrics.width(), DisplayMetrics.height()),
                         (320, 240))
        DeviceInfo.set_hardware_id(LONG_BOARD)
        screen = self._open_about()
        overflow = screen.get_scroll_bottom()
        self.assertTrue(overflow <= 0, f"About scrolls by {overflow} px")
        # The worst case: the board name takes two lines.
        hardware = screen.get_child(3)
        self.assertEqual(_fact(hardware),
                         ("fact", "Hardware", LONG_BOARD_PRETTY))
        self.assertTrue(hardware.get_height() > 20, "board name did not wrap")

    def test_back_button_is_bottom_right_and_clear_of_content(self):
        DeviceInfo.set_hardware_id(LONG_BOARD)
        w, h = DisplayMetrics.width(), DisplayMetrics.height()
        corner = (w - BACK, h - BACK, w - 1, h - 1)
        screen = self._open_about()
        _, back = _content(screen)
        self.assertEqual(_coords(back), corner)
        # ClipTV's own look, a round button with a left arrow, centred in
        # the square; and the whole square answers a tap.
        button = back.get_child(0)
        self.assertTrue(isinstance(button, lv.button))
        self.assertEqual(button.get_style_radius(0), lv.RADIUS_CIRCLE)
        self.assertEqual(button.get_style_bg_opa(0), lv.OPA._80)
        self.assertEqual(button.get_child(0).get_text(), lv.SYMBOL.LEFT)
        x1, y1, x2, y2 = _coords(button)
        self.assertEqual(x2 - x1 + 1, max(32, DisplayMetrics.pct_of_height(15)))
        self.assertEqual((x1 + x2, y1 + y2),
                         (corner[0] + corner[2], corner[1] + corner[3]))
        for x, y in ((corner[0], corner[1]), (corner[2], corner[3])):
            point = lv.point_t()
            point.x, point.y = x, y
            self.assertTrue(button.hit_test(point), f"no tap at {(x, y)}")
        for obj in _drawn(screen, []):
            text = (obj.get_text() if isinstance(obj, lv.label)
                    else type(obj).__name__)
            self.assertFalse(_overlap(_coords(obj), corner),
                             f"{text!r} runs under the back button")
        button.send_event(lv.EVENT.CLICKED, None)
        wait_for_render(SETTLE)
        self.assertTrue(isinstance(_top(), cliptv_settings.CliptvSettings))

    def test_colours_come_from_the_os_theme(self):
        seen = {}
        for light in (True, False):
            _set_theme(light)
            screen = self._open_about()
            reference = lv.obj()                  # a plain themed screen
            ref_label = lv.label(reference)
            expected = (_rgb(reference.get_style_bg_color(0)),
                        _rgb(ref_label.get_style_text_color(0)))
            reference.delete()
            self.assertEqual(_rgb(screen.get_style_bg_color(0)), expected[0])
            self.assertEqual(screen.get_style_bg_opa(0), lv.OPA.COVER)
            for label in _drawn(screen, []):
                if isinstance(label, lv.label):
                    self.assertEqual(_rgb(label.get_style_text_color(0)),
                                     expected[1], label.get_text())
                if isinstance(label, lv.label) and label.get_recolor():
                    # The name: the theme's text at 60% over its background.
                    self.assertEqual(_dim(label),
                                     _mix60(expected[1], expected[0]),
                                     label.get_text())
            facts = [label for label in _drawn(screen, [])
                     if isinstance(label, lv.label) and label.get_recolor()]
            self.assertEqual(len(facts), 3)
            seen[light] = expected
            self._close()
        self.assertTrue(seen[True] != seen[False], "theme did not switch")

    def test_the_old_about_text_is_under_help(self):
        settings = self._open_settings()
        screen = self._tap_row(settings, "Help",
                               getattr(cliptv_settings, "HelpActivity", None))
        labels = [screen.get_child(i) for i in range(screen.get_child_count())
                  if isinstance(screen.get_child(i), lv.label)]
        self.assertEqual([label.get_text() for label in labels],
                         [text for text, _ in _help_text()])
        primary = _rgb(lv.theme_get_color_primary(None))
        for label, (text, header) in zip(labels, _help_text()):
            # One focus stop per paragraph, or a keypad never reaches the end.
            self.assertTrue(label.get_group() is not None, text)
            size = 16 if header else 12
            self.assertEqual(label.get_style_text_font(0).line_height,
                             FontManager.getFont(size=size).line_height, text)
            if header:
                self.assertEqual(_rgb(label.get_style_text_color(0)), primary)
        # Help keeps its back button where Settings and About have it.
        w, h = DisplayMetrics.width(), DisplayMetrics.height()
        self.assertEqual(_coords(_floating(screen)),
                         (w - 50, h - 50, w - 1, h - 1))

    def test_settings_back_button_is_in_the_same_corner(self):
        self._open_settings()
        screen = lv.screen_active()
        screen.update_layout()
        backs = [screen.get_child(i) for i in range(screen.get_child_count())
                 if screen.get_child(i).has_flag(lv.obj.FLAG.FLOATING)]
        self.assertEqual(len(backs), 1)
        w, h = DisplayMetrics.width(), DisplayMetrics.height()
        self.assertEqual(_coords(backs[0]), (w - 50, h - 50, w - 1, h - 1))


if __name__ == "__main__":
    unittest.main()
