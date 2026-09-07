import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.homography import compute_homography, nudge_corner
from src.config_io import validate_config, new_frame
import numpy as np


def test_compute_homography():
    """Test homography computation."""
    source_rect = (640, 480)
    dest_quad = {
        'tl': [100, 100],
        'tr': [500, 110],
        'br': [490, 400],
        'bl': [90, 390]
    }

    H = compute_homography(source_rect, dest_quad)

    assert H.shape == (4, 4)
    assert isinstance(H, np.ndarray)


def test_nudge_corner():
    """Test corner nudging."""
    quad = {
        'tl': [100, 100],
        'tr': [500, 100],
        'br': [500, 400],
        'bl': [100, 400]
    }

    updated = nudge_corner(quad, 'tl', 10, -5)

    assert updated['tl'] == [110, 95]
    assert updated['tr'] == [500, 100]


def test_validate_config():
    """Test config validation."""
    valid_config = {
        "canvas": {
            "width": 1080,
            "height": 1920,
            "orientation": "portrait"
        },
        "frames": [
            {
                "id": "frame_1",
                "label": "Test",
                "media": "test.mp4",
                "corners": {
                    "tl": [0, 0],
                    "tr": [100, 0],
                    "br": [100, 100],
                    "bl": [0, 100]
                }
            }
        ]
    }

    validate_config(valid_config)


def test_validate_config_missing_keys():
    """Test config validation with missing keys."""
    invalid_config = {"frames": []}

    with pytest.raises(ValueError):
        validate_config(invalid_config)


def test_new_frame():
    """Test creating a new default frame."""
    frame = new_frame("frame_1", "Test Frame", "media/test.mp4", 1920, 1080)

    assert frame['id'] == "frame_1"
    assert frame['label'] == "Test Frame"
    assert 'tl' in frame['corners']
    assert 'tr' in frame['corners']
    assert 'br' in frame['corners']
    assert 'bl' in frame['corners']
