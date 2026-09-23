# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ZapTV.org
#
# This file is part of ClipTV. ClipTV is free software: you can redistribute
# it and/or modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. It is distributed WITHOUT
# ANY WARRANTY; see the GNU General Public License (LICENSE) for details.

import logging
import os
import sys

import lvgl as lv

from mpos import (
    Activity,
    AudioManager,
    DisplayMetrics,
    InputActivity,
    Intent,
    SettingsActivity,
    add_focus_border,
    add_focus_highlight,
)

import cliptv_common
import cliptv_grid
from video_player import VideoPlayerActivity

logger = logging.getLogger(__name__)


def pick_start_dir():
    if cliptv_common.path_exists(cliptv_common.CLIPS_DIR):
        return cliptv_common.CLIPS_DIR + "/"
    if cliptv_common.path_exists("/sdcard"):
        return "/sdcard/"
    bundled = cliptv_common.bundled_clips_dir()
    if bundled:
        return bundled + "/"
    return "data/"


class ButtonEditorActivity(Activity):
    """Edit one soundboard button: what it plays, name, color and looping.

    Edits are buffered in memory; Save persists them and closes the
    editor, the back button discards them.
    """

    def onCreate(self):
        extras = self.getIntent().extras or {}
        self._screen_index = extras.get("screen_index", 0)
        self._button_index = extras.get("button_index", 0)
        self._prefs = extras.get("prefs") or cliptv_common.get_prefs()
        self._player = None
        self._pending = cliptv_common.get_button_config(
            self._prefs, self._screen_index, self._button_index
        )

        screen = lv.obj()
        screen.set_flex_flow(lv.FLEX_FLOW.COLUMN)
        screen.set_style_pad_all(DisplayMetrics.pct_of_width(2), lv.PART.MAIN)
        screen.set_style_border_width(0, lv.PART.MAIN)

        title = lv.label(screen)
        title.set_text("Screen %d - Button %d" % (self._screen_index + 1, self._button_index + 1))
        title.set_style_text_font(lv.font_montserrat_16, lv.PART.MAIN)

        self._plays_button, self._plays_label = self._add_row(screen, self._plays_clicked)
        self._name_button, self._name_label = self._add_row(screen, self._name_clicked)
        self._color_button, self._color_label = self._add_row(screen, self._color_clicked)

        loop_row = lv.obj(screen)
        loop_row.set_width(lv.pct(100))
        loop_row.set_height(lv.SIZE_CONTENT)
        loop_row.set_flex_flow(lv.FLEX_FLOW.ROW)
        loop_row.set_style_flex_cross_place(lv.FLEX_ALIGN.CENTER, lv.PART.MAIN)
        loop_row.set_style_pad_all(0, lv.PART.MAIN)
        loop_row.set_style_pad_gap(6, lv.PART.MAIN)
        loop_row.set_style_border_width(0, lv.PART.MAIN)
        loop_row.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)

        self._loop_checkbox = lv.checkbox(loop_row)
        self._loop_checkbox.set_text("Infinite loop")
        self._loop_checkbox.add_event_cb(self._loop_changed, lv.EVENT.VALUE_CHANGED, None)
        add_focus_border(self._loop_checkbox)

        cliptv_grid.add_infinity_badge(
            loop_row, self._loop_checkbox.get_style_text_color(lv.PART.MAIN),
            small=True,
        )

        # Only shown for playlist buttons (hidden/shown in _refresh).
        self._shuffle_checkbox = lv.checkbox(loop_row)
        self._shuffle_checkbox.set_text("Shuffle " + lv.SYMBOL.SHUFFLE)
        self._shuffle_checkbox.add_event_cb(self._shuffle_changed, lv.EVENT.VALUE_CHANGED, None)
        add_focus_border(self._shuffle_checkbox)

        # Right-aligned so the floating back button keeps the bottom-left
        # corner to itself.
        action_row = lv.obj(screen)
        action_row.set_width(lv.pct(100))
        action_row.set_height(lv.SIZE_CONTENT)
        action_row.set_flex_flow(lv.FLEX_FLOW.ROW)
        action_row.set_style_flex_main_place(lv.FLEX_ALIGN.END, lv.PART.MAIN)
        action_row.set_style_flex_cross_place(lv.FLEX_ALIGN.CENTER, lv.PART.MAIN)
        action_row.set_style_pad_gap(6, lv.PART.MAIN)
        action_row.set_style_border_width(0, lv.PART.MAIN)
        action_row.set_style_pad_all(0, lv.PART.MAIN)

        clear_button = lv.button(action_row)
        clear_size = max(30, DisplayMetrics.pct_of_height(13))
        clear_button.set_size(clear_size, clear_size)
        clear_label = lv.label(clear_button)
        clear_label.set_text(lv.SYMBOL.TRASH)
        clear_label.center()
        clear_button.add_event_cb(self._clear_clicked, lv.EVENT.CLICKED, None)
        add_focus_border(clear_button)

        test_button = lv.button(action_row)
        test_button.set_size(lv.pct(28), lv.SIZE_CONTENT)
        test_label = lv.label(test_button)
        test_label.set_text(lv.SYMBOL.PLAY + " Test")
        test_label.center()
        test_button.add_event_cb(self._test_clicked, lv.EVENT.CLICKED, None)
        add_focus_border(test_button)

        save_button = lv.button(action_row)
        save_button.set_size(lv.pct(35), lv.SIZE_CONTENT)
        save_label = lv.label(save_button)
        save_label.set_text(lv.SYMBOL.SAVE + " Save")
        save_label.center()
        save_button.add_event_cb(self._save_clicked, lv.EVENT.CLICKED, None)
        add_focus_border(save_button)

        cliptv_grid.add_floating_back_button(screen)

        self._refresh()
        self.setContentView(screen)

        # A freshly tapped "+" button has nothing assigned yet: go straight
        # to the clip-or-action choice instead of making the user find it.
        if not cliptv_common.button_is_assigned(self._pending):
            self._plays_clicked()

    def _add_row(self, parent, callback):
        button = lv.button(parent)
        button.set_width(lv.pct(100))
        button.set_height(lv.SIZE_CONTENT)
        label = lv.label(button)
        label.set_width(lv.pct(100))
        label.set_long_mode(lv.label.LONG_MODE.DOTS)
        label.center()
        button.add_event_cb(callback, lv.EVENT.CLICKED, None)
        add_focus_border(button)
        return button, label

    def _config(self):
        return self._pending

    def _save(self, config):
        self._pending = config
        self._refresh()

    def _save_clicked(self, event=None):
        cliptv_common.save_button_config(
            self._prefs, self._screen_index, self._button_index, self._pending
        )
        self.finish()

    def _refresh(self):
        config = self._config()

        clip = config.get("clip")
        action = config.get("action")
        playlist = config.get("playlist")
        if clip:
            plays = clip
        elif playlist:
            plays = "Playlist (%d clips%s)" % (
                len(playlist), ", shuffled" if config.get("shuffle") else ""
            )
        elif action:
            plays = cliptv_common.ACTION_LABELS.get(action, action)
        else:
            plays = "(tap to assign)"
        self._plays_label.set_text("Plays: %s" % plays)

        if playlist:
            self._shuffle_checkbox.remove_flag(lv.obj.FLAG.HIDDEN)
        else:
            self._shuffle_checkbox.add_flag(lv.obj.FLAG.HIDDEN)
        if config.get("shuffle"):
            self._shuffle_checkbox.add_state(lv.STATE.CHECKED)
        else:
            self._shuffle_checkbox.remove_state(lv.STATE.CHECKED)

        name = config.get("name") or "(not set)"
        self._name_label.set_text("Name: %s" % name)

        color_hex = config.get("color") or cliptv_common.DEFAULT_COLOR
        color_name = color_hex
        for label, value in cliptv_common.COLOR_OPTIONS:
            if value == color_hex:
                color_name = label
                break
        self._color_label.set_text("Color: %s" % color_name)
        color = cliptv_common.color_int(color_hex)
        self._color_button.set_style_bg_color(lv.color_hex(color), lv.PART.MAIN)
        text_color = 0xFFFFFF if cliptv_common.is_dark_color(color) else 0x000000
        self._color_label.set_style_text_color(lv.color_hex(text_color), lv.PART.MAIN)

        if config.get("loop"):
            self._loop_checkbox.add_state(lv.STATE.CHECKED)
        else:
            self._loop_checkbox.remove_state(lv.STATE.CHECKED)

    def _name_clicked(self, event=None):
        intent = Intent(activity_class=InputActivity)
        intent.putExtra("setting", {"title": "Button name", "placeholder": "e.g. Airhorn"})
        intent.putExtra("value", self._config().get("name") or "")
        self.startActivityForResult(intent, self._name_result)

    def _name_result(self, result):
        if not result or not result.get("result_code"):
            return
        config = self._config()
        config["name"] = result.get("data", {}).get("value") or ""
        self._save(config)

    def _color_clicked(self, event=None):
        intent = Intent(activity_class=InputActivity)
        intent.putExtra("setting", {
            "title": "Button color",
            "ui": "dropdown",
            "ui_options": cliptv_common.COLOR_OPTIONS,
        })
        intent.putExtra("value", self._config().get("color") or cliptv_common.DEFAULT_COLOR)
        self.startActivityForResult(intent, self._color_result)

    def _color_result(self, result):
        if not result or not result.get("result_code"):
            return
        new_color = result.get("data", {}).get("value")
        if not new_color:
            return
        config = self._config()
        config["color"] = new_color
        self._save(config)

    def _plays_clicked(self, event=None):
        config = self._config()
        if config.get("clip"):
            current = "clip"
        elif config.get("playlist"):
            current = "playlist"
        else:
            current = config.get("action") or "clip"
        options = [
            ("A specific clip...", "clip"),
            ("A playlist of clips...", "playlist"),
        ]
        options += [
            (cliptv_common.ACTION_LABELS[action], action)
            for action in (
                cliptv_common.ACTION_RANDOM_AUDIO,
                cliptv_common.ACTION_RANDOM_VIDEO,
                cliptv_common.ACTION_RANDOM_ANY,
            )
        ]
        # No "note" here: with four options it would push Save/Cancel below
        # the 240px screen. The clips folder is documented on the About page.
        intent = Intent(activity_class=InputActivity)
        intent.putExtra("setting", {
            "title": "This button plays",
            "ui": "radiobuttons",
            "ui_options": options,
        })
        intent.putExtra("value", current)
        self.startActivityForResult(intent, self._plays_result)

    def _plays_result(self, result):
        if not result or not result.get("result_code"):
            return
        choice = result.get("data", {}).get("value")
        if not choice:
            return
        if choice == "clip":
            # Chain straight into the file picker; the existing clip (if
            # any) is kept when the picker is cancelled.
            self._pick_clip_file()
            return
        if choice == "playlist":
            self._open_playlist_editor()
            return
        config = self._config()
        config["action"] = choice
        config.pop("clip", None)
        config.pop("playlist", None)
        config.pop("shuffle", None)
        if not config.get("color"):
            config["color"] = cliptv_common.DEFAULT_COLOR
        self._save(config)

    def _open_playlist_editor(self):
        intent = Intent(activity_class=PlaylistEditorActivity)
        intent.putExtra("playlist", list(self._config().get("playlist") or []))
        self.startActivityForResult(intent, self._playlist_result)

    def _playlist_result(self, result):
        if not result or not result.get("result_code"):
            return
        items = result.get("data", {}).get("playlist") or []
        config = self._config()
        if items:
            config["playlist"] = items
            config.pop("clip", None)
            config.pop("action", None)
            if not config.get("color"):
                config["color"] = cliptv_common.DEFAULT_COLOR
        else:
            config.pop("playlist", None)
            config.pop("shuffle", None)
        self._save(config)

    def _pick_clip_file(self):
        patterns = list(cliptv_common.AUDIO_EXTENSIONS + cliptv_common.VIDEO_EXTENSIONS)
        intent = Intent(
            action="pick_file",
            extras={"start_dir": pick_start_dir(), "path_pattern": patterns},
        )
        self.startActivityForResult(intent, self._clip_result)

    def _clip_result(self, result):
        if not result or not result.get("result_code"):
            return
        paths = result.get("data", {}).get("paths", [])
        clip = self._find_first_clip(paths)
        if clip:
            config = self._config()
            config["clip"] = clip
            config.pop("action", None)
            config.pop("playlist", None)
            config.pop("shuffle", None)
            if not config.get("color"):
                config["color"] = cliptv_common.DEFAULT_COLOR
            self._save(config)

    @staticmethod
    def _is_clip(path):
        return cliptv_common.is_audio_clip(path) or cliptv_common.is_video_clip(path)

    def _find_first_clip(self, paths):
        """Return the first picked clip; a picked folder yields its first clip."""
        for path in paths:
            if path.endswith("/"):
                try:
                    # FAT32 (SD card) rejects directory paths ending with '/'.
                    items = os.listdir(path.rstrip("/") or "/")
                    items.sort()
                    for item in items:
                        if self._is_clip(item):
                            return path + item
                except OSError:
                    pass
            elif self._is_clip(path):
                return path
        return None

    def _loop_changed(self, event=None):
        if self._loop_checkbox.get_state() & lv.STATE.CHECKED:
            self._pending["loop"] = True
        else:
            self._pending.pop("loop", None)

    def _shuffle_changed(self, event=None):
        if self._shuffle_checkbox.get_state() & lv.STATE.CHECKED:
            self._pending["shuffle"] = True
        else:
            self._pending.pop("shuffle", None)

    def _test_clicked(self, event=None):
        # Test plays a single roll, without looping.
        clip = cliptv_common.resolve_button_clip(self._config())
        if not clip:
            if self._config().get("action"):
                self._plays_label.set_text("Plays: no matching clips found")
            return
        if cliptv_common.is_video_clip(clip):
            intent = Intent(activity_class=VideoPlayerActivity)
            intent.putExtra("clip", clip)
            self.startActivity(intent)
            return
        try:
            output = cliptv_common.ensure_audio_output(self._prefs)
            self._player = cliptv_common.play_audio_clip(clip, output)
        except Exception as e:
            logger.error("Test playback failed: %s", e)

    def _clear_clicked(self, event=None):
        AudioManager.stop()
        self._save({})


