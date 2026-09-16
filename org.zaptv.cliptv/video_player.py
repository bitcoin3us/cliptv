import logging

import lvgl as lv

from mpos import Activity, DisplayMetrics

import cliptv_common
import cliptv_grid

logger = logging.getLogger(__name__)

DEFAULT_FPS = 12
MIN_FPS = 1
MAX_FPS = 30
BYTES_PER_PIXEL = 2  # RGB565


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
    """Plays a raw RGB565 video clip (.rgb565) fullscreen.

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
                self._reader_file.seek(0)
                return True
            except OSError:
                return False
        return False

    def _show_next_frame(self, timer=None):
        if not self._reader_file:
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

    def onStop(self, screen):
        self._cleanup()

    def onDestroy(self, screen):
        self._cleanup()
