import logging
import os
import random
import sys
import time

from mpos import AudioManager, SDCardManager, SharedPreferences


logger = logging.getLogger(__name__)

APP_ID = "com.zaptv.cliptv"
APP_NAME = "ClipTV"
APP_VERSION = "0.8.0"

OUTPUT_NAME = "PCM5102A"

DEFAULT_BUTTONS_PER_SCREEN = 6
DEFAULT_SCREEN_COUNT = 1
MAX_SCREEN_COUNT = 8
DEFAULT_VOLUME = 70

# Free pins on the Waveshare ESP32-S3-Touch-LCD-2 header (GPIO21 is shared
# with the camera I2C, which ClipTV does not use).
DEFAULT_PIN_BCK = 17
DEFAULT_PIN_LRCK = 18
DEFAULT_PIN_DIN = 21

# TF card slot wiring per Waveshare board (from the schematics), keyed by the
# board module MicroPythonOS loads at boot:
#  - LCD-2:   the slot shares the LCD SPI bus, chip-select on GPIO 41.
#  - LCD-3.5: a dedicated 1-bit SDMMC interface (CLK=11, CMD=10, D0=9) whose
#             chip-select sits on the PCA9554 IO expander (EXIO3); it must be
#             high so the card starts in SD (not SPI) mode.
SD_SLOTS = {
    "mpos.board.waveshare_esp32_s3_touch_lcd_2": {"spi": True, "cs": 41},
    "mpos.board.waveshare_esp32_s3_touch_lcd_3_5": {
        "sdio": True, "clk": 11, "cmd": 10, "d0": 9, "exio_cs": 3,
    },
}

CLIPS_DIR = "/sdcard/clips"

AUDIO_EXTENSIONS = (".wav",)
VIDEO_EXTENSIONS = (".rgb565",)

# Button "action" values: instead of one fixed clip, the button picks a
# random clip of the given kind every time it is pressed (and, with loop
# enabled, every time the previous pick finishes).
ACTION_RANDOM_AUDIO = "random_audio"
ACTION_RANDOM_VIDEO = "random_video"
ACTION_RANDOM_ANY = "random_any"

ACTION_KINDS = {
    ACTION_RANDOM_AUDIO: "audio",
    ACTION_RANDOM_VIDEO: "video",
    ACTION_RANDOM_ANY: "any",
}

ACTION_LABELS = {
    ACTION_RANDOM_AUDIO: "Random audio clip",
    ACTION_RANDOM_VIDEO: "Random video clip",
    ACTION_RANDOM_ANY: "Completely random",
}

# Repeat "forever" for looping audio clips (same convention as the OS
# music player).
ENDLESS_REPEAT_COUNT = 1_000_000

# (label, hex) pairs for button colors.
COLOR_OPTIONS = [
    ("Bitcoin Orange", "f0a010"),
    ("Coral Red", "ff7f50"),
    ("Crimson", "dc143c"),
    ("Emerald", "50c878"),
    ("Forest Green", "228b22"),
    ("Goldenrod", "daa520"),
    ("Indigo", "4b0082"),
    ("Lightning Yellow", "fcc01e"),
    ("Matrix Green", "03a062"),
    ("Midnight Blue", "191970"),
    ("Moon Grey", "c0c4cc"),
    ("Nostr Purple", "ff00ff"),
    ("Piggy Pink", "ff69b4"),
    ("Sky Blue", "87ceeb"),
    ("Slate Gray", "36454f"),
    ("Teal", "008080"),
    ("Turquoise", "40e0d0"),
    ("Violet", "9f00ff"),
]
DEFAULT_COLOR = "36454f"

# buttons_per_screen -> number of grid columns
GRID_COLUMNS = {4: 2, 6: 3, 8: 4, 9: 3, 12: 4}

BUTTONS_PER_SCREEN_OPTIONS = [
    ("4 buttons (2x2)", "4"),
    ("6 buttons (3x2)", "6"),
    ("8 buttons (4x2)", "8"),
    ("9 buttons (3x3)", "9"),
    ("12 buttons (4x3)", "12"),
]


# Keys that older ClipTV versions stored as JSON numbers. The OS settings list
# reads prefs back with get_string() and passes the value to a label, so a
# number there raises a TypeError ("app threw an exception" dialog); every
# writer in this version stores strings, and this migrates existing prefs.
_NUMERIC_PREF_KEYS = ("buttons_per_screen", "screen_count", "volume",
                      "pin_bck", "pin_lrck", "pin_din")


