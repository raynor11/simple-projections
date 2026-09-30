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


def _check_projector(projector):
    from .projector.viewsonic import DEFAULT_LIGHT_SOURCE_MODES
    if "serial" not in projector:
        raise ValueError("Projector missing key: serial")
    modes = projector.get("light_source_modes") or DEFAULT_LIGHT_SOURCE_MODES
    brightness = projector.get("brightness")
    if brightness is not None:
        for phase in ("day", "dusk", "night"):
            if phase in brightness and brightness[phase] not in modes:
                raise ValueError(f"Projector brightness {phase}: unknown light source mode "
                                 f"{brightness[phase]!r} (known: {', '.join(modes)})")
    opcode = projector.get("light_source_opcode")
    if opcode is not None and not (isinstance(opcode, list) and len(opcode) == 2
                                   and all(isinstance(b, int) and 0 <= b <= 255 for b in opcode)):
        raise ValueError("Projector light_source_opcode must be two bytes, e.g. [17, 16]")


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

    # Leave "location" out (or set "auto") to look it up from the IP address.
    location = config.get("location")
    if location is not None and location != "auto":
        if not isinstance(location, dict):
            raise ValueError('Location must be "auto" or {"lat": ..., "lon": ...}')
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

        if "rect" in frame:
            _check_rect(frame["rect"], f"{what} rect")
            if "rect_portrait" in frame:
                _check_rect(frame["rect_portrait"], f"{what} rect_portrait")
        elif "corners" in frame:
            _check_corners(frame["corners"], what)
        else:
            raise ValueError(f"{what} needs a rect or corners")

    for frame in config.get("frames", []):
        validate_rules(frame, seen_ids)
        for n, rule in enumerate(frame.get("rules") or [], 1):
            overrides, where = rule["set"], f"Frame {frame['id']!r} rule {n}"
            for key in ("rect", "rect_portrait"):
                if key in overrides:
                    _check_rect(overrides[key], f"{where} {key}")
            if "corners" in overrides:
                _check_corners(overrides["corners"], where)
            if "source" in overrides and "type" in overrides["source"]:
                _check_source(overrides["source"], where)