class PlaylistEditorActivity(Activity):
    """Build an ordered playlist of clips for one button.

    Add opens the file picker (multiple files can be selected; picking a
    folder adds every clip in it). Done returns the list to the button
    editor; the back button cancels.
    """

    def onCreate(self):
        extras = self.getIntent().extras or {}
        self._items = list(extras.get("playlist") or [])

        screen = lv.obj()
        screen.remove_flag(lv.obj.FLAG.SCROLLABLE)
        screen.set_style_pad_all(0, lv.PART.MAIN)
        screen.set_style_border_width(0, lv.PART.MAIN)

        header_height = max(32, DisplayMetrics.pct_of_height(16))
        header = lv.obj(screen)
        header.set_size(lv.pct(100), header_height)
        header.align(lv.ALIGN.TOP_MID, 0, 0)
        header.set_style_pad_all(2, lv.PART.MAIN)
        header.set_style_border_width(0, lv.PART.MAIN)
        header.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)

        self._title = lv.label(header)
        self._title.align(lv.ALIGN.LEFT_MID, 2, 0)

        done_button = lv.button(header)
        done_button.set_size(DisplayMetrics.pct_of_width(24), header_height - 8)
        done_button.align(lv.ALIGN.RIGHT_MID, -2, 0)
        done_label = lv.label(done_button)
        done_label.set_text(lv.SYMBOL.OK + " Done")
        done_label.center()
        done_button.add_event_cb(self._done_clicked, lv.EVENT.CLICKED, None)
        add_focus_border(done_button)

        add_button = lv.button(header)
        add_button.set_size(DisplayMetrics.pct_of_width(22), header_height - 8)
        add_button.align(lv.ALIGN.RIGHT_MID, -(DisplayMetrics.pct_of_width(24) + 6), 0)
        add_label = lv.label(add_button)
        add_label.set_text(lv.SYMBOL.PLUS + " Add")
        add_label.center()
        add_button.add_event_cb(self._add_clicked, lv.EVENT.CLICKED, None)
        add_focus_border(add_button)

        self._list = lv.obj(screen)
        self._list.set_size(lv.pct(100), DisplayMetrics.height() - header_height)
        self._list.align(lv.ALIGN.BOTTOM_MID, 0, 0)
        self._list.set_flex_flow(lv.FLEX_FLOW.COLUMN)
        self._list.set_style_pad_all(4, lv.PART.MAIN)
        self._list.set_style_pad_gap(4, lv.PART.MAIN)
        self._list.set_style_border_width(0, lv.PART.MAIN)

        cliptv_grid.add_floating_back_button(screen)

        self._rebuild()
        self.setContentView(screen)

        # An empty playlist means the user just chose to build one: go
        # straight to the picker.
        if not self._items:
            self._add_clicked()

    def _rebuild(self):
        self._title.set_text("Playlist (%d)" % len(self._items))
        self._list.clean()
        if not self._items:
            hint = lv.label(self._list)
            hint.set_text("No clips yet - tap Add")
            return
        for index, item in enumerate(self._items):
            row = lv.obj(self._list)
            row.set_width(lv.pct(100))
            row.set_height(lv.SIZE_CONTENT)
            row.set_flex_flow(lv.FLEX_FLOW.ROW)
            row.set_style_flex_cross_place(lv.FLEX_ALIGN.CENTER, lv.PART.MAIN)
            row.set_style_pad_all(3, lv.PART.MAIN)
            row.set_style_pad_gap(4, lv.PART.MAIN)
            row.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)

            label = lv.label(row)
            label.set_text("%d. %s" % (index + 1, item.rsplit("/", 1)[-1]))
            label.set_long_mode(lv.label.LONG_MODE.DOTS)
            label.set_flex_grow(1)

            if index > 0:
                up_button = lv.button(row)
                up_button.set_size(26, 26)
                up_label = lv.label(up_button)
                up_label.set_text(lv.SYMBOL.UP)
                up_label.center()
                up_button.add_event_cb(
                    lambda e, i=index: self._move_up(i), lv.EVENT.CLICKED, None
                )
                add_focus_border(up_button)

            remove_button = lv.button(row)
            remove_button.set_size(26, 26)
            remove_label = lv.label(remove_button)
            remove_label.set_text(lv.SYMBOL.TRASH)
            remove_label.center()
            remove_button.add_event_cb(
                lambda e, i=index: self._remove(i), lv.EVENT.CLICKED, None
            )
            add_focus_border(remove_button)

    def _move_up(self, index):
        self._items[index - 1], self._items[index] = (
            self._items[index], self._items[index - 1]
        )
        self._rebuild()

    def _remove(self, index):
        self._items.pop(index)
        self._rebuild()

    def _add_clicked(self, event=None):
        patterns = list(cliptv_common.AUDIO_EXTENSIONS + cliptv_common.VIDEO_EXTENSIONS)
        intent = Intent(
            action="pick_file",
            extras={"start_dir": pick_start_dir(), "path_pattern": patterns},
        )
        self.startActivityForResult(intent, self._add_result)

    def _add_result(self, result):
        if not result or not result.get("result_code"):
            return
        paths = result.get("data", {}).get("paths", [])
        for path in paths:
            if path.endswith("/"):
                try:
                    # FAT32 (SD card) rejects directory paths ending with '/'.
                    entries = os.listdir(path.rstrip("/") or "/")
                    entries.sort()
                    for entry in entries:
                        if cliptv_common.matches_kind(entry, "any"):
                            self._items.append(path + entry)
                except OSError:
                    pass
            elif cliptv_common.matches_kind(path, "any"):
                self._items.append(path)
        self._rebuild()

    def _done_clicked(self, event=None):
        self.setResult(True, {"playlist": list(self._items)})
        self.finish()


