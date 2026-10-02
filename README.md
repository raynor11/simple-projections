# Projection Mapper

Projects a layout of live frames onto a projector screen from a Raspberry Pi 4. The frames can show weather, text, a camera, a Chromecast or AirPlay. The Pi also runs the projector: power follows the wall switch through the UPS, and brightness follows the time of day.

Built for this setup:

- Raspberry Pi 4B running Raspberry Pi OS Lite 64-bit (Bookworm)
- ViewSonic LS740-4K laser projector, about 13 ft back, projecting onto a 100" 16:9 screen (white, black border)
- One USB webcam: finds the screen, and can also be shown as a frame
- USB HDMI capture card with a Chromecast plugged into it
- APC BE600M1 UPS (USB to the Pi) on a switched wall outlet
- USB-to-RS232 adapter to the projector

## Features

- **Screen detection:** the webcam finds the screen, and frames are laid out as rectangles on it. The keystone correction is exact (per-pixel perspective warp). If the projector moves, re-run detection and the layout stays put.
- **Frame types:** video/image files, text, weather (with icons, for your local area), a live camera, Chromecast and AirPlay.
- **Casting:** a Chromecast or AirPlay frame appears when someone starts casting and hides when they stop. Black bars are cropped, and portrait content switches to a portrait layout. Cast audio plays through the projector.
- **Frame rules:** frames can change while something else is happening, for example "shrink the weather while casting" or "hide the message at night".
- **Live config:** edit `config/frames.json` over SSH and the running display picks up the change.
- **Power:** turning the wall switch off turns the projector off, then shuts the Pi down cleanly. Turning it on brings everything back up.
- **Brightness:** the projector's light source mode follows sunrise and sunset.

## How it runs

Three systemd services:

| Service | What it does |
|---|---|
| `projection-mapper` | Draws the frames (`main.py --play`); detects casting; plays Chromecast audio |
| `uxplay` | AirPlay receiver; writes video to `/dev/video10` and audio to HDMI |
| `projector-control` | RS-232 projector control, UPS monitoring, power-loss shutdown, brightness schedule |

## Installation

### Raspberry Pi

1. **Flash Raspberry Pi OS Lite (64-bit)** with Raspberry Pi Imager: *Raspberry Pi OS (other) → Raspberry Pi OS Lite (64-bit)*. In Imager's settings, enable SSH and set your Wi-Fi, user and password. A card that came with 32-bit or desktop Pi OS must be reflashed; the setup script refuses to run on 32-bit.
2. **Cooling:** fit the heatsinks and plug the case fan into the Pi's 5 V and ground pins, so it runs whenever the Pi is on. Sustained video decoding throttles a passively cooled Pi 4.
3. Clone the repo and run the setup script as your normal user:
   ```bash
   git clone <repo> projection-mapper
   cd projection-mapper
   ./scripts/setup_pi.sh
   cp config/frames.example.json config/frames.json
   sudo reboot
   ```

The setup script:
- installs the packages, including the hardware video decoding pieces;
- forces the HDMI output to 1080p60, and keeps it on even while the projector is in standby at boot;
- turns off Bluetooth, the boot splash and unused services, and swaps to compressed RAM (zram) instead of the SD card;
- configures the AirPlay loopback device, HDMI audio and NUT for the UPS;
- installs and enables the three services, with the display given CPU priority.

The projector scales 1080p up to its 4K output. Rendering at 4K would be four times the work for the Pi 4. Once everything runs well at 1080p, you can try 4K at 30 Hz: change the `video=` entry in `/boot/firmware/cmdline.txt` to `3840x2160@30D`, set the config's `canvas` to 3840×2160, re-run screen detection, and compare `scripts/benchmark.py` results and temperatures. It only sharpens text and weather; video gains nothing.

