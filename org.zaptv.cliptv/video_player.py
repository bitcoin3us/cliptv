# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ZapTV.org
#
# This file is part of ClipTV. ClipTV is free software: you can redistribute
# it and/or modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. It is distributed WITHOUT
# ANY WARRANTY; see the GNU General Public License (LICENSE) for details.

import logging
import time

import lvgl as lv

from mpos import Activity, DisplayMetrics

import cliptv_common
import cliptv_grid

logger = logging.getLogger(__name__)

DEFAULT_FPS = 12
MIN_FPS = 1
MAX_FPS = 30
BYTES_PER_PIXEL = 2  # RGB565

MJPEG_EXTENSIONS = (".mjpeg", ".mjpg")
try:
    import jpegdec  # native decoder (MicroPythonOS c_mpos); falls back to LVGL's if absent
except ImportError:
    jpegdec = None
_JPEG_SOI = b"\xff\xd8"
_JPEG_EOI = b"\xff\xd9"
_MJPEG_READ_CHUNK = 32 * 1024
# LVGL keeps decoded images in a cache keyed by the descriptor's address and
# the binding exposes no way to drop entries, so a recycled descriptor address
# would be served the previously decoded frame. Recent descriptors are kept
# alive until the cache (this many bytes on MicroPythonOS builds) has had to
# evict their entries, and only then released for reuse.
_LVGL_IMAGE_CACHE_BYTES = 3686400


def parse_video_filename(path):
    """Parse "<name>_<W>x<H>[_<fps>fps].rgb565" into (width, height, fps).

    Returns (None, None, fps) when no dimensions are found in the name.
    """
    name = path.rsplit("/", 1)[-1]
    base = name.rsplit(".", 1)[0]
    width = height = None
    fps = DEFAULT_FPS
    for part in base.split("_"):
        lower = part.lower()
        if lower.endswith("fps"):
            try:
                fps = max(MIN_FPS, min(MAX_FPS, int(lower[:-3])))
            except ValueError:
                pass
        elif "x" in lower:
            try:
                w_str, h_str = lower.split("x", 1)
                width, height = int(w_str), int(h_str)
            except ValueError:
                pass
    return width, height, fps


