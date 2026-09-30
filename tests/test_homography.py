import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.homography import (
    compute_homography, nudge_corner, screen_homography, rect_to_canvas_corners,
    uv_homography, apply_homography, quad_footprint,
)
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
            "height": 1920
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


def test_screen_homography_maps_unit_square_to_screen_corners():
    screen = {'tl': [100, 50], 'tr': [1800, 80], 'br': [1750, 1000], 'bl': [150, 1030]}
    S = screen_homography(screen)
    corners = rect_to_canvas_corners([0, 0, 1, 1], S)
    for key in ('tl', 'tr', 'br', 'bl'):
        assert np.allclose(corners[key], screen[key], atol=1e-3)


def test_rect_to_canvas_corners_identity_screen():
    screen = {'tl': [0, 0], 'tr': [1920, 0], 'br': [1920, 1080], 'bl': [0, 1080]}
    S = screen_homography(screen)
    corners = rect_to_canvas_corners([0.25, 0.5, 0.5, 0.25], S)
    assert np.allclose(corners['tl'], [480, 540], atol=1e-3)
    assert np.allclose(corners['br'], [1440, 810], atol=1e-3)


def test_uv_homography_maps_corners_to_unit_square():
    corners = {'tl': [100, 100], 'tr': [500, 110], 'br': [490, 400], 'bl': [90, 390]}
    H = uv_homography(corners)
    uv = apply_homography(H, [corners[k] for k in ('tl', 'tr', 'br', 'bl')])
    assert np.allclose(uv, [[0, 0], [1, 0], [1, 1], [0, 1]], atol=1e-5)


def test_quad_footprint():
    corners = {'tl': [0, 0], 'tr': [400, 0], 'br': [400, 300], 'bl': [0, 300]}
    assert quad_footprint(corners) == (400, 300)