def get_prefs():
    prefs = SharedPreferences(APP_ID)
    try:
        stale = [k for k in _NUMERIC_PREF_KEYS
                 if isinstance(prefs.data.get(k), (int, float))]
        if stale:
            editor = prefs.edit()
            for k in stale:
                editor.put_string(k, str(int(prefs.data[k])))
            editor.commit()
    except Exception as e:
        logger.warning("Pref migration skipped: %s", e)
    return prefs


def app_asset(filename):
    """LVGL 'M:' src path for a file shipped in the app folder."""
    for base in ("apps/", "builtin/apps/"):
        path = base + APP_ID + "/" + filename
        if path_exists(path):
            return "M:" + path
    return "M:apps/" + APP_ID + "/" + filename


INFINITY_ICON = "infinity_16.png"
INFINITY_ICON_SMALL = "infinity_12.png"


def get_buttons_per_screen(prefs):
    count = prefs.get_int("buttons_per_screen", DEFAULT_BUTTONS_PER_SCREEN)
    if count not in GRID_COLUMNS:
        count = DEFAULT_BUTTONS_PER_SCREEN
    return count


def get_screen_count(prefs):
    count = prefs.get_int("screen_count", DEFAULT_SCREEN_COUNT)
    return max(1, min(MAX_SCREEN_COUNT, count))


def _set_screen_count(prefs, count):
    editor = prefs.edit()
    editor.put_string("screen_count", str(count))
    editor.commit()


def add_screen(prefs):
    """Add one screen (up to MAX_SCREEN_COUNT); returns the new count."""
    count = get_screen_count(prefs)
    if count < MAX_SCREEN_COUNT:
        count += 1
        _set_screen_count(prefs, count)
    return count


def screen_has_assignments(prefs, screen_index):
    prefix = "s%d_b" % screen_index
    for key in prefs.get_dict_keys("buttons"):
        if key.startswith(prefix):
            return True
    return False


def remove_screen(prefs, screen_index):
    """Remove a screen that has no assigned buttons.

    Later screens shift down (their button keys are re-indexed). Returns
    True when the screen was removed; False when it was the only screen
    or still has buttons assigned.
    """
    count = get_screen_count(prefs)
    if count <= 1 or screen_index >= count:
        return False
    if screen_has_assignments(prefs, screen_index):
        return False
    buttons = prefs.get_dict("buttons")
    new_buttons = {}
    for key, config in buttons.items():
        try:
            screen_part, button_part = key.split("_", 1)
            screen = int(screen_part[1:])
        except (ValueError, IndexError):
            new_buttons[key] = config
            continue
        if screen > screen_index:
            new_buttons["s%d_%s" % (screen - 1, button_part)] = config
        else:
            new_buttons[key] = config
    editor = prefs.edit()
    editor.put_dict("buttons", new_buttons)
    editor.put_string("screen_count", str(count - 1))
    editor.commit()
    return True


def grid_dimensions(buttons_per_screen):
    columns = GRID_COLUMNS.get(buttons_per_screen, 3)
    rows = (buttons_per_screen + columns - 1) // columns
    return columns, rows


def button_key(screen_index, button_index):
    return "s%d_b%d" % (screen_index, button_index)


def get_button_config(prefs, screen_index, button_index):
    # Copy: get_dict_item returns the live nested dict, and mutating that
    # in place would make the later save look like a no-op (nothing written).
    return dict(prefs.get_dict_item("buttons", button_key(screen_index, button_index)))


def save_button_config(prefs, screen_index, button_index, config):
    editor = prefs.edit()
    if config:
        editor.put_dict_item("buttons", button_key(screen_index, button_index), config)
    else:
        editor.remove_dict_item("buttons", button_key(screen_index, button_index))
    editor.commit()


def is_audio_clip(path):
    return path.lower().endswith(AUDIO_EXTENSIONS)


def is_video_clip(path):
    return path.lower().endswith(VIDEO_EXTENSIONS)


def matches_kind(path, kind):
    if kind == "audio":
        return is_audio_clip(path)
    if kind == "video":
        return is_video_clip(path)
    return is_audio_clip(path) or is_video_clip(path)


BUNDLED_CLIPS_DIRNAME = "clips"


def bundled_clips_dir():
    """Filesystem path of the sample clips shipped inside the app package.

    Mirrors app_asset()'s apps/ vs builtin/apps/ resolution but returns a
    real (non-"M:") path, and only if it exists.
    """
    for base in ("apps/", "builtin/apps/"):
        path = base + APP_ID + "/" + BUNDLED_CLIPS_DIRNAME
        if path_exists(path):
            return path
    return None


