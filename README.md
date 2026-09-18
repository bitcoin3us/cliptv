# ClipTV

A soundboard app for [MicroPythonOS](https://micropythonos.com), part of the
[ZapTV](https://www.zaptv.org) suite alongside ZapTV! and BlockTV: assign
audio and video clips to big colour-coded buttons and play them with a single
tap.

Four sounds are bundled so it plays out of the box (a 1990s phone ring, a dog,
a pig and a cat); add your own from a microSD card.

**Get it from the MicroPythonOS app store (BadgeHub project
`org.zaptv.cliptv`), or scan the QR on [www.ZapTV.org](https://www.zaptv.org#cliptv).**

## Features

- Grid of soundboard buttons; tap to play, long-press to edit. A bouncing
  equaliser shows which button is playing.
- A button plays a fixed clip, an ordered or shuffled playlist of hand-picked
  clips, or a random action: random audio clip, random video clip, or
  completely random.
- Optional per-button infinite loop: a fixed clip repeats forever; a random
  button keeps rolling a fresh clip each time one finishes. Looping buttons
  show an infinity icon; tap them again, or the stop button, to end it.
- Multiple screens of buttons with prev/next navigation; add screens with the
  + button in the assign-buttons view and remove empty ones with the - button.
- Configurable buttons per screen: 4, 6, 8, 9 or 12.
- Buttons can be renamed, colour-coded (eighteen colours) and assigned to any
  clip, bundled or from the SD card.
- Volume control in the header, remembered between sessions.
- Audio clips: WAV. Video clips: MJPEG (`.mjpeg`) or raw RGB565 (`.rgb565`),
  with an optional companion `.wav` soundtrack of the same basename.
- Settings page: assign buttons, buttons per screen, volume, DAC pin
  configuration (2" board), and an About page.

## Hardware

### Waveshare ESP32-S3-Touch-LCD-3.5

Works out of the box: sound plays through the onboard ES8311 codec and
speaker, and the TF card slot is mounted automatically at `/sdcard` (1-bit
SDMMC on GPIO 11/10/9 with its chip-select on the board's IO expander).

Playback notes for this board: ClipTV keeps the codec and I2S clocks warm for
30 s after a clip so rapid back-to-back presses play without a click (the OS
releases them on idle). MJPEG clips play at about 8 fps at 320x240 or 15 fps
at 160x120 (decoder-bound); raw RGB565 clips are scaled to fill the 480x320
screen and top out around 6 fps.

### Waveshare ESP32-S3-Touch-LCD-2 with a PCM5102A I2S DAC

All three signal pins are next to each other on the board's header and are
configurable in the app's settings:

| PCM5102A pin | Board pin | Notes                          |
| ------------ | --------- | ------------------------------ |
| VIN          | 3V3       |                                |
| GND          | GND       |                                |
| BCK          | GPIO17    | Bit clock                      |
| LRCK / LCK   | GPIO18    | Word select                    |
| DIN          | GPIO21    | Data (shared with camera I2C: do not use the camera while ClipTV uses the default pins) |
| SCK          | GND       | DAC generates the system clock internally |

Most PCM5102A breakouts also expose FLT, DMP, FMT and XSMT; the common purple
breakout already ties them correctly (FLT/DMP/FMT low, XSMT high). If yours has
them floating: FLT -> GND, DMP -> GND, FMT -> GND (I2S), XSMT -> 3V3.

The TF card slot on this board shares the LCD SPI bus (CLK=39, MOSI=38,
MISO=40, CS=41); ClipTV initialises and mounts it automatically at `/sdcard`.

## Preparing clips

Use a FAT32-formatted microSD card and put clips in `/sdcard/clips/` (any
folder works; the file picker starts there when it exists).

Audio (WAV, 16-bit PCM; mono keeps files small):

```bash
ffmpeg -i input.mp3 -ar 22050 -ac 1 -c:a pcm_s16le clips/airhorn.wav
```

Video comes in two formats. The filename must carry the dimensions and the
frame rate either way: `<name>_<W>x<H>_<fps>fps.<ext>`.

**MJPEG** (`.mjpeg`) is the recommended format: a plain stream of JPEG frames
that the firmware decodes from RAM, so files are 10 to 20 times smaller than
raw video and storage speed stops being the limit. Frames are shown at their
encoded size, centred on the screen, so encode at the size you want to see.
The decoder sets the pace: on the ESP32-S3 it manages about 8 fps at 320x240
and about 15 fps at 160x120, and ClipTV keeps a clip in real time by
skipping frames when it falls behind, so encode at those rates or lower for
smooth playback (the `-q:v` value trades quality for size; 5 to 8 is good):

```bash
ffmpeg -i input.mp4 -vf "scale=320:240,fps=8" -c:v mjpeg -q:v 7 -f mjpeg clips/dance_320x240_8fps.mjpeg
ffmpeg -i input.mp4 -vf "scale=160:120,fps=15" -c:v mjpeg -q:v 7 -f mjpeg clips/dance_160x120_15fps.mjpeg
```

**Raw RGB565** (`.rgb565`) needs no decoder (frames stream straight from
storage to the screen) and is scaled up to fill the display, but at about
460 KB/s for 160x120 @ 12 fps it is limited by storage speed:

```bash
ffmpeg -i input.mp4 -vf "scale=160:120,fps=12" -f rawvideo -pix_fmt rgb565le clips/dance_160x120_12fps.rgb565
```

Optional soundtrack for a video clip (same basename, `.wav` extension):

```bash
ffmpeg -i input.mp4 -ar 22050 -ac 1 -c:a pcm_s16le clips/dance_160x120_12fps.wav
```

Raw video is large: keep such clips short or use a big card. MJPEG clips
are small enough to keep many on the card.

The `samples/` folder contains ready-made test clips: the four bundled sounds,
a 3-second tone (`tone3s.wav`) and a bouncing-square video with soundtrack
(`bounce_160x120_12fps.rgb565` + `.wav`). Copy them to the card (or, for
desktop testing, into `internal_filesystem/data/audio/`) and assign them to
buttons.

## Installing

The app lives in `org.zaptv.cliptv/`, a standard MicroPythonOS app folder.

On a device (via `mpremote`):

```bash
mpremote cp -r org.zaptv.cliptv :/apps/
```

then reset the device (or run `AppManager.refresh_apps()`).

For desktop development, symlink the app into a MicroPythonOS checkout:

```bash
ln -s "$(pwd)/org.zaptv.cliptv" /path/to/MicroPythonOS/internal_filesystem/apps/
```

On desktop builds the app plays through the host's audio output; the PCM5102A
pin settings only appear when running on ESP32 hardware.

### Package as .mpk

From the repo root, a deterministic package for BadgeHub:

```bash
find org.zaptv.cliptv -exec touch -t 202501010000.00 {} \;
(find org.zaptv.cliptv -type d; find org.zaptv.cliptv -type f) | sort | TZ=CET zip -X -r -0 dist/org.zaptv.cliptv_<version>.mpk -@
```

## Licence

ClipTV is released under the [MIT License](LICENSE).

The bundled sounds in `org.zaptv.cliptv/clips/` carry their own licences,
listed with sources and attribution in
[`org.zaptv.cliptv/clips/README.md`](org.zaptv.cliptv/clips/README.md):
the phone ring and cat are CC0, the pig is public domain, and the dog bark is
CC BY-SA 4.0 (Dr. Nono YesMaybe, Wikimedia Commons).