### macOS (development)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py --play --windowed --config config/frames.dev.json
```

On a Mac, a camera `device` can be an index like `0`. Chromecast, AirPlay, audio and the UPS only work on the Pi.

## First-time setup on site

1. **Find your devices** and put their stable paths in `config/frames.json`:
   ```bash
   ls /dev/v4l/by-id/          # webcam and capture card (use the -video-index0 entries)
   ls /dev/serial/by-id/       # USB-RS232 adapter
   arecord -l                  # capture card audio device, e.g. hw:CARD=MS2109
   v4l2-ctl -d <device> --list-formats-ext   # supported capture modes
   ```
   Also set the Chromecast's name as it appears in the Google Home app (`cast_name`).
   The location for weather and sunrise/sunset is looked up automatically (see [Configuration](#configuration)).
2. **Check the projector link** (see [Projector control](#projector-control)):
   ```bash
   sudo systemctl stop projector-control
   .venv/bin/python projector_control.py --test status
   ```
3. **Detect the screen.** Point the webcam at the screen, then:
   ```bash
   sudo systemctl stop projection-mapper
   .venv/bin/python main.py --detect-screen
   ```
   It projects white, black and a chessboard, then shows a green outline of the screen it found. If the outline sits on the inner edge of the black border, press **Enter** to save; press **Esc** to discard. The camera photos go to `captures/` if you need to troubleshoot.
4. **Lay out the frames** with `python main.py --calibrate` (controls below). Press `s` to save.
5. Start everything again: `sudo systemctl start projection-mapper projector-control`.

A webcam aimed at the screen will show the projection itself in its camera frame (an "infinite mirror" effect). Re-aim it after detection, or place that frame deliberately.

## Calibration

`python main.py --calibrate` (add `--windowed` on a Mac; the window can be resized). Everything works with the mouse, the keyboard, or both.

**Mouse**
- Click a frame to select it, and drag it to move it.
- Drag the square handles on its corners and edges to resize it.
- Scroll to make it bigger or smaller.
- For a shape that isn't a rectangle (a trapezoid, a rhombus, or any four-cornered shape), click **Warp corners** (or press `W`). Then drag the numbered corners one at a time. The content is warped to fit exactly. Click it again to go back to a rectangle (the shape's bounding box).
- While dragging, frames snap to the screen's edges and center and to other frames' edges; a dashed guide shows what they snapped to. Hold **Alt** to drag without snapping.
- The toolbar across the top has a button for every action, and each button shows its shortcut key.

**Keyboard**

| Key | Action |
|---|---|
| `Tab` / `Shift+Tab` | Select the next / previous frame |
| Arrows | Move the frame (`Shift` for bigger steps) |
| `Ctrl`/`Alt` + Arrows | Resize the frame |
| `Ctrl+Z` / `Ctrl+Y` (or `Ctrl+Shift+Z`) | Undo / redo |
| `w` | Warp corners: switch the frame between a rectangle and four free corners |
| `1`–`4`, then arrows | On a warped frame, move one corner; `Ctrl`/`Alt` + arrows move the whole frame |
| `p` | Edit the frame's portrait layout (`rect_portrait`, rectangles only) |
| `r` | Preview rule states: idle / casting / night. Edits go to whichever rule is active in that state. |
| `e` | Screen mode: drag the numbered corner handles, or press `1`–`4` and use the arrows |
| `a` | Auto-detect the screen with the camera |
| `n` / `d` | Add a text frame / delete the selected frame |
| `s` / `l` | Save / reload the config |
| `+` / `-` | Bigger / smaller interface text |
| `h` | Hide / show the help panel |
| `t` | Test pattern: near-black and near-white steps, for checking the projector's color range |
| `Esc` / `q` | Quit. With unsaved changes you're asked to press it again (or `s` to save). |

**Accessibility**
- Nothing is shown by color alone. The selected frame is filled and has a thicker outline; hidden frames (such as a cast frame that isn't casting in the previewed state) are dashed and labelled "hidden".
- Corner handles are numbered 1–4 to match their keys, and the active one is larger.
- Buttons that are switched on are light with dark text.
- The colors come from the Okabe-Ito palette, which stays distinguishable with the common kinds of color blindness.
- Messages such as "Saved" or detection errors appear on screen, not just in the terminal.

## Configuration

`config/frames.json` (start from [`config/frames.example.json`](config/frames.example.json)). While running, the display and projector control reload it automatically when it changes. If an edit is invalid, it's reported in the logs and the running config stays in effect.

| Key | Meaning |
|---|---|
| `canvas` | Output size in pixels (1920×1080). Changing it needs a restart. |
| `location` | For weather and sunrise/sunset, most to least precise: `{"lat": ..., "lon": ..., "timezone": "America/Los_Angeles"}`; `{"address": "123 Main St, Springfield, IL 62701"}`, which is looked up once (US Census geocoder, or OpenStreetMap for non-US addresses) and cached in `config/location.address.json` until the address changes; or `"auto"` (or leave it out), which uses your internet connection's IP address, is accurate to about city level (wrong behind a VPN) and is cached for a week. |
| `screen.corners` | Screen corners in canvas pixels, written by `--detect-screen`. Without it, the whole canvas counts as the screen. |
| `detection.camera` | Camera used by `--detect-screen`. Defaults to the first `camera` frame's device. |
| `frames` | Frames, drawn in list order (later frames are drawn on top) |
| `projector` | RS-232 projector control (see below) |

### Frames

```json
{"id": "weather", "label": "Weather", "rect": [0.02, 0.04, 0.3, 0.45], "source": {"type": "weather"}}
```

- `rect` is `[x, y, width, height]` as fractions of the screen, from its top-left corner.
- `rect_portrait` (optional) is used instead when a cast is showing portrait content.
- A frame can be warped into any four-cornered shape with `"quad": {"tl": [x, y], "tr": [x, y], "br": [x, y], "bl": [x, y]}` instead of `rect`. The corners are fractions of the screen, just like `rect`, so a warped frame still follows the screen when you re-run detection. Each frame has exactly one of `rect`, `quad` or the older pixel `corners`.
- Older configs with pixel `corners` and a `media` path still work.

| Source `type` | Fields |
|---|---|
| `file` | `path` to a video (mp4/mov/avi/mkv/webm; loops) or an image |
| `text` | `text` (`\n` for line breaks), `color`, `background` (`#rrggbbaa`), `align` (`left`/`center`/`right`), `font` (path to a .ttf). The text auto-sizes to fill the frame. |
| `weather` | `units` (`imperial`/`metric`), optional `title`, optional `lat`/`lon`. Shows the current conditions with an icon (day/night aware), today's high and low, and a 3-day forecast with icons. Uses Open-Meteo; no API key needed. |
| `camera` | `device`, `width`, `height`, `fps` |
| `chromecast` | `device` (capture card), `cast_name`, `width`, `height`, `audio_device`, `audio_delay_ms` (lip-sync, default 120), `grace_seconds` (default 3), `idle_app_ids` |
| `airplay` | `device` (`/dev/video10`), `port` (7000), `grace_seconds` |

