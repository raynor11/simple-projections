import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config_io import validate_config, migrate_config, save_config, load_config


def base_config(**frame):
    return {"canvas": {"width": 1920, "height": 1080}, "frames": [dict({"id": "f1"}, **frame)]}


def test_migrate_media_to_file_source():
    config = base_config(media="media/a.mp4", corners={})
    migrate_config(config)
    assert config["frames"][0]["source"] == {"type": "file", "path": "media/a.mp4"}
    assert "media" not in config["frames"][0]


def test_rect_frame_is_valid():
    validate_config(base_config(rect=[0.1, 0.1, 0.5, 0.5], source={"type": "text", "text": "hi"}))


def test_legacy_corners_frame_is_valid():
    corners = {"tl": [0, 0], "tr": [100, 0], "br": [100, 100], "bl": [0, 100]}
    validate_config(base_config(media="a.mp4", corners=corners))


def test_frame_needs_rect_or_corners():
    with pytest.raises(ValueError, match="rect or corners"):
        validate_config(base_config(source={"type": "text", "text": "hi"}))


def test_bad_rect_rejected():
    with pytest.raises(ValueError, match="rect"):
        validate_config(base_config(rect=[0, 0, 1], source={"type": "text", "text": "hi"}))


def test_unknown_source_type_rejected():
    with pytest.raises(ValueError, match="unknown source type"):
        validate_config(base_config(rect=[0, 0, 1, 1], source={"type": "hologram"}))


def test_source_missing_required_key_rejected():
    with pytest.raises(ValueError, match="device"):
        validate_config(base_config(rect=[0, 0, 1, 1], source={"type": "camera"}))


def test_duplicate_frame_ids_rejected():
    config = base_config(rect=[0, 0, 1, 1], source={"type": "text", "text": "a"})
    config["frames"].append(dict(config["frames"][0]))
    with pytest.raises(ValueError, match="duplicated"):
        validate_config(config)


def test_save_then_load_round_trip(tmp_path):
    config = base_config(rect=[0, 0, 1, 1], source={"type": "text", "text": "a"})
    path = tmp_path / "frames.json"
    save_config(config, path)
    assert load_config(path) == config
    assert not (tmp_path / "frames.json.tmp").exists()