class EditButtonsActivity(Activity):
    """Grid view of all screens; tap a button to edit it."""

    def onCreate(self):
        extras = self.getIntent().extras or {}
        self._prefs = extras.get("prefs") or cliptv_common.get_prefs()
        self._screen_index = 0

        screen = lv.obj()
        screen.remove_flag(lv.obj.FLAG.SCROLLABLE)
        screen.set_style_pad_all(0, lv.PART.MAIN)
        screen.set_style_border_width(0, lv.PART.MAIN)

        header_height = DisplayMetrics.pct_of_height(16)
        header = lv.obj(screen)
        header.set_size(lv.pct(100), header_height)
        header.align(lv.ALIGN.TOP_MID, 0, 0)
        header.set_style_pad_all(2, lv.PART.MAIN)
        header.set_style_border_width(0, lv.PART.MAIN)
        header.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)

        self._hint = lv.label(header)
        self._hint.set_text("Tap a button to edit")
        self._hint.align(lv.ALIGN.LEFT_MID, 2, 0)

        self._page_label = lv.label(header)
        self._page_label.align(lv.ALIGN.RIGHT_MID, -102, 0)

        prev_button = lv.button(header)
        prev_button.set_size(22, 22)
        prev_button.align(lv.ALIGN.RIGHT_MID, -78, 0)
        prev_label = lv.label(prev_button)
        prev_label.set_text(lv.SYMBOL.LEFT)
        prev_label.center()
        prev_button.add_event_cb(lambda e: self._change_page(-1), lv.EVENT.CLICKED, None)
        add_focus_border(prev_button)

        next_button = lv.button(header)
        next_button.set_size(22, 22)
        next_button.align(lv.ALIGN.RIGHT_MID, -52, 0)
        next_label = lv.label(next_button)
        next_label.set_text(lv.SYMBOL.RIGHT)
        next_label.center()
        next_button.add_event_cb(lambda e: self._change_page(1), lv.EVENT.CLICKED, None)
        add_focus_border(next_button)

        remove_screen_button = lv.button(header)
        remove_screen_button.set_size(22, 22)
        remove_screen_button.align(lv.ALIGN.RIGHT_MID, -26, 0)
        remove_screen_label = lv.label(remove_screen_button)
        remove_screen_label.set_text(lv.SYMBOL.MINUS)
        remove_screen_label.center()
        remove_screen_button.add_event_cb(self._remove_screen_clicked, lv.EVENT.CLICKED, None)
        add_focus_border(remove_screen_button)

        add_screen_button = lv.button(header)
        add_screen_button.set_size(22, 22)
        add_screen_button.align(lv.ALIGN.RIGHT_MID, 0, 0)
        add_screen_label = lv.label(add_screen_button)
        add_screen_label.set_text(lv.SYMBOL.PLUS)
        add_screen_label.center()
        add_screen_button.add_event_cb(self._add_screen_clicked, lv.EVENT.CLICKED, None)
        add_focus_border(add_screen_button)

        self._grid = lv.obj(screen)
        self._grid.set_size(lv.pct(100), DisplayMetrics.height() - header_height)
        self._grid.align(lv.ALIGN.BOTTOM_MID, 0, 0)

        cliptv_grid.add_floating_back_button(screen)

        self.setContentView(screen)

    def onResume(self, screen):
        super().onResume(screen)
        self._prefs.load()
        self._render()

    def _render(self):
        self._hint.set_text("Tap a button to edit")
        screen_count = cliptv_common.get_screen_count(self._prefs)
        self._screen_index = min(self._screen_index, screen_count - 1)
        self._page_label.set_text("%d/%d" % (self._screen_index + 1, screen_count))
        cliptv_grid.populate_grid(self._grid, self._prefs, self._screen_index, self._button_clicked)

    def _change_page(self, delta):
        screen_count = cliptv_common.get_screen_count(self._prefs)
        self._screen_index = (self._screen_index + delta) % screen_count
        self._render()

    def _remove_screen_clicked(self, event=None):
        if cliptv_common.get_screen_count(self._prefs) <= 1:
            self._hint.set_text("Need at least one screen")
            return
        if cliptv_common.screen_has_assignments(self._prefs, self._screen_index):
            self._hint.set_text("Screen has buttons - clear them first")
            return
        cliptv_common.remove_screen(self._prefs, self._screen_index)
        self._render()

    def _add_screen_clicked(self, event=None):
        before = cliptv_common.get_screen_count(self._prefs)
        after = cliptv_common.add_screen(self._prefs)
        if after == before:
            self._hint.set_text("Max %d screens" % cliptv_common.MAX_SCREEN_COUNT)
            return
        # Jump straight to the new empty screen.
        self._screen_index = after - 1
        self._render()

    def _button_clicked(self, screen_index, button_index):
        intent = Intent(activity_class=ButtonEditorActivity)
        intent.putExtra("screen_index", screen_index)
        intent.putExtra("button_index", button_index)
        intent.putExtra("prefs", self._prefs)
        self.startActivity(intent)


