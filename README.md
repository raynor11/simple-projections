# Projection Mapper

A lightweight, cross-platform projection mapping tool for Raspberry Pi and macOS. Warps multiple video/image sources onto a physical display using 4-point perspective correction.

## Features

- **Multi-frame support**: Map multiple independent video/image sources to different screen regions
- **Interactive calibration**: Real-time keyboard-driven UI to adjust frame corners
- **Cross-platform**: Runs identically on macOS (for development) and Raspberry Pi (for deployment)
- **Headless deployment**: Auto-starts on boot via systemd, runs unattended
- **Portrait orientation support**: Optimized for projectors in portrait mode

## Requirements

- Python 3.11+
- macOS or Raspberry Pi OS (64-bit)
- For video playback: ffmpeg/libmpv
- For OpenGL: mesa drivers

## Installation

### macOS (Development)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Raspberry Pi (Production)

```bash
cd /home/pi
git clone <repo> projection-mapper
cd projection-mapper
chmod +x scripts/setup_pi.sh
./scripts/setup_pi.sh
```

## Usage

### Calibration Mode

Interactively adjust frame corners:

```bash
python main.py --calibrate --windowed
```

**Controls:**
- `Tab` / `Shift+Tab`: cycle frames
- `1`–`4`: select corner (TL, TR, BR, BL)
- Arrow keys: nudge corner by 1px (Shift+Arrow for 10px)
- `n`: add new frame
- `d`: delete current frame
- `s`: save config
- `l`: reload config from disk
- `Esc` / `q`: quit

### Playback Mode

```bash
python main.py --play
```

On Raspberry Pi, this runs fullscreen. On macOS, use `--windowed` for dev testing.

## Configuration

Frame configuration is stored in `config/frames.json`:

```json
{
  "canvas": {
    "width": 1080,
    "height": 1920,
    "orientation": "portrait"
  },
  "frames": [
    {
      "id": "frame_1",
      "label": "Upper left",
      "media": "media/video.mp4",
      "corners": {
        "tl": [120, 200],
        "tr": [420, 210],
        "br": [415, 610],
        "bl": [115, 600]
      }
    }
  ]
}
```

- `canvas`: Output resolution and orientation
- `frames[].media`: Path to video/image file
- `corners`: 4-point quad in canvas pixel space

### Supported Media

- Video: MP4, MOV, AVI, MKV, WebM
- Images: PNG, JPG, JPEG, BMP, WebP

## Deployment

### On Raspberry Pi

The service auto-starts on boot:

```bash
sudo systemctl start projection-mapper      # Start now
sudo systemctl stop projection-mapper       # Stop
sudo systemctl status projection-mapper     # Status
journalctl -u projection-mapper -f          # Tail logs
```

To re-calibrate remotely, SSH into the Pi and run:

```bash
export DISPLAY=:0
python main.py --calibrate
```

Or use VNC to access the desktop and run from terminal.

## Development

Run tests:

```bash
pip install pytest
pytest tests/
```

## Architecture

- `main.py`: Entry point, mode selector
- `renderer.py`: OpenGL quad rendering and compositing
- `calibration.py`: Interactive calibration UI
- `homography.py`: Perspective math helpers
- `media_loader.py`: Video/image decoding (OpenCV)
- `config_io.py`: Config file I/O and validation
- `platform_io.py`: Platform-specific utilities (Mac/Pi detection, GPU info)

## Performance Notes

- Targets 1080p30 on Raspberry Pi VideoCore GPU
- Keeps shaders simple; avoids large texture uploads per frame
- Video decoding handled by OpenCV (ffmpeg backend)
- Tested on Raspberry Pi 4/5

## Known Limitations

- Audio is not currently supported
- No remote control interface (filesystem-based config updates only)
- Single projector output only (no multi-output support)

## License

See LICENSE file.
