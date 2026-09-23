# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ZapTV.org
#
# This file is part of ClipTV. ClipTV is free software: you can redistribute
# it and/or modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. It is distributed WITHOUT
# ANY WARRANTY; see the GNU General Public License (LICENSE) for details.

import logging
import random

import lvgl as lv

from mpos import Activity, AudioManager, DisplayMetrics, Intent, add_focus_border

import cliptv_common
import cliptv_grid
from cliptv_settings import ButtonEditorActivity, CliptvSettings
from video_player import VideoPlayerActivity

logger = logging.getLogger(__name__)


class ClipTV(Activity):
    """ClipTV soundboard: tap a button to play its clip, long-press to edit."""

    def onCreate(self):
        self._equalizer = None
        self._eq_bars = []
        self._eq_timer = None
        self._prefs = cliptv_common.get_prefs()
        self._screen_index = 0
        self._widgets = []
        self._playing_index = None
        # Playback generation: bumped on every new play/stop so stale
        # on_complete callbacks from finished or superseded clips are ignored.
        self._play_generation = 0
        # Active sequence state: the button key and config driving a chain of
        # clips (an infinite loop, a playlist, or both), plus the playlist
        # position of the NEXT entry to play.
        self._loop_key = None
        self._loop_config = None
        # The playing order for the active playlist (shuffled when the
        # button has shuffle enabled) and the position of the NEXT entry.
        self._playlist_order = None
        self._playlist_pos = 0
        # Set when a chain hands off to the video player; onResume continues
        # the chain when it is still the current generation.
        self._chain_on_resume = None

        cliptv_common.ensure_sdcard()
        cliptv_common.apply_volume(self._prefs)

        screen = lv.obj()
        screen.remove_flag(lv.obj.FLAG.SCROLLABLE)
        screen.set_style_pad_all(0, lv.PART.MAIN)
        screen.set_style_border_width(0, lv.PART.MAIN)

        header_height = max(28, DisplayMetrics.pct_of_height(14))
        header = lv.obj(screen)
        header.set_size(lv.pct(100), header_height)
        header.align(lv.ALIGN.TOP_MID, 0, 0)
        header.set_style_pad_all(2, lv.PART.MAIN)
        header.set_style_border_width(0, lv.PART.MAIN)
        header.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)

        button_size = header_height - 6

        settings_button = lv.button(header)
        settings_button.set_size(button_size, button_size)
        settings_button.align(lv.ALIGN.LEFT_MID, 2, 0)
        settings_label = lv.label(settings_button)
        settings_label.set_text(lv.SYMBOL.SETTINGS)
        settings_label.center()
        settings_button.add_event_cb(self._settings_clicked, lv.EVENT.CLICKED, None)
        add_focus_border(settings_button)

        stop_button = lv.button(header)
        stop_button.set_size(button_size, button_size)
        stop_button.align(lv.ALIGN.LEFT_MID, button_size + 6, 0)
        stop_label = lv.label(stop_button)
        stop_label.set_text(lv.SYMBOL.STOP)
        stop_label.center()
        stop_button.add_event_cb(self._stop_clicked, lv.EVENT.CLICKED, None)
        add_focus_border(stop_button)

        # Volume: quick -/+ controls flanking a level pill that doubles as
        # the indicator, centred in the header.
        self._volume = self._prefs.get_int("volume", cliptv_common.DEFAULT_VOLUME)
        volbox = lv.obj(header)
        volbox.set_height(button_size)
        volbox.set_width(lv.SIZE_CONTENT)
        volbox.align(lv.ALIGN.CENTER, 0, 0)
        volbox.set_style_bg_opa(lv.OPA.TRANSP, lv.PART.MAIN)
        volbox.set_style_border_width(0, lv.PART.MAIN)
        volbox.set_style_pad_all(0, lv.PART.MAIN)
        volbox.set_style_pad_column(6, lv.PART.MAIN)
        volbox.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
        volbox.remove_flag(lv.obj.FLAG.SCROLLABLE)
        volbox.set_flex_flow(lv.FLEX_FLOW.ROW)
        volbox.set_flex_align(lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER)

        vol_down = lv.button(volbox)
        vol_down.set_size(button_size, button_size)
        vol_down_label = lv.label(vol_down)
        vol_down_label.set_text(lv.SYMBOL.MINUS)
        vol_down_label.center()
        vol_down.add_event_cb(lambda e: self._change_volume(-10), lv.EVENT.CLICKED, None)
        add_focus_border(vol_down)

        self._vol_bar = lv.bar(volbox)
        self._vol_bar.set_size(button_size * 2, button_size)
        self._vol_bar.set_range(0, 100)
        self._vol_bar.set_style_radius(4, lv.PART.MAIN)
        self._vol_bar.set_style_radius(4, lv.PART.INDICATOR)
        self._vol_bar.set_style_bg_color(lv.color_hex(0x333333), lv.PART.MAIN)
        self._vol_bar.set_style_bg_color(lv.color_hex(0xF0A010), lv.PART.INDICATOR)
        self._vol_indicator = lv.label(self._vol_bar)
        self._vol_indicator.set_style_text_color(lv.color_hex(0xFFFFFF), lv.PART.MAIN)
        self._vol_indicator.center()

        vol_up = lv.button(volbox)
        vol_up.set_size(button_size, button_size)
        vol_up_label = lv.label(vol_up)
        vol_up_label.set_text(lv.SYMBOL.PLUS)
        vol_up_label.center()
        vol_up.add_event_cb(lambda e: self._change_volume(10), lv.EVENT.CLICKED, None)
        add_focus_border(vol_up)

        self._update_volume_indicator()

        self._page_label = lv.label(header)
        self._page_label.align(lv.ALIGN.RIGHT_MID, -(2 * button_size + 10), 0)

        prev_button = lv.button(header)
        prev_button.set_size(button_size, button_size)
        prev_button.align(lv.ALIGN.RIGHT_MID, -(button_size + 6), 0)
        prev_label = lv.label(prev_button)
        prev_label.set_text(lv.SYMBOL.LEFT)
        prev_label.center()
        prev_button.add_event_cb(lambda e: self._change_page(-1), lv.EVENT.CLICKED, None)
        add_focus_border(prev_button)

        next_button = lv.button(header)
        next_button.set_size(button_size, button_size)
        next_button.align(lv.ALIGN.RIGHT_MID, -2, 0)
        next_label = lv.label(next_button)
        next_label.set_text(lv.SYMBOL.RIGHT)
        next_label.center()
        next_button.add_event_cb(lambda e: self._change_page(1), lv.EVENT.CLICKED, None)
        add_focus_border(next_button)

        self._grid = lv.obj(screen)
        self._grid.set_size(lv.pct(100), DisplayMetrics.height() - header_height)
        self._grid.align(lv.ALIGN.BOTTOM_MID, 0, 0)

        self.setContentView(screen)

    def onDestroy(self, screen):
        # Leaving the app: mute the DAC and stop the I2S/MCLK that WAVStream
        # keeps warm between clips, so the peripheral is free for other apps.
        try:
            from mpos.audio.stream_wav import WAVStream
            release = getattr(WAVStream, "release_warm", None)
            if release:
                release()
        except Exception as e:
            logger.error("release_warm failed: %s", e)
        super().onDestroy(screen)

    def onResume(self, screen):
        super().onResume(screen)
        # Settings may have changed while we were paused.
        self._prefs.load()
        cliptv_common.apply_volume(self._prefs)
        self._volume = self._prefs.get_int("volume", cliptv_common.DEFAULT_VOLUME)
        if hasattr(self, "_vol_bar"):
            self._update_volume_indicator()
        self._render()
        # A "completely random" loop that handed off to the video player
        # continues rolling when the video finishes and we come back.
        if self._chain_on_resume is not None:
            chain_generation = self._chain_on_resume
            self._chain_on_resume = None
            if chain_generation == self._play_generation and self._loop_config:
                self._sequence_next()

    def _render(self):
        screen_count = cliptv_common.get_screen_count(self._prefs)
        self._screen_index = min(self._screen_index, screen_count - 1)
        self._page_label.set_text("%d/%d" % (self._screen_index + 1, screen_count))
        self._clear_playing_indicator()
        self._widgets = cliptv_grid.populate_grid(
            self._grid,
            self._prefs,
            self._screen_index,
            self._button_clicked,
            self._button_long_pressed,
        )

    def _change_volume(self, delta):
        volume = max(0, min(100, self._volume + delta))
        if volume == self._volume:
            return
        self._volume = volume
        AudioManager.set_volume(volume)
        editor = self._prefs.edit()
        editor.put_string("volume", str(volume))
        editor.commit()
        self._update_volume_indicator()

    def _update_volume_indicator(self):
        self._vol_bar.set_value(self._volume, False)
        self._vol_indicator.set_text("%d%%" % self._volume)

    def _change_page(self, delta):
        screen_count = cliptv_common.get_screen_count(self._prefs)
        self._screen_index = (self._screen_index + delta) % screen_count
        self._render()

    def _settings_clicked(self, event=None):
        self.startActivity(Intent(activity_class=CliptvSettings))

    def _stop_clicked(self, event=None):
        self._stop_playback()

    def _stop_playback(self):
        self._play_generation += 1
        self._loop_key = None
        self._loop_config = None
        self._playlist_order = None
        self._playlist_pos = 0
        self._chain_on_resume = None
        AudioManager.stop()
        self._clear_playing_indicator()

    def _build_playlist_order(self, config, previous_last=None):
        """The playing order for one pass of a playlist button."""
        playlist = config.get("playlist") or []
        if not config.get("shuffle"):
            return list(playlist)
        order = cliptv_common.shuffled(playlist)
        # Avoid replaying the previous pass's last clip back-to-back.
        if previous_last is not None and len(order) > 1 and order[0] == previous_last:
            order[0], order[-1] = order[-1], order[0]
        return order

    def _button_clicked(self, screen_index, button_index):
        config = cliptv_common.get_button_config(self._prefs, screen_index, button_index)
        if not cliptv_common.button_is_assigned(config):
            # Unassigned button: jump straight into the editor.
            self._open_editor(screen_index, button_index)
            return
        key = (screen_index, button_index)
        if config.get("loop") and self._loop_key == key:
            # Pressing the actively looping button again toggles it off.
            self._stop_playback()
            return

        self._play_generation += 1
        # A sequence (chain of clips) is driven by an infinite loop, a
        # playlist, or both; a plain one-shot button needs no chain state.
        if config.get("loop") or config.get("playlist"):
            self._loop_key = key
            self._loop_config = config
        else:
            self._loop_key = None
            self._loop_config = None
        self._playlist_pos = 0
        self._playlist_order = None

        if config.get("playlist"):
            self._playlist_order = self._build_playlist_order(config)
            self._sequence_next()
            return

        clip = cliptv_common.resolve_button_clip(config)
        if not clip:
            if config.get("action"):
                self._show_button_message(key, "No clips\nfound")
            return
        if config.get("clip") and not cliptv_common.path_exists(clip):
            cliptv_common.ensure_sdcard()
            if not cliptv_common.path_exists(clip):
                logger.warning("Clip not found: %s", clip)
                self._show_button_message(key, "Clip\nmissing")
                return
        self._play_clip(clip, key, config)

    def _play_clip(self, clip, key, config):
        loop = bool(config.get("loop"))
        action = config.get("action")
        playlist = bool(config.get("playlist"))
        if cliptv_common.is_video_clip(clip):
            AudioManager.stop()
            self._clear_playing_indicator()
            intent = Intent(activity_class=VideoPlayerActivity)
            intent.putExtra("clip", clip)
            if playlist:
                # Play this entry once; the sequence continues (audio or
                # video) when we resume from the video player.
                self._chain_on_resume = self._play_generation
            elif loop and action == cliptv_common.ACTION_RANDOM_VIDEO:
                # The video player re-rolls new random videos itself.
                intent.putExtra("loop", True)
                intent.putExtra("shuffle", True)
            elif loop and not action:
                intent.putExtra("loop", True)
            elif loop:
                # "Completely random" loop: same resume-chain as playlists.
                self._chain_on_resume = self._play_generation
            self.startActivity(intent)
            return
        chained = bool(loop or playlist)
        self._play_audio(clip, key, chained=chained,
                         gapless=bool(loop and not action and not playlist))

    def _play_audio(self, clip, key, chained=False, gapless=False):
        generation = self._play_generation
        if chained:
            # Sequence playback: when a pass completes, play the next entry
            # (the same clip for fixed loops, a fresh roll for random ones,
            # the next playlist entry for playlists). On-device the endless
            # repeat below plays fixed loops gaplessly and never completes;
            # desktop playback ignores set_repeat, so this completion re-arm
            # is what keeps loops infinite there.
            def on_complete(result=None):
                self.update_ui_threadsafe_if_foreground(self._loop_next, generation)
        else:
            on_complete = self._playback_finished
        try:
            output = cliptv_common.ensure_audio_output(self._prefs)
            player = cliptv_common.play_audio_clip(clip, output, on_complete=on_complete)
            if gapless:
                player.set_repeat(cliptv_common.ENDLESS_REPEAT_COUNT)
        except Exception as e:
            logger.error("Playback failed for %s: %s", clip, e)
            return
        self._mark_playing(key)

    def _loop_next(self, generation):
        if generation != self._play_generation:
            return  # stopped or superseded in the meantime
        self._sequence_next()

    def _sequence_next(self):
        config = self._loop_config
        key = self._loop_key
        if not config or not key:
            return
        if config.get("playlist"):
            order = self._playlist_order or self._build_playlist_order(config)
            self._playlist_order = order
            # Skip missing entries, but never spin more than one full pass.
            for _ in range(len(order)):
                if self._playlist_pos >= len(order):
                    if config.get("loop"):
                        # New pass; reshuffle when shuffling.
                        order = self._build_playlist_order(
                            config, previous_last=order[-1] if order else None
                        )
                        self._playlist_order = order
                        self._playlist_pos = 0
                    else:
                        # End of a one-shot playlist.
                        self._loop_key = None
                        self._loop_config = None
                        self._playlist_order = None
                        self._clear_playing_indicator()
                        return
                clip = order[self._playlist_pos]
                self._playlist_pos += 1
                if cliptv_common.path_exists(clip):
                    self._play_clip(clip, key, config)
                    return
                logger.warning("Playlist entry not found: %s", clip)
            self._show_button_message(key, "Clips\nmissing")
            return
        clip = cliptv_common.resolve_button_clip(config)
        if not clip:
            self._show_button_message(key, "No clips\nfound")
            return
        self._play_clip(clip, key, config)

    def _mark_playing(self, key):
        """Show an animated equaliser along the bottom of the playing button."""
        self._clear_playing_indicator()
        screen_index, button_index = key
        if screen_index == self._screen_index and button_index < len(self._widgets):
            button, label = self._widgets[button_index]
            color = label.get_style_text_color(lv.PART.MAIN)
            self._equalizer, self._eq_bars = cliptv_grid.add_equalizer(button, color)
            self._equalizer.remove_flag(lv.obj.FLAG.HIDDEN)
            self._eq_tick(None)
            self._eq_timer = lv.timer_create(self._eq_tick, 90, None)
            self._playing_index = button_index

    def _eq_tick(self, timer):
        for bar in self._eq_bars:
            bar.set_height(lv.pct(random.randint(15, 100)))

    def _clear_playing_indicator(self):
        if self._eq_timer is not None:
            self._eq_timer.delete()
            self._eq_timer = None
        if self._equalizer is not None and self._playing_index is not None \
                and self._playing_index < len(self._widgets):
            self._equalizer.delete()
        self._equalizer = None
        self._eq_bars = []
        self._playing_index = None

    def _playback_finished(self, result=None):
        self.update_ui_threadsafe_if_foreground(self._clear_playing_indicator)

    def _show_button_message(self, key, message):
        screen_index, button_index = key
        if screen_index == self._screen_index and button_index < len(self._widgets):
            _, label = self._widgets[button_index]
            label.set_text(message)

    def _button_long_pressed(self, screen_index, button_index):
        self._stop_playback()
        self._open_editor(screen_index, button_index)

    def _open_editor(self, screen_index, button_index):
        intent = Intent(activity_class=ButtonEditorActivity)
        intent.putExtra("screen_index", screen_index)
        intent.putExtra("button_index", button_index)
        intent.putExtra("prefs", self._prefs)
        self.startActivity(intent)