class AboutActivity(Activity):
    """About page for ClipTV."""

    def onCreate(self):
        screen = lv.obj()
        screen.set_flex_flow(lv.FLEX_FLOW.COLUMN)
        screen.set_style_pad_all(DisplayMetrics.pct_of_width(2), lv.PART.MAIN)
        screen.set_style_border_width(0, lv.PART.MAIN)

        self._add_label(screen, "%s %s" % (cliptv_common.APP_NAME, cliptv_common.APP_VERSION), is_header=True)
        self._add_label(
            screen,
            "A soundboard for audio and video clips. Assign clips from the "
            "SD card to buttons, then tap a button to play its clip. "
            "Long-press any button to edit it.",
        )
        self._add_label(
            screen,
            "Buttons can also play a playlist of hand-picked clips, in "
            "order or shuffled, or random clips (audio, video, or anything). Random "
            "picks come from the built-in sample clips and %s and its "
            "subfolders. With Infinite loop enabled, a fixed clip repeats "
            "forever and a random button keeps rolling a new clip each time "
            "one finishes; buttons doing this show an infinity icon. Tap "
            "the button again (or Stop) to end it."
            % (cliptv_common.clips_base_dir() or cliptv_common.CLIPS_DIR),
        )

        self._add_label(screen, "Audio output", is_header=True)
        self._add_label(
            screen,
            "Audio plays through a PCM5102A I2S DAC. Default wiring on the "
            "Waveshare ESP32-S3-Touch-LCD-2 header:",
        )
        self._add_label(screen, "BCK -> GPIO%d" % cliptv_common.DEFAULT_PIN_BCK)
        self._add_label(screen, "LRCK (LCK) -> GPIO%d" % cliptv_common.DEFAULT_PIN_LRCK)
        self._add_label(screen, "DIN -> GPIO%d" % cliptv_common.DEFAULT_PIN_DIN)
        self._add_label(screen, "VIN -> 3V3, GND -> GND, SCK -> GND")
        self._add_label(screen, "Pins can be changed in Settings.")

        self._add_label(screen, "Clips", is_header=True)
        self._add_label(
            screen,
            "ClipTV ships with a few sample sounds (a 1990s telephone "
            "ring, a dog, a pig and a cat) so there is something to play "
            "out of the box. Add your own on a FAT32 SD card, e.g. in %s. "
            "Audio: WAV (16-bit PCM). Video: MJPEG (.mjpeg, shown at its "
            "encoded size) or raw RGB565 (.rgb565, scaled to fit) named "
            "like clip_160x120_12fps.rgb565, with an optional companion "
            ".wav of the same name for sound. See the README for ffmpeg "
            "conversion commands." % cliptv_common.CLIPS_DIR,
        )

        # GPL section 5(d): an interactive program shows its legal notices.
        self._add_label(screen, "Licence", is_header=True)
        self._add_label(
            screen,
            "ClipTV is free software under the GNU General Public License, "
            "version 3 or (at your option) any later version, with no "
            "warranty. The bundled sample clips carry their own licences, "
            "listed in clips/README.md.",
        )

        cliptv_grid.add_floating_back_button(screen)

        self.setContentView(screen)

    def _add_label(self, parent, text, is_header=False):
        label = lv.label(parent)
        label.set_text(text)
        label.set_width(lv.pct(98))
        label.set_long_mode(lv.label.LONG_MODE.WRAP)
        add_focus_highlight(label)
        lv.group_get_default().add_obj(label)
        if is_header:
            label.set_style_text_color(lv.theme_get_color_primary(None), lv.PART.MAIN)
            label.set_style_text_font(lv.font_montserrat_16, lv.PART.MAIN)
            label.set_style_margin_top(DisplayMetrics.pct_of_height(3), lv.PART.MAIN)
        else:
            label.set_style_text_font(lv.font_montserrat_12, lv.PART.MAIN)
        return label


