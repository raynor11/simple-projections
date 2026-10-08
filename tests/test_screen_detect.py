import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.homography import corners_to_array
from src.screen_detect import chessboard_pattern, detect_screen, DetectionError, find_screen_in_canvas

CANVAS = (1920, 1080)
CAM = (1600, 1200)

# Where the projector's canvas lands in the camera image (a keystoned view).
CANVAS_IN_CAM = np.float32([[180, 170], [1450, 120], [1500, 1050], [140, 980]])
# The screen's white area, in projector pixels (what detection should recover).
SCREEN_TRUTH = np.float32([[130, 80], [1790, 95], [1775, 1010], [140, 1000]])
BORDER_PX = 30


def reflectance_map(proj_to_cam, covers_screen=True):
    """Per-camera-pixel reflectance: white screen, black border, grey wall."""
    h, w = CANVAS[1] + 400, CANVAS[0] + 400   # the scene extends past the projection
    offset = np.float32([200, 200])
    scene = np.full((h, w), 0.55, np.float32)                    # wall
    outer = SCREEN_TRUTH + np.float32([[-1, -1], [1, -1], [1, 1], [-1, 1]]) * BORDER_PX
    cv2.fillPoly(scene, [np.int32((outer + offset) * 16)], 0.06, shift=4)          # black border
    cv2.fillPoly(scene, [np.int32((SCREEN_TRUTH + offset) * 16)], 0.92, shift=4)   # white screen
    shift = np.array([[1, 0, -200], [0, 1, -200], [0, 0, 1]], np.float64)
    return cv2.warpPerspective(scene, proj_to_cam @ shift, CAM, flags=cv2.INTER_AREA)


def photograph(projected_gray, proj_to_cam, reflect, ambient=12):
    light = cv2.warpPerspective(projected_gray.astype(np.float32), proj_to_cam, CAM,
                                flags=cv2.INTER_AREA)
    img = ambient + light * reflect
    rng = np.random.default_rng(1)
    img = img + rng.normal(0, 2, img.shape)
    img = cv2.GaussianBlur(img, (3, 3), 0)
    return np.clip(img, 0, 255).astype(np.uint8)


@pytest.fixture(scope='module')
def scene():
    corners = np.float32([[0, 0], [CANVAS[0], 0], [CANVAS[0], CANVAS[1]], [0, CANVAS[1]]])
    proj_to_cam = cv2.getPerspectiveTransform(corners, CANVAS_IN_CAM).astype(np.float64)
    reflect = reflectance_map(proj_to_cam)
    board_rgba, board_px = chessboard_pattern(*CANVAS)
    white = photograph(np.full(CANVAS[::-1], 255), proj_to_cam, reflect)
    black = photograph(np.zeros(CANVAS[::-1]), proj_to_cam, reflect)
    board = photograph(board_rgba[..., 0], proj_to_cam, reflect)
    return white, black, board, board_px


def test_detects_screen_corners_within_2px(scene):
    white, black, board, board_px = scene
    result = detect_screen(white, black, board, board_px, CANVAS)
    found = corners_to_array(result.corners)
    assert np.abs(found - SCREEN_TRUTH).max() < 2.0, found
    assert result.warnings == []


def test_no_screen_raises():
    blank = np.full((CAM[1], CAM[0]), 10, np.uint8)
    with pytest.raises(DetectionError):
        find_screen_in_canvas(blank, np.eye(3), CANVAS)


def test_auto_exposure_and_bright_wall(scene):
    """
    The real webcam can't lock its exposure, and the room light was on: the
    white photo saturates the screen and the wall comes out nearly as bright,
    and the black photo is exposed far brighter. Detection must still find it.
    """
    corners = np.float32([[0, 0], [CANVAS[0], 0], [CANVAS[0], CANVAS[1]], [0, CANVAS[1]]])
    proj_to_cam = cv2.getPerspectiveTransform(corners, CANVAS_IN_CAM).astype(np.float64)
    reflect = reflectance_map(proj_to_cam)
    board_rgba, board_px = chessboard_pattern(*CANVAS)
    white = photograph(np.full(CANVAS[::-1], 255), proj_to_cam, reflect * 1.6, ambient=40)   # overexposed
    black = photograph(np.zeros(CANVAS[::-1]), proj_to_cam, reflect, ambient=180)            # auto-brightened
    board = photograph(board_rgba[..., 0], proj_to_cam, reflect)
    found = corners_to_array(detect_screen(white, black, board, board_px, CANVAS).corners)
    assert np.abs(found - SCREEN_TRUTH).max() < 2.0, found


@pytest.mark.parametrize('turns', [1, 2, 3])
def test_camera_mounted_at_any_angle(scene, turns):
    """The real webcam is mounted on its side; the board's orientation must come from the board."""
    white, black, board = (np.ascontiguousarray(np.rot90(img, -turns)) for img in scene[:3])
    board_px = scene[3]
    found = corners_to_array(detect_screen(white, black, board, board_px, CANVAS).corners)
    assert np.abs(found - SCREEN_TRUTH).max() < 2.0, found


def test_wide_angle_lens_distortion(scene):
    """A barrel-distorting webcam, like the real one: corners must still land within a few px."""
    from src.screen_detect import LensModel
    lens = LensModel(CAM, k1=-0.25, k2=-0.3, dx=-60, dy=20)
    ys, xs = np.mgrid[0:CAM[1], 0:CAM[0]].astype(np.float64)
    src = lens.undistort(np.stack([xs.ravel(), ys.ravel()], axis=1)).reshape(CAM[1], CAM[0], 2)
    map_x, map_y = src[..., 0].astype(np.float32), src[..., 1].astype(np.float32)
    white, black, board = (cv2.remap(img, map_x, map_y, cv2.INTER_LINEAR) for img in scene[:3])
    found = corners_to_array(detect_screen(white, black, board, scene[3], CANVAS).corners)
    assert np.abs(found - SCREEN_TRUTH).max() < 4.0, found