def _scan_clips_dir(base, kind, clips, seen):
    """Append clips of `kind` from `base` and its immediate subdirs."""
    try:
        entries = os.listdir(base)
    except OSError:
        return
    for entry in entries:
        full = base + "/" + entry
        if matches_kind(entry, kind):
            if full not in seen:
                seen.add(full); clips.append(full)
        else:
            try:
                for sub_entry in os.listdir(full):
                    if matches_kind(sub_entry, kind):
                        sub = full + "/" + sub_entry
                        if sub not in seen:
                            seen.add(sub); clips.append(sub)
            except OSError:
                pass  # not a directory


def clips_base_dir():
    """The directory that random actions (and the file picker) draw from."""
    for base in (CLIPS_DIR, "/sdcard", "data/audio"):
        if path_exists(base):
            return base
    return None


def list_clips(kind="any"):
    """List clips of the given kind: the app's bundled sample clips plus the
    user clips dir (SD card / data) and their immediate subdirs."""
    clips = []
    seen = set()
    for base in (bundled_clips_dir(), clips_base_dir()):
        if base:
            _scan_clips_dir(base, kind, clips, seen)
    return clips


def pick_random_clip(kind="any"):
    clips = list_clips(kind)
    if not clips:
        return None
    return clips[random.randint(0, len(clips) - 1)]


def shuffled(items):
    """Return a shuffled copy (Fisher-Yates; MicroPython lacks random.shuffle)."""
    items = list(items)
    for i in range(len(items) - 1, 0, -1):
        j = random.randint(0, i)
        items[i], items[j] = items[j], items[i]
    return items


def button_is_assigned(config):
    return bool(config.get("clip") or config.get("action") or config.get("playlist"))


def resolve_button_clip(config):
    """Return the concrete clip path a button press should play, or None.

    Fixed-clip buttons return their clip, playlist buttons their first
    entry, and action buttons a fresh random pick of the action's kind.
    """
    clip = config.get("clip")
    if clip:
        return clip
    playlist = config.get("playlist")
    if playlist:
        return playlist[0]
    kind = ACTION_KINDS.get(config.get("action"))
    if kind:
        return pick_random_clip(kind)
    return None


def path_exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def color_int(hex_string, fallback=DEFAULT_COLOR):
    try:
        return int(hex_string, 16)
    except (TypeError, ValueError):
        return int(fallback, 16)


def is_dark_color(color):
    red = (color >> 16) & 0xFF
    green = (color >> 8) & 0xFF
    blue = color & 0xFF
    # Approximate perceived luminance
    return (red * 299 + green * 587 + blue * 114) // 1000 < 128


def get_pin(prefs, key, default):
    pin = prefs.get_int(key, default)
    if pin < 0 or pin > 48:
        return default
    return pin


def _init_sd_slot():
    """Initialize the TF slot of a known Waveshare board (see SD_SLOTS)."""
    for module_name, slot in SD_SLOTS.items():
        board = sys.modules.get(module_name)
        if not board:
            continue
        if slot.get("sdio"):
            expander = getattr(board, "expander", None)
            if expander is not None and "exio_cs" in slot:
                expander.set_output(slot["exio_cs"], True)
            SDCardManager.init(
                mode="sdio", clk_pin=slot["clk"], cmd_pin=slot["cmd"],
                d0_pin=slot["d0"], width=1,
            )
            return True
        spi_bus = getattr(board, "spi_bus", None)
        if spi_bus:
            SDCardManager.init(spi_bus=spi_bus, cs_pin=slot["cs"])
            return True
    return False


def ensure_sdcard():
    """Mount the SD card, initializing the Waveshare TF slot if needed."""
    if SDCardManager.get_raw() is None and sys.platform == "esp32":
        try:
            _init_sd_slot()
        except Exception as e:
            logger.warning("Could not init SD card: %s", e)
    try:
        return SDCardManager.mount()
    except Exception as e:
        logger.warning("Could not mount SD card: %s", e)
        return False


def get_i2s_pins(prefs):
    return {
        "sck": get_pin(prefs, "pin_bck", DEFAULT_PIN_BCK),
        "ws": get_pin(prefs, "pin_lrck", DEFAULT_PIN_LRCK),
        "sd": get_pin(prefs, "pin_din", DEFAULT_PIN_DIN),
    }


# Onboard ES8311 codec + speaker on the Waveshare ESP32-S3-Touch-LCD-3.5
# (pin map from the vendor's board support: MCLK=12, BCLK=13, WS=15, DOUT=16).
WAVESHARE_3_5_BOARD_MODULE = "mpos.board.waveshare_esp32_s3_touch_lcd_3_5"
ES8311_OUTPUT_NAME = "ES8311 Speaker"
ES8311_I2S_PINS = {"mck": 12, "sck": 13, "ws": 15, "sd": 16}
ES8311_I2C_ADDR = 0x18
# Same setting the board support uses: the driver's 85% default is audibly
# distorted through the speaker connector, 76% is the loudest clean level.
ES8311_DAC_VOLUME = 76