class CliptvSettings(SettingsActivity):
    """ClipTV settings page."""

    def onResume(self, screen):
        # SettingsActivity rebuilds (and cleans) the screen on every resume,
        # so the floating back button must be re-added afterwards.
        super().onResume(screen)
        cliptv_grid.add_floating_back_button(screen)

    def getIntent(self):
        prefs = cliptv_common.get_prefs()
        self._prefs_for_callbacks = prefs
        is_esp32 = sys.platform == "esp32"
        pin_note = "GPIO number on the 40-pin header. Takes effect on next playback."

        intent = Intent()
        intent.putExtra("prefs", prefs)
        intent.putExtra("settings", [
            {
                "title": "Assign buttons",
                "key": "assign_buttons",
                "ui": "activity",
                "activity_class": EditButtonsActivity,
                "placeholder": "Edit buttons, +/- screens",
            },
            {
                "title": "Buttons per screen",
                "key": "buttons_per_screen",
                "ui": "dropdown",
                "ui_options": cliptv_common.BUTTONS_PER_SCREEN_OPTIONS,
                "default_value": str(cliptv_common.DEFAULT_BUTTONS_PER_SCREEN),
            },
            {
                "title": "Volume",
                "key": "volume",
                "ui": "slider",
                "min": 0,
                "max": 100,
                "default_value": str(cliptv_common.DEFAULT_VOLUME),
                "changed_callback": self._volume_changed,
            },
            {
                "title": "DAC BCK pin",
                "key": "pin_bck",
                "placeholder": str(cliptv_common.DEFAULT_PIN_BCK),
                "default_value": str(cliptv_common.DEFAULT_PIN_BCK),
                "note": pin_note,
                "should_show": is_esp32,
                "changed_callback": self._pins_changed,
            },
            {
                "title": "DAC LRCK pin",
                "key": "pin_lrck",
                "placeholder": str(cliptv_common.DEFAULT_PIN_LRCK),
                "default_value": str(cliptv_common.DEFAULT_PIN_LRCK),
                "note": pin_note,
                "should_show": is_esp32,
                "changed_callback": self._pins_changed,
            },
            {
                "title": "DAC DIN pin",
                "key": "pin_din",
                "placeholder": str(cliptv_common.DEFAULT_PIN_DIN),
                "default_value": str(cliptv_common.DEFAULT_PIN_DIN),
                "note": pin_note,
                "should_show": is_esp32,
                "changed_callback": self._pins_changed,
            },
            {
                "title": "About",
                "key": "about",
                "ui": "activity",
                "activity_class": AboutActivity,
                "placeholder": "Version, wiring and clip formats",
            },
        ])
        return intent

    def _volume_changed(self, new_value):
        try:
            AudioManager.set_volume(int(new_value))
        except (TypeError, ValueError):
            pass

    def _pins_changed(self, new_value):
        cliptv_common.ensure_audio_output(self._prefs_for_callbacks)
