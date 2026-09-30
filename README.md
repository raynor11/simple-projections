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

1. Flash **Raspberry Pi OS Lite (64-bit)** and enable SSH.
2. Clone the repo and run the setup script as your normal user:
   ```bash
   git clone <repo> projection-mapper
   cd projection-mapper
   ./scripts/setup_pi.sh
   cp config/frames.example.json config/frames.json
   sudo reboot
   ```

The setup script:
- installs the packages;
- sets HDMI to 1080p (the projector upscales to 4K; the Pi 4 can't render live frames at 4K);
- configures the AirPlay loopback device, HDMI audio and NUT for the UPS;
- installs and enables the three services.

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
- While dragging, frames snap to the screen's edges and center and to other frames' edges; a dashed guide shows what they snapped to. Hold **Alt** to drag without snapping.
- The toolbar across the top has a button for every action, and each button shows its shortcut key.

**Keyboard**

| Key | Action |
|---|---|
| `Tab` / `Shift+Tab` | Select the next / previous frame |
| Arrows | Move the frame (`Shift` for bigger steps) |
| `Ctrl`/`Alt` + Arrows | Resize the frame |
| `Ctrl+Z` / `Ctrl+Y` (or `Ctrl+Shift+Z`) | Undo / redo |
| `p` | Edit the frame's portrait layout (`rect_portrait`) |
| `r` | Preview rule states: idle / casting / night. Edits go to whichever rule is active in that state. |
| `e` | Screen mode: drag the numbered corner handles, or press `1`–`4` and use the arrows |
| `a` | Auto-detect the screen with the camera |
| `n` / `d` | Add a text frame / delete the selected frame |
| `s` / `l` | Save / reload the config |
| `+` / `-` | Bigger / smaller interface text |
| `h` | Hide / show the help panel |
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
- `rect` and `rect_portrait`;
- `corners`;
- `source`: a partial source merges into the frame's source (e.g. `{"text": "Now casting"}`); a source with a different `type` replaces it.

## Projector control

The `projector` section of the config:

| Key | Meaning |
|---|---|
| `serial`, `baud` | The RS-232 adapter's port and speed (ViewSonic's default is 19200) |
| `light_source_opcode` | The two command bytes for light source mode. Defaults to `[17, 16]` (0x11 0x10), as on ViewSonic's 4K PX7xx series. ViewSonic's generic document uses `[17, 12]` instead. |
| `light_source_modes` | Mode name → value. Defaults: `normal` 0, `eco` 1, `dynamic_eco` 2, `supereco` 3. |
| `brightness` | The light source mode for `day`, `dusk` and `night`, plus `twilight_offset_min` (default 30) |
| `power_loss_debounce_s` | How long power must stay off before shutting down (default 10) |
| `projector_off_timeout_s` | Shut down anyway if the projector hasn't reported off by then (default 90) |
| `ups` | NUT's name for the UPS (default `apc@localhost`) |
| `power_on_at_boot` | Turn the projector on at boot (default true) |

"Dusk" covers civil twilight. It also includes the last `twilight_offset_min` of daylight before sunset and the first after sunrise.

**Check the commands against the LS740-4K before relying on them.** The light-source opcode differs between ViewSonic models; confirm it against the LS740-4K's RS-232 table. Test each command with the service stopped:

```bash
sudo systemctl stop projector-control
.venv/bin/python projector_control.py --test status
.venv/bin/python projector_control.py --test light-source          # read the current mode
.venv/bin/python projector_control.py --test light-source eco      # set a mode
.venv/bin/python projector_control.py --test off
.venv/bin/python projector_control.py --test on
.venv/bin/python projector_control.py --test raw "07 14 00 05 00 34 00 00 11 00 5E"
.venv/bin/python projector_control.py --test ups
```

### Power loss sequence

1. The wall switch is turned off, and the UPS goes on battery.
2. After 10 s (so flicking the switch off and on does nothing), the projector is sent power-off.
3. When the projector reports it's off (or after the timeout), NUT shuts the Pi down.
4. About 20 s after the Pi halts, the UPS cuts its output.
5. When the switch is turned back on, the UPS restores its output 30 s later. The Pi boots, turns the projector on and sets its brightness.

If power returns during steps 1–2, the projector is turned back on and nothing shuts down. NUT's standard low-battery shutdown remains as a safety net.

## Hardware notes

- **UPS load:** the BE600M1 is rated for 330 W. Check the projector's rated power draw: if the projector plus the Pi exceed about 330 W, the UPS will overload the moment it switches to battery. Plug both into the **battery-backed** outlets, not the surge-only ones. The Pi's own power supply must be on the UPS too; the USB cable is only for data.
- **Projector settings:**
  - Enable RS-232 control in standby (the projector's standby/power settings), or the power-on command at boot is ignored.
  - Leave "Direct Power On" off, so the Pi decides when the projector turns on.
  - Set the projector's baud rate to match `baud`.
- **RS-232:** the Pi has no serial port, so use a USB-to-RS232 adapter (FTDI-based). Use a straight-through or null-modem cable according to the projector's port pinout.
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
- [ ] The projector commands work with `projector_control.py --test`, including the light-source opcode.
- [ ] The UPS power cycle works:
  - `upscmd -l apc@localhost` lists `shutdown.return`;
  - the full power-loss sequence runs;
  - turning the switch back on *during* the Pi's shutdown still brings it back.
- [ ] With the camera, weather, text and one active cast, the logged FPS stays at 25 or more (`journalctl -u projection-mapper -f`).

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