class _CodecI2C:
    """Adapt the lcd_bus i2c Device to the machine.I2C-style API that
    MicroPythonOS's drivers.codec.es8311 driver expects."""

    def __init__(self, bus, dev_id):
        import i2c as i2c_mod

        self._dev = i2c_mod.I2C.Device(bus=bus, dev_id=dev_id, reg_bits=8)

    def writeto_mem(self, addr, reg, data):
        self._dev.write_mem(reg, data)

    def readfrom_mem_into(self, addr, reg, buf):
        self._dev.read_mem(reg, buf=buf)


def _es8311_present(codec_i2c):
    """Chip-ID check (0xFD/0xFE == 0x83/0x11) without touching any config."""
    try:
        buf = bytearray(1)
        codec_i2c.readfrom_mem_into(ES8311_I2C_ADDR, 0xFD, buf)
        hi = buf[0]
        codec_i2c.readfrom_mem_into(ES8311_I2C_ADDR, 0xFE, buf)
        return hi == 0x83 and buf[0] == 0x11
    except Exception as e:
        logger.warning("ES8311 not detected: %s", e)
        return False


def _ensure_es8311_output():
    """Register the 3.5" board's onboard codec speaker, when present.

    MicroPythonOS 0.18+ registers the codec itself (as "Speaker") from the
    board support, so defer to any existing output already claiming the
    codec's I2S pins. Older firmware gets the same setup done here, using
    the OS's own drivers.codec.es8311 driver rather than a private copy.
    """
    for existing in AudioManager.get_outputs():
        if existing.kind == "i2s" and existing.i2s_pins.get("ws") == ES8311_I2S_PINS["ws"]:
            return existing
    output = AudioManager.find_output_by_name(ES8311_OUTPUT_NAME)
    if output:
        return output
    board = sys.modules.get(WAVESHARE_3_5_BOARD_MODULE)
    i2c_bus = getattr(board, "i2c_bus", None) if board else None
    if not i2c_bus:
        return None
    try:
        from drivers.codec.es8311 import ES8311
    except ImportError as e:
        logger.warning("No ES8311 driver in this firmware: %s", e)
        return None
    try:
        codec_i2c = _CodecI2C(i2c_bus, ES8311_I2C_ADDR)
        if not _es8311_present(codec_i2c):
            return None
        codec = ES8311(codec_i2c)  # runs the driver's init sequence
        codec.set_dac_volume(ES8311_DAC_VOLUME)
        codec.dac_mute(True)

        def _on_open():
            time.sleep_ms(10)
            codec.dac_mute(False)

        def _on_close():
            codec.dac_mute(True)
            time.sleep_ms(20)

        return AudioManager.add(AudioManager.Output(
            ES8311_OUTPUT_NAME,
            "i2s",
            channels=1,
            i2s_pins=dict(ES8311_I2S_PINS),
            on_open=_on_open,
            on_close=_on_close,
        ))
    except Exception as e:
        logger.error("Could not register %s: %s", ES8311_OUTPUT_NAME, e)
        return None


def ensure_audio_output(prefs):
    """Return the audio output for clip playback.

    On ESP32 this registers the onboard ES8311 codec speaker when the board
    has one, plus the external PCM5102A I2S DAC output with the configured
    pins; the OS audio settings pick between them (the codec speaker is
    preferred by default). On other platforms the platform default is used.
    """
    if sys.platform != "esp32":
        return AudioManager.get_default_output()

    # Register the onboard speaker first so it becomes the default.
    _ensure_es8311_output()

    pins = get_i2s_pins(prefs)
    output = AudioManager.find_output_by_name(OUTPUT_NAME)
    if output:
        output.i2s_pins = dict(pins)
    else:
        try:
            AudioManager.add(
                AudioManager.Output(OUTPUT_NAME, "i2s", channels=2, i2s_pins=pins)
            )
        except Exception as e:
            logger.error("Could not register %s output: %s", OUTPUT_NAME, e)

    return AudioManager.get_default_output()


def apply_volume(prefs):
    AudioManager.set_volume(prefs.get_int("volume", DEFAULT_VOLUME))


def play_audio_clip(path, output, on_complete=None):
    """Stop any current playback and play the given WAV file."""
    AudioManager.stop()
    player = AudioManager.player(
        file_path=path,
        stream_type=AudioManager.STREAM_MUSIC,
        output=output,
        on_complete=on_complete,
    )
    player.start()
    return player
