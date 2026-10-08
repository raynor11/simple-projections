import json
from pathlib import Path

from .rules import validate_rules


CONFIG_PATH = Path("config/frames.json")

CORNER_KEYS = ("tl", "tr", "br", "bl")


def load_config(path=CONFIG_PATH):
    """Load frames.json config."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path) as f:
        return json.load(f)


def save_config(config, path=CONFIG_PATH):
    """Save frames.json config."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    # Write-then-rename so the hot-reloading playback process never reads a
    # half-written file.
    with open(tmp, 'w') as f:
        json.dump(config, f, indent=2)
    tmp.replace(path)


class ConfigWatcher:
    """Notices edits to the config file (checked at most once per interval) and loads them."""

    def __init__(self, path, interval=1.0):
        self.path = Path(path)
        self.interval = interval
        self._mtime = self._stat()
        self._last_check = 0.0

    def _stat(self):
        try:
            return self.path.stat().st_mtime_ns
        except OSError:
            return None

    def check(self, now):
        """
        Return the new, validated config if the file changed since the last
        check, else None. An invalid edit is reported and skipped, so the
        running config stays in effect until the file is fixed.
        """
        if now - self._last_check < self.interval:
            return None
        self._last_check = now
        mtime = self._stat()
        if mtime is None or mtime == self._mtime:
            return None
        self._mtime = mtime
        try:
            config = migrate_config(load_config(self.path))
            validate_config(config)
        except Exception as e:
            print(f"Config change ignored, fix the file and save again: {e}")
            return None
        print(f"Config reloaded: {self.path}")
        return config


def migrate_config(config):
    """Convert legacy fields in place: a frame's "media": "path" becomes a file source."""
    for frame in config.get("frames", []):
        if "source" not in frame and "media" in frame:
            frame["source"] = {"type": "file", "path": frame.pop("media")}
    return config


def _check_corners(corners, what):
    if not isinstance(corners, dict):
        raise ValueError(f"{what} corners must be an object")
    for corner in CORNER_KEYS:
        value = corners.get(corner)
        if not (isinstance(value, list) and len(value) == 2
                and all(isinstance(v, (int, float)) for v in value)):
            raise ValueError(f"{what} corners missing or malformed: {corner}")


def _check_rect(rect, what):
    if not (isinstance(rect, list) and len(rect) == 4
            and all(isinstance(v, (int, float)) for v in rect)):
        raise ValueError(f"{what} must be [x, y, w, h]")
    if rect[2] <= 0 or rect[3] <= 0:
        raise ValueError(f"{what} must have positive width and height")


# Required fields per source type.
SOURCE_REQUIRED = {
    "file": ("path",),
    "text": ("text",),
    "weather": (),
    "camera": ("device",),
    "chromecast": ("device", "cast_name"),
    "airplay": ("device",),
}


def _check_source(source, what):
    if not isinstance(source, dict):
        raise ValueError(f"{what} source must be an object")
    kind = source.get("type")
    if kind not in SOURCE_REQUIRED:
        raise ValueError(f"{what} has unknown source type: {kind!r}")
    for key in SOURCE_REQUIRED[kind]:
        if key not in source:
            raise ValueError(f"{what} {kind} source missing key: {key}")
    if source.get("decoder", "opencv") not in ("opencv", "gstreamer"):
        raise ValueError(f"{what} decoder must be \"opencv\" or \"gstreamer\"")


