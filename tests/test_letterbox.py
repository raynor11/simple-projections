import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sources.letterbox import detect_content_box, OrientationTracker


def frame_with_content(x0, y0, x1, y1, w=1280, h=720, bar=16):
    """A BGR frame of near-black bars with textured content in the given normalized box."""
    img = np.full((h, w, 3), bar, np.uint8)
    rng = np.random.default_rng(0)
    px0, py0, px1, py1 = int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)
    img[py0:py1, px0:px1] = rng.integers(60, 255, (py1 - py0, px1 - px0, 3), dtype=np.uint8)
    return img


def test_full_landscape_content():
    box = detect_content_box(frame_with_content(0, 0, 1, 1))
    assert box == pytest.approx((0, 0, 1, 1), abs=0.01)


def test_pillarboxed_portrait_content():
    # 9:16 content centered in a 16:9 frame occupies ~31.6% of the width.
    box = detect_content_box(frame_with_content(0.342, 0, 0.658, 1))
    assert box == pytest.approx((0.342, 0, 0.658, 1), abs=0.01)


def test_all_black_frame():
    assert detect_content_box(np.full((720, 1280, 3), 16, np.uint8)) is None


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_tracker_switches_only_after_hold():
    clock = FakeClock()
    tracker = OrientationTracker(hold_seconds=1.5, clock=clock)
    portrait = (0.342, 0.0, 0.658, 1.0)
    for t in (0.0, 0.5, 1.0):
        clock.t = t
        tracker.observe(portrait, 1280, 720)
        assert tracker.orientation == 'landscape'
    clock.t = 1.6
    tracker.observe(portrait, 1280, 720)
    assert tracker.orientation == 'portrait'
    assert tracker.crop == pytest.approx(portrait)


def test_tracker_ignores_black_frames_and_brief_flips():
    clock = FakeClock()
    tracker = OrientationTracker(hold_seconds=1.5, clock=clock)
    portrait = (0.342, 0.0, 0.658, 1.0)
    clock.t = 0.0
    tracker.observe(portrait, 1280, 720)
    clock.t = 0.5
    tracker.observe(None, 1280, 720)                    # dark scene: ignored
    clock.t = 1.0
    tracker.observe((0, 0, 1, 1), 1280, 720)            # back to landscape: candidate cleared
    clock.t = 2.0
    tracker.observe(portrait, 1280, 720)                # new candidate starts the hold over
    assert tracker.orientation == 'landscape'


def test_tracker_ignores_negligible_bars():
    clock = FakeClock()
    tracker = OrientationTracker(hold_seconds=0, clock=clock)
    tracker.observe((0.01, 0.0, 0.99, 1.0), 1280, 720)
    assert tracker.crop == (0, 0, 1, 1)
