import json
import os
from pathlib import Path


CONFIG_PATH = Path("config/frames.json")


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
    with open(path, 'w') as f:
        json.dump(config, f, indent=2)


def validate_config(config):
    """Validate config structure."""
    required_keys = {"canvas", "frames"}
    if not all(k in config for k in required_keys):
        raise ValueError(f"Config missing required keys: {required_keys}")

    canvas = config.get("canvas", {})
    for key in ["width", "height"]:
        if key not in canvas:
            raise ValueError(f"Canvas missing key: {key}")

    for frame in config.get("frames", []):
        for key in ["id", "label", "media", "corners"]:
            if key not in frame:
                raise ValueError(f"Frame missing key: {key}")

        corners = frame["corners"]
        for corner in ["tl", "tr", "br", "bl"]:
            if corner not in corners or not isinstance(corners[corner], list):
                raise ValueError(f"Frame corners missing or malformed: {corner}")


def new_frame(frame_id, label, media_path, canvas_width, canvas_height):
    """Create a new default frame (centered rectangle)."""
    margin = 50
    return {
        "id": frame_id,
        "label": label,
        "media": media_path,
        "corners": {
            "tl": [margin, margin],
            "tr": [canvas_width - margin, margin],
            "br": [canvas_width - margin, canvas_height - margin],
            "bl": [margin, canvas_height - margin]
        }
    }