class VideoPlayerActivity(Activity):
    """Plays a raw RGB565 (.rgb565) or MJPEG (.mjpeg) video clip.

    The file is a plain sequence of little-endian RGB565 frames; width,
    height and frame rate are encoded in the filename, for example
    "dance_160x120_12fps.rgb565". A companion WAV with the same basename
    is played alongside the video when present.

    Raw RGB565 is used instead of a compressed format because it needs no
    image decoder: frames are read straight into a buffer that the LVGL
    image widget displays directly (the same pattern the camera preview
    uses), which works on stock MicroPythonOS firmware.
    """

    def onCreate(self):
        extras = self.getIntent().extras or {}
        self._reader_file = None
        self._timer = None
        self._mjpeg = False
        self._mj_buf = None
        self._mj_len = 0
        self._mj_pos = 0
        self._mj_ring = []        # [dsc, bytearray] slots, reused round-robin
        self._mj_ring_i = 0
        self._mj_ring_len = 8
        self._mj_drop = None
        self._mj_native = False   # decode with jpegdec into RGB565 buffers
        self._mj_last_jpeg = None
        self._mj_rgb = None       # two (dsc, bytearray) RGB565 targets
        self._mj_rgb_i = 0
        self._mj_frames_shown = 0
        self._mj_start_ms = 0
        self._mj_period_ms = 1000 // DEFAULT_FPS
        self._mj_work_ema = 0   # smoothed tick cost, ms x16
        self._mj_file_off = 0     # file offset of buffer byte 0
        self._mj_avg_frame = 0    # running average encoded frame size
        self._mj_frames_read = 0
        # Per-phase timing (microseconds) and counters, readable by test tools.
        self._mj_prof = {"skip_us": 0, "read_us": 0, "copy_us": 0, "dsc_us": 0,
                         "setsrc_us": 0, "gc_us": 0, "ticks": 0, "skipped": 0, "fills": 0}
        self._audio_player = None
        self._buffers = None
        self._buffer_index = 0
        self._image_dsc = None
        self._clip = extras.get("clip")
        self._loop = bool(extras.get("loop"))
        # With shuffle + loop, a new random video is picked when this one ends.
        self._shuffle = bool(extras.get("shuffle"))
        self._width = self._height = None
        self._fps = DEFAULT_FPS

        screen = lv.obj()
        screen.remove_flag(lv.obj.FLAG.SCROLLABLE)
        screen.set_style_bg_color(lv.color_hex(0x000000), lv.PART.MAIN)
        screen.set_style_pad_all(0, lv.PART.MAIN)
        screen.set_style_border_width(0, lv.PART.MAIN)

        self._image = lv.image(screen)
        self._image.center()
        self._image.add_flag(lv.obj.FLAG.CLICKABLE)
        self._image.add_event_cb(lambda e: self.finish(), lv.EVENT.CLICKED, None)

        self._status_label = lv.label(screen)
        self._status_label.set_text("")
        self._status_label.set_width(lv.pct(90))
        self._status_label.set_long_mode(lv.label.LONG_MODE.WRAP)
        self._status_label.set_style_text_color(lv.color_hex(0xFFFFFF), lv.PART.MAIN)
        self._status_label.center()

        cliptv_grid.add_floating_back_button(screen)

        self.setContentView(screen)

    def onResume(self, screen):
        super().onResume(screen)
        if self._timer or not self._clip:
            return
        if not self._open_clip(self._clip):
            return
        self._timer = lv.timer_create(self._show_next_frame, 1000 // self._fps, None)
        self._timer.set_repeat_count(-1)

    def _open_clip(self, clip):
        """(Re)open a video file, allocating buffers and starting its audio."""
        width, height, fps = parse_video_filename(clip)
        if not width or not height:
            self._status_label.set_text(
                "Video filename must include dimensions,\n"
                "e.g. clip_160x120_12fps.rgb565"
            )
            return False
        try:
            reader_file = open(clip, "rb")
        except OSError as e:
            logger.error("Could not open video %s: %s", clip, e)
            self._status_label.set_text("Could not open\n%s" % clip)
            return False

        if clip.lower().endswith(MJPEG_EXTENSIONS):
            return self._open_mjpeg(clip, reader_file, width, height, fps)

        frame_size = width * height * BYTES_PER_PIXEL
        if not self._buffers or len(self._buffers[0]) != frame_size:
            try:
                # Double-buffered so the frame being displayed is never the
                # one being overwritten by the file read.
                self._buffers = (bytearray(frame_size), bytearray(frame_size))
            except MemoryError:
                reader_file.close()
                self._cleanup()
                self._status_label.set_text("Video resolution too large")
                return False

        self._close_reader()
        self._stop_companion_audio()
        self._clip = clip
        self._reader_file = reader_file
        self._width, self._height, self._fps = width, height, fps

        self._image_dsc = lv.image_dsc_t({
            "header": {
                "magic": lv.IMAGE_HEADER_MAGIC,
                "w": width,
                "h": height,
                "stride": width * BYTES_PER_PIXEL,
                "cf": lv.COLOR_FORMAT.RGB565,
            },
            "data_size": frame_size,
            "data": None,
        })

        # Scale the video up/down to fit the display, preserving aspect ratio.
        scale_w = round(DisplayMetrics.width() * 256 / width)
        scale_h = round(DisplayMetrics.height() * 256 / height)
        self._image.set_size(width, height)
        self._image.set_scale(min(scale_w, scale_h))
        self._image.center()

        self._start_companion_audio()
        if self._timer:
            try:
                self._timer.set_period(1000 // self._fps)
            except AttributeError:
                pass
        return True

    # ------------------------------------------------------------------
    #  MJPEG: a plain stream of JPEG frames (ffmpeg -f mjpeg), decoded by the
    #  firmware's JPEG decoder straight from RAM. Frames are shown at their
    #  native size, centred: scaling a decoded JPEG is not supported by the
    #  LVGL build, so encode clips at the size they should appear.
    # ------------------------------------------------------------------

    def _open_mjpeg(self, clip, reader_file, width, height, fps):
        self._close_reader()
        self._stop_companion_audio()
        self._mjpeg = True
        self._clip = clip
        self._reader_file = reader_file
        self._width, self._height, self._fps = width, height, fps
        if self._mj_buf is None:
            self._mj_buf = bytearray(2 * _MJPEG_READ_CHUNK)
        self._mj_len = 0
        self._mj_pos = 0
        # Firmware that exposes lv.image_cache_drop lets each frame's decode be
        # dropped from LVGL's cache once shown, so a few slots suffice and the
        # heap stays small. Otherwise the ring must be longer than the cache can
        # hold decoded frames of this size, so a slot is only reused once its
        # cached decode has been evicted.
        self._mj_drop = getattr(lv, "image_cache_drop", None)
        if self._mj_drop:
            self._mj_ring_len = 4
        else:
            self._mj_ring_len = _LVGL_IMAGE_CACHE_BYTES // (width * height * BYTES_PER_PIXEL) + 4
        self._mj_ring = []
        self._mj_ring_i = 0
        # Native path: the jpegdec module decodes straight into RGB565, which
        # LVGL blits like a raw frame (no decoder, no image cache involved).
        self._mj_native = jpegdec is not None
        if self._mj_native:
            frame_size = width * height * BYTES_PER_PIXEL
            try:
                self._mj_rgb = []
                for _ in range(2):
                    buf = bytearray(frame_size)
                    dsc = lv.image_dsc_t({
                        "header": {
                            "magic": lv.IMAGE_HEADER_MAGIC,
                            "w": width,
                            "h": height,
                            "stride": width * BYTES_PER_PIXEL,
                            "cf": lv.COLOR_FORMAT.RGB565,
                        },
                        "data_size": frame_size,
                        "data": None,
                    })
                    dsc.data = buf
                    self._mj_rgb.append((dsc, buf))
            except MemoryError:
                self._mj_native = False
                self._mj_rgb = None
        self._mj_frames_shown = 0
        self._mj_start_ms = time.ticks_ms()
        self._mj_period_ms = 1000 // fps
        self._mj_work_ema = 0
        self._mj_file_off = 0
        self._mj_avg_frame = 0
        self._mj_frames_read = 0

        self._image.set_size(width, height)
        self._image.set_scale(256)
        self._image.center()

        self._start_companion_audio()
        if self._timer:
            try:
                self._timer.set_period(1000 // self._fps)
            except AttributeError:
                pass
        return True

    def _mj_fill(self, want=None):
        """Read more of the file into the buffer; False at end of file.

        Reads are the expensive part of MJPEG playback (storage delivers
        about 1 MB/s, whatever the chunk size), so `want` lets a caller
        read only what it expects to need, e.g. after a seek.
        """
        buf = self._mj_buf
        want = want or _MJPEG_READ_CHUNK
        if self._mj_pos:
            # Drop consumed bytes so the buffer only holds the pending frame.
            # Copied via memoryviews: a plain slice would allocate a temporary
            # the size of the tail, and every large allocation risks a full
            # garbage collection while the decoded-frame cache fills the heap.
            n = self._mj_len - self._mj_pos
            mv = memoryview(buf)
            buf[0:n] = mv[self._mj_pos:self._mj_len]
            self._mj_len -= self._mj_pos
            self._mj_file_off += self._mj_pos
            self._mj_pos = 0
        if len(buf) - self._mj_len < want:
            grown = bytearray(max(len(buf) * 2, self._mj_len + want))
            grown[0:self._mj_len] = buf[0:self._mj_len]
            self._mj_buf = buf = grown
        n = self._reader_file.readinto(memoryview(buf)[self._mj_len:self._mj_len + want])
        self._mj_prof["fills"] += 1
        if not n:
            return False
        self._mj_len += n
        return True

    def _mj_next_frame(self, skip=False, want=None):
        """Return the next JPEG frame as bytes (or None at end of file).

        With skip=True the frame is located but not copied out.
        """
        while True:
            buf = self._mj_buf
            start = buf.find(_JPEG_SOI, self._mj_pos, self._mj_len)
            if start < 0:
                # Keep the last byte: it may be the first half of a marker.
                self._mj_pos = max(self._mj_pos, self._mj_len - 1)
                if not self._mj_fill(want):
                    return None
                continue
            end = buf.find(_JPEG_EOI, start + 2, self._mj_len)
            if end < 0:
                self._mj_pos = start
                if not self._mj_fill(want):
                    return None
                continue
            end += 2
            self._mj_pos = end
            self._mj_frames_read += 1
            size = end - start
            self._mj_avg_frame += (size - self._mj_avg_frame) // min(self._mj_frames_read, 16)
            if skip:
                return True
            return self._mj_slot_for(buf, start, end)

    def _mj_slot_for(self, buf, start, end):
        """Copy a located frame into the next ring slot; return its descriptor."""
        size = end - start
        if len(self._mj_ring) < self._mj_ring_len:
            data = bytearray(max(size, self._mj_avg_frame + self._mj_avg_frame // 4))
            dsc = lv.image_dsc_t({
                "header": {
                    "magic": lv.IMAGE_HEADER_MAGIC,
                    "w": self._width,
                    "h": self._height,
                    "stride": 0,
                    "cf": lv.COLOR_FORMAT.RAW,
                },
                "data_size": size,
                "data": None,
            })
            slot = [dsc, data]
            self._mj_ring.append(slot)
        else:
            slot = self._mj_ring[self._mj_ring_i]
            self._mj_ring_i = (self._mj_ring_i + 1) % self._mj_ring_len
            if self._mj_drop:
                # Shown several frames ago: forget its decode before reuse.
                try:
                    self._mj_drop(slot[0])
                except Exception:
                    self._mj_drop = None
            if len(slot[1]) < size:
                slot[1] = bytearray(size + size // 4)
        data = slot[1]
        data[0:size] = memoryview(buf)[start:end]
        dsc = slot[0]
        dsc.data = data
        dsc.data_size = size
        self._mj_last_jpeg = memoryview(data)[0:size]
        return dsc

    def _mj_rewind(self):
        self._reader_file.seek(0)
        self._mj_len = 0
        self._mj_pos = 0
        self._mj_file_off = 0
        self._mj_frames_shown = 0
        self._mj_start_ms = time.ticks_ms()

    def _mj_skip_ahead(self, count):
        """Skip about `count` frames without reading them.

        A raw MJPEG stream has no index, so the target is estimated from the
        average encoded frame size and the stream is resynchronised on the
        next start-of-image marker from there. Landing a frame early or
        late is fine for pacing; reading the skipped frames would cost as
        much as playing them. Returns False if the seek ran past the end.
        """
        if count <= 0 or not self._mj_avg_frame:
            return True
        target = self._mj_file_off + self._mj_pos + count * self._mj_avg_frame
        try:
            self._reader_file.seek(target)
        except OSError:
            return False
        self._mj_file_off = target
        self._mj_len = 0
        self._mj_pos = 0
        # Resync: consume the (partial) frame we landed in, reading only
        # about one frame's worth to find its end.
        if not self._mj_next_frame(skip=True, want=self._mj_avg_frame + self._mj_avg_frame // 2):
            return False
        self._mj_frames_shown += count
        return True

    def _show_next_mjpeg_frame(self):
        # Wall-clock pacing: when decoding falls behind the declared rate,
        # skip frames instead of slowing the clip down, so it keeps real
        # time and stays in sync with its soundtrack.
        tick_start = time.ticks_ms()
        prof = self._mj_prof
        elapsed = time.ticks_diff(tick_start, self._mj_start_ms)
        target = elapsed * self._fps // 1000
        behind = target - self._mj_frames_shown
        t = time.ticks_us()
        # Skipping costs about one frame's worth of reading (seek + resync),
        # so tolerate up to half a second of drift, then skip it all at once.
        if behind >= max(3, self._fps // 2):
            prof["skipped"] += behind - 1
            self._mj_skip_ahead(behind - 1)
        prof["skip_us"] += time.ticks_diff(time.ticks_us(), t)
        t = time.ticks_us()
        frame = self._mj_next_frame()
        prof["read_us"] += time.ticks_diff(time.ticks_us(), t)
        if frame is None:
            if not self._next_after_end():
                self.finish()
                return
            frame = self._mj_next_frame()
            if frame is None:
                self.finish()
                return
        self._mj_frames_shown += 1
        dsc = frame
        if self._mj_native:
            t = time.ticks_us()
            dsc = self._mj_decode_native(frame)
            prof["copy_us"] += time.ticks_diff(time.ticks_us(), t)
            if dsc is None:
                return
        self._status_label.set_text("")
        t = time.ticks_us()
        self._image.set_src(dsc)
        prof["setsrc_us"] += time.ticks_diff(time.ticks_us(), t)
        prof["ticks"] += 1
        self._mj_adapt_period(time.ticks_diff(time.ticks_ms(), tick_start))

    def _mj_decode_native(self, src_dsc):
        """Decode the frame just placed in a ring slot into the next RGB565 target."""
        target_dsc, target_buf = self._mj_rgb[self._mj_rgb_i]
        self._mj_rgb_i = 1 - self._mj_rgb_i
        try:
            jpegdec.decode(self._mj_last_jpeg, target_buf)
        except Exception as e:
            logger.error("jpegdec failed: %s", e)
            return None
        return target_dsc

    def _mj_adapt_period(self, work_ms):
        # Frame work happens inside the UI loop. Keep the timer period at
        # least ~1.3x the smoothed per-tick cost so touch handling and the
        # rest of the system keep getting time (skipping keeps the clip in
        # real time regardless), but follow a moving average rather than
        # single spikes, or one garbage-collection pause would throttle the
        # whole clip.
        nominal = 1000 // self._fps
        ema = self._mj_work_ema
        ema += (work_ms * 16 - ema) // 4 if ema else work_ms * 16
        self._mj_work_ema = ema
        avg = ema // 16
        # Leave at least a third of every period, and never less than 15 ms,
        # to the rest of the system (touch, the REPL, other apps' timers):
        # ticks that fill the period starve them and can wedge the device.
        period = max(nominal, avg + avg // 2 + 4, avg + 15)
        if period != self._mj_period_ms and self._timer:
            self._mj_period_ms = period
            try:
                self._timer.set_period(period)
            except AttributeError:
                pass

    def _start_companion_audio(self):
        dot = self._clip.rfind(".")
        wav_path = (self._clip[:dot] if dot > 0 else self._clip) + ".wav"
        if not cliptv_common.path_exists(wav_path):
            return
        try:
            prefs = cliptv_common.get_prefs()
            output = cliptv_common.ensure_audio_output(prefs)
            self._audio_player = cliptv_common.play_audio_clip(wav_path, output)
            if self._loop and not self._shuffle:
                self._audio_player.set_repeat(cliptv_common.ENDLESS_REPEAT_COUNT)
        except Exception as e:
            logger.warning("Could not play companion audio %s: %s", wav_path, e)

    def _next_after_end(self):
        """Handle end of file: shuffle to a new clip, rewind, or finish."""
        if self._loop and self._shuffle:
            # Re-roll a random video; try a few in case one has a bad name.
            for _ in range(5):
                next_clip = cliptv_common.pick_random_clip("video")
                if next_clip and self._open_clip(next_clip):
                    return True
            return False
        if self._loop:
            try:
                if self._mjpeg:
                    self._mj_rewind()
                else:
                    self._reader_file.seek(0)
                return True
            except OSError:
                return False
        return False

    def _show_next_frame(self, timer=None):
        if not self._reader_file:
            return
        if self._mjpeg:
            self._show_next_mjpeg_frame()
            return
        buffer = self._buffers[self._buffer_index]
        self._buffer_index = 1 - self._buffer_index
        try:
            bytes_read = self._reader_file.readinto(buffer)
            if bytes_read != len(buffer):
                if not self._next_after_end():
                    self.finish()
                    return
                buffer = self._buffers[self._buffer_index]
                self._buffer_index = 1 - self._buffer_index
                bytes_read = self._reader_file.readinto(buffer)
                if bytes_read != len(buffer):
                    # Still short after rewinding/shuffling: corrupt or empty.
                    self.finish()
                    return
        except OSError as e:
            logger.error("Video read failed: %s", e)
            self.finish()
            return
        self._status_label.set_text("")
        self._image_dsc.data = buffer
        self._image.set_src(self._image_dsc)

    def _stop_companion_audio(self):
        if self._audio_player:
            try:
                self._audio_player.stop()
            except Exception:
                pass
            self._audio_player = None

    def _close_reader(self):
        if self._reader_file:
            try:
                self._reader_file.close()
            except Exception:
                pass
            self._reader_file = None

    def _cleanup(self):
        if self._timer:
            self._timer.delete()
            self._timer = None
        self._stop_companion_audio()
        self._close_reader()
        self._buffers = None
        self._mj_buf = None
        if self._mj_drop:
            for slot in self._mj_ring:
                try:
                    self._mj_drop(slot[0])
                except Exception:
                    pass
        self._mj_ring = []

    def onStop(self, screen):
        self._cleanup()

    def onDestroy(self, screen):
        self._cleanup()