def _check_projector(projector):
    from .projector.viewsonic import LIGHT_SOURCE_MODES, INPUTS, ASPECTS
    if "serial" not in projector:
        raise ValueError("Projector missing key: serial")
    brightness = projector.get("brightness")
    if brightness is not None:
        for phase in ("day", "dusk", "night"):
            if phase in brightness and brightness[phase] not in LIGHT_SOURCE_MODES:
                raise ValueError(f"Projector brightness {phase}: unknown light source mode "
                                 f"{brightness[phase]!r} (the LS740-4K has: {', '.join(LIGHT_SOURCE_MODES)})")
        dim = brightness.get("software_dim")
        if dim is not None:
            for phase, value in dim.items():
                if phase not in ("day", "dusk", "night") or not isinstance(value, (int, float)) \
                        or not 0.1 <= value <= 1.0:
                    raise ValueError("Projector brightness software_dim needs day/dusk/night values "
                                     "between 0.1 and 1.0")
    if projector.get("input", "hdmi1") not in INPUTS:
        raise ValueError(f"Projector input must be one of: {', '.join(INPUTS)}")
    if projector.get("aspect", "16:9") not in ASPECTS:
        raise ValueError(f"Projector aspect must be one of: {', '.join(ASPECTS)}")
    volume = projector.get("volume")
    if volume is not None and not (isinstance(volume, int) and 0 <= volume <= 10):
        raise ValueError("Projector volume must be a whole number from 0 to 10")


def validate_config(config):
    """Validate config structure. Raises ValueError describing the first problem found."""
    required_keys = {"canvas", "frames"}
    if not all(k in config for k in required_keys):
        raise ValueError(f"Config missing required keys: {required_keys}")

    canvas = config.get("canvas", {})
    for key in ["width", "height"]:
        if key not in canvas:
            raise ValueError(f"Canvas missing key: {key}")
        if not isinstance(canvas[key], int) or canvas[key] <= 0:
            raise ValueError(f"Canvas {key} must be a positive integer")

    screen = config.get("screen")
    if screen is not None and "corners" in screen:
        _check_corners(screen["corners"], "Screen")
    if screen is not None and screen.get("rotation", 0) not in (0, 90, 180, 270):
        raise ValueError("screen.rotation must be 0, 90, 180 or 270")

    # Leave "location" out (or set "auto") to look it up from the IP address,
    # or give {"address": ...} to geocode a street address.
    location = config.get("location")
    if location is not None and location != "auto":
        if not isinstance(location, dict):
            raise ValueError('Location must be "auto", {"address": ...} or {"lat": ..., "lon": ...}')
        if "address" in location and "lat" not in location:
            if not isinstance(location["address"], str) or not location["address"].strip():
                raise ValueError("Location address must be a non-empty string")
        else:
            for key in ("lat", "lon"):
                if not isinstance(location.get(key), (int, float)):
                    raise ValueError(f"Location missing numeric {key}")

    projector = config.get("projector")
    if projector is not None:
        _check_projector(projector)

    seen_ids = set()
    for frame in config.get("frames", []):
        if "id" not in frame:
            raise ValueError("Frame missing key: id")
        what = f"Frame {frame['id']!r}"
        if frame["id"] in seen_ids:
            raise ValueError(f"{what} is duplicated")
        seen_ids.add(frame["id"])

        if "source" in frame:
            _check_source(frame["source"], what)
        elif "media" not in frame:
            raise ValueError(f"{what} missing key: source")

        shapes = [k for k in ("rect", "quad", "corners") if k in frame]
        if len(shapes) != 1:
            raise ValueError(f"{what} needs exactly one of rect, quad or corners")
        if "rect" in frame:
            _check_rect(frame["rect"], f"{what} rect")
            if "rect_portrait" in frame:
                _check_rect(frame["rect_portrait"], f"{what} rect_portrait")
        elif "quad" in frame:
            _check_corners(frame["quad"], f"{what} quad")
        else:
            _check_corners(frame["corners"], what)

    for frame in config.get("frames", []):
        validate_rules(frame, seen_ids)
        for n, rule in enumerate(frame.get("rules") or [], 1):
            overrides, where = rule["set"], f"Frame {frame['id']!r} rule {n}"
            for key in ("rect", "rect_portrait"):
                if key in overrides:
                    _check_rect(overrides[key], f"{where} {key}")
            if "corners" in overrides:
                _check_corners(overrides["corners"], where)
            if "quad" in overrides:
                _check_corners(overrides["quad"], f"{where} quad")
            if "source" in overrides and "type" in overrides["source"]:
                _check_source(overrides["source"], where)