### Rules

```json
"rules": [
  {"when": {"active_any": ["cc", "air"]}, "set": {"rect": [0.02, 0.04, 0.15, 0.25]}},
  {"when": {"between": ["23:00", "06:00"]}, "set": {"hidden": true}}
]
```

- Rules are checked top to bottom, and the first one that matches wins. If none match, the frame uses its normal settings.
- All the conditions inside one `when` must be true.
- A frame counts as "active" while its cast is showing.

| Condition | True when |
|---|---|
| `active_any: [ids]` | any of these frames is active |
| `active_all: [ids]` | all of these frames are active |
| `inactive_all: [ids]` | none of these frames are active |
| `between: ["HH:MM", "HH:MM"]` | the local time is in this window (it can wrap past midnight) |

`set` can change these fields:
- `hidden`;
- `rect`, `rect_portrait` and `quad` (a rule that sets a shape replaces the frame's own shape);
- `corners`;
- `source`: a partial source merges into the frame's source (e.g. `{"text": "Now casting"}`); a source with a different `type` replaces it.

## Projector control

The RS-232 commands come from the LS740-4K user guide's command table (pp. 57–69). The `projector` section of the config:

| Key | Meaning |
|---|---|
| `serial`, `baud` | The RS-232 adapter's port and speed. The LS740-4K defaults to **115200** (8N1); its menu also offers 9600. |
| `input` | The input the Pi is plugged into: `hdmi1` (default) or `hdmi2`. Selected at every power-on. |
| `aspect` | Set at every power-on: `16:9` (default), `auto`, `native`, `4:3` or `21:9` |
| `volume` | Optional speaker volume, 0–10, set at every power-on (cast audio plays through the projector) |
| `brightness` | The light source mode for `day`, `dusk` and `night`: `normal`, `eco` (about 20% dimmer) or `dynamic_black` (adapts to content). Also `twilight_offset_min` (default 30) and `software_dim` (below). |
| `power_loss_debounce_s` | How long power must stay off before shutting down (default 10) |
| `projector_off_timeout_s` | Shut down anyway if the projector hasn't reported off by then (default 90) |
| `ups` | NUT's name for the UPS (default `apc@localhost`) |
| `power_on_at_boot` | Turn the projector on at boot (default true) |

"Dusk" covers civil twilight. It also includes the last `twilight_offset_min` of daylight before sunset and the first after sunrise.

**Dimming at night:** over RS-232 the LS740-4K only switches between Normal and Eco. Its finer *Light Source Power 50–100%* setting exists only in the on-screen menu. To dim further at night, add `"software_dim": {"day": 1.0, "dusk": 0.9, "night": 0.75}` to `brightness`; the display scales its own output by that factor.

**At power-on**, projector-control waits for the projector to finish warming up (it ignores most commands until then). It then selects the input, sets the aspect ratio and volume, and applies the brightness mode. It also logs the light-source hours once a day.

Test the commands with the service stopped:

```bash
sudo systemctl stop projector-control
.venv/bin/python projector_control.py --test status
.venv/bin/python projector_control.py --test on                    # then status: warming, then on
.venv/bin/python projector_control.py --test light-source          # read: normal / eco / dynamic_black / custom_power
.venv/bin/python projector_control.py --test light-source eco      # set a mode
.venv/bin/python projector_control.py --test input hdmi1
.venv/bin/python projector_control.py --test aspect 16:9
.venv/bin/python projector_control.py --test volume 5
.venv/bin/python projector_control.py --test blank on              # AV mute (and blank off)
.venv/bin/python projector_control.py --test hours
.venv/bin/python projector_control.py --test off
.venv/bin/python projector_control.py --test raw "07 14 00 05 00 34 00 00 11 00 5E"
.venv/bin/python projector_control.py --test ups
```

`custom_power` means the mode was set to a Light Source Power percentage in the menu. A warning about a reply checksum mismatch is harmless: the manual's own example reply for Normal mode has one.

## Projector setup (LS740-4K menu)

Set these once on the projector itself (Menu button). They keep the projector's own processing out of the way of our warping, and leave power and input to the Pi.

| Menu | Setting | Why |
|---|---|---|
| Display → **Ultra Fast Input** | **Active** | Resets and disables the projector's Keystone, Four Corners, Warping, Aspect, Zoom and Image Shift, so only our warp applies. Also lowest input lag (4.2 ms). |
| Display → Image Settings → Color Settings → **Color Space** | Auto, then check | The Pi's 1080p60 is a TV timing that may be sent as limited range. Check it with the test pattern (`--calibrate`, press **T**): every near-black and near-white step should be distinct and 0 should be true black. If black looks grey, choose RGB (16–235); if dark steps merge, RGB (0–255). |
| Setup → Power Settings → **Direct Power On** / **Signal Power On** | **Off** / **Off** | The Pi turns the projector on over RS-232 |
| Setup → Power Settings → **Auto Power Off** | **0** (disabled) | A dark screen or a Pi restart shouldn't turn it off |
| Setup → Power Settings → **Power Mode (Standby)** | Eco; ErP Off if needed | If the projector doesn't respond to `--test on` from standby in Eco, use ErP Off |
| Setup → **HDMI CEC** | **Off** | Avoids CEC fighting the RS-232 control |
| Setup → Options → **Auto Source** | **Off** | The Pi selects the input |
| Setup → Options → **Splash Screen** | **Neutral** | Black instead of a logo at power-on |
| Setup → Options → **Baud Rate** | **115200** | Matches `baud` |
| Setup → **Projection** | DeskFront or CeilingFront | Matches how it's mounted |

**Placement:** a 100" 16:9 screen needs 122–195 in of throw (UG p.18), so 13 ft (156 in) works. Use the zoom ring so the image just overfills the screen onto its black border on every side. Screen detection needs the border lit, and nearly all of the Pi's output pixels then land on the screen. Focus on a test pattern.

### Power loss sequence

1. The wall switch is turned off, and the UPS goes on battery.
2. After 10 s (so flicking the switch off and on does nothing), the projector is sent power-off.
3. When the projector reports it's off (or after the timeout), NUT shuts the Pi down.
4. About 20 s after the Pi halts, the UPS cuts its output.
5. When the switch is turned back on, the UPS restores its output 30 s later. The Pi boots, turns the projector on and sets its brightness.

If power returns during steps 1–2, the projector is turned back on and nothing shuts down. NUT's standard low-battery shutdown remains as a safety net.

## Hardware notes

- **UPS load:** the LS740-4K draws 165–210 W and the Pi under 10 W, comfortably within the BE600M1's 330 W. Plug both into the **battery-backed** outlets, not the surge-only ones. The Pi's own power supply must be on the UPS too; the USB cable is only for data.
- **Projector settings:** see [Projector setup](#projector-setup-ls740-4k-menu).
- **RS-232:** the Pi has no serial port, so use a USB-to-RS232 adapter (FTDI-based) with a DB-9 male plug. The projector's port is DB-9 female with RX on pin 2, TX on pin 3 and ground on pin 5 (UG p.57), so a standard **straight-through** cable is correct (not null-modem).
- **Capture card:**
  - 720p MJPEG is recommended on a Pi 4.
  - Put the capture card and the webcam on separate USB 3 ports.
  - **HDCP:** Netflix and other protected Chromecast content will show black through a capture card unless an HDCP-stripping HDMI splitter sits between the Chromecast and the card.
- **Network:** Chromecast detection and AirPlay need the Pi on the same network and subnet as the phones, with mDNS (multicast) allowed.
- **Audio:** HDMI audio isn't shared (no mixing). While both the Chromecast and AirPlay are playing, only one of them is heard; the other retries until the device is free.

### Verify on the Pi

These parts can't be tested off the hardware:
- [ ] The GL context comes up under kmsdrm. The service sets `MESA_GL_VERSION_OVERRIDE=3.3`; the renderer also falls back to GL 3.1.
- [ ] UxPlay accepts the multi-element `-vs` pipeline in `scripts/uxplay.service`, and frames appear on `/dev/video10`. If not, change `-vs` to just `v4l2sink device=/dev/video10`: portrait phones will then change the stream size, which v4l2loopback may not handle.
- [ ] The projector commands work with `projector_control.py --test` at 115200 baud, including power-on from standby (note which Power Mode setting it needs).
- [ ] The UPS power cycle works:
  - `upscmd -l apc@localhost` lists `shutdown.return`;
  - the full power-loss sequence runs;
  - turning the switch back on *during* the Pi's shutdown still brings it back.
- [ ] With the camera, weather, text and one active cast, the logged FPS stays at 25 or more (`journalctl -u projection-mapper -f`).

## Performance on the Pi

**Prepare videos on the Mac before copying them to the Pi:**

```bash
scripts/prepare_media.sh ~/Movies/clip.mov                  # -> media/clip.mp4, 1080p30 H.264
WIDTH=1280 scripts/prepare_media.sh ~/Movies/clip.mov       # smaller, for a smaller frame
```

The Pi 4 decodes H.264 in hardware; other codecs (HEVC, AV1, VP9) and 4K files fall back to slow software decoding. The script needs `ffmpeg` (`brew install ffmpeg`). Sizing a video close to its frame's on-screen size also cuts upload work.

**Decoders.** At startup each video logs which decoder it got: `hardware (v4l2h264dec + v4l2convert)` on the Pi, or `software (FFmpeg)`. Cameras and the capture card use OpenCV's V4L2 capture by default. Add `"decoder": "gstreamer"` to a camera or Chromecast source to try the Pi's hardware JPEG decoder; it falls back automatically if it's unavailable. Capture defaults to 1280×720; set `width`/`height` lower for small frames.

**Stats.** Playback logs a line every 10 s:

```
FPS 29.8 | tick 1.6/3.3ms (avg/p95) | upload 2.7ms + draw 0.1ms per frame | 185MB/s uploaded | 0.0 dropped/s | CPU 83% | RSS 407MB | 52°C | throttled=0x0
```

- CPU is a percentage of one core.
- `throttled` other than `0x0` means undervoltage or overheating (check the power supply and the fan).
- `python main.py --play --stats` also shows these numbers on screen.

The display only redraws when something on it changes, so text and weather alone show an FPS near zero.

**Benchmark.** Run it before and after changes:

```bash
python scripts/benchmark.py all --seconds 30 --json results.json     # add --fullscreen on the Pi
```

| Scenario | What it shows |
|---|---|
| `static` | Text and weather only |
| `video` | Plus a looping 1080p video |
| `typical` | Plus a second video standing in for a cast |
| `stress` | Plus a camera-sized video and another text frame |

To compare options on the Pi:
- `PM_VIDEO_DECODER=software` forces software decoding.
- `PM_UPLOAD_RGBA=1` uploads 4-channel textures instead of 3.

## Operating

```bash
sudo systemctl status projection-mapper uxplay projector-control
journalctl -u projection-mapper -f       # display logs (FPS every 10 s)
journalctl -u projector-control -f       # power/brightness logs
sudo systemctl restart projection-mapper
```

## Development

```bash
pip install -r requirements.txt
pytest tests/
```

| Path | Contents |
|---|---|
| `main.py` | Entry point: `--play`, `--calibrate`, `--detect-screen` |
| `projector_control.py` | The projector/UPS control service |
| `src/renderer.py` | OpenGL drawing with a per-pixel perspective warp; textures persist between frames |
| `src/playback.py` | Maps frames onto the screen; fades and slides; applies rules and hot reload |
| `src/rules.py` | Frame rules |
| `src/sources/` | `file`, `text`, `weather`, `v4l2` (camera), `letterbox`, `cast`, `chromecast`, `airplay`, `audio` |
| `src/screen_detect.py` | Camera-based screen detection |
| `src/calibration.py` | The calibration UI |
| `src/homography.py` | Perspective math |
| `src/config_io.py` | Config loading, validation, migration and file watching |
| `src/projector/` | ViewSonic RS-232 control, UPS power sequencing, brightness schedule |
| `scripts/` | Pi setup script, systemd units, NUT/ALSA/v4l2loopback config |

## License

See LICENSE file.
