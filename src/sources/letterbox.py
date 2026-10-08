import time

import cv2
import numpy as np

from .base import FULL_CROP
from .v4l2 import V4L2Source


ANALYSIS_SIZE = (160, 90)
BLACK_THRESHOLD = 28        # 8-bit luma; capture cards output "black" bars around 16
MIN_LIT_FRACTION = 0.02     # a row/column is content if at least this share of it is lit
IGNORE_BAR_FRACTION = 0.03  # bars thinner than this aren't worth cropping
PORTRAIT_BELOW = 0.9        # content aspect (w/h) below this is portrait...
LANDSCAPE_ABOVE = 1.1       # ...above this is landscape; in between keeps the current one


def detect_content_box(frame, threshold=BLACK_THRESHOLD):
    """
    Find the non-black region of a frame (i.e. inside any letterbox or
    pillarbox bars). Returns normalized (x0, y0, x1, y1), or None if the
    frame is entirely black.
    """
    if frame.ndim == 3:
        code = cv2.COLOR_RGBA2GRAY if frame.shape[2] == 4 else cv2.COLOR_BGR2GRAY
        frame = cv2.cvtColor(frame, code)
    small = cv2.resize(frame, ANALYSIS_SIZE, interpolation=cv2.INTER_AREA)
    lit = small > threshold
    cols = np.flatnonzero(lit.mean(axis=0) >= MIN_LIT_FRACTION)
    rows = np.flatnonzero(lit.mean(axis=1) >= MIN_LIT_FRACTION)
    if cols.size == 0 or rows.size == 0:
        return None
    w, h = ANALYSIS_SIZE
    return (cols[0] / w, rows[0] / h, (cols[-1] + 1) / w, (rows[-1] + 1) / h)


def snap_crop(box):
    """Ignore negligible bars so tiny dark edges in the content don't cause a crop."""
    x0, y0, x1, y1 = box
    if x0 < IGNORE_BAR_FRACTION and x1 > 1 - IGNORE_BAR_FRACTION:
        x0, x1 = 0.0, 1.0
    if y0 < IGNORE_BAR_FRACTION and y1 > 1 - IGNORE_BAR_FRACTION:
        y0, y1 = 0.0, 1.0
    return (x0, y0, x1, y1)


def classify(box, frame_w, frame_h, current):
    x0, y0, x1, y1 = box
    aspect = ((x1 - x0) * frame_w) / max(1e-6, (y1 - y0) * frame_h)
    if aspect < PORTRAIT_BELOW:
        return 'portrait'
    if aspect > LANDSCAPE_ABOVE:
        return 'landscape'
    return current


class OrientationTracker:
    """
    Turns per-frame content boxes into a stable orientation + crop. A change
    only takes effect after it has been seen consistently for hold_seconds,
    and all-black frames (dark scenes, fades) are ignored, so the frame
    doesn't flap between layouts.
    """

    def __init__(self, hold_seconds=1.5, clock=time.monotonic):
        self.hold_seconds = hold_seconds
        self.clock = clock
        self.reset()

    def reset(self):
        self.orientation = 'landscape'
        self.crop = FULL_CROP
        self._candidate = None
        self._since = None

    @staticmethod
    def _same(a, b):
        return a[0] == b[0] and np.allclose(a[1], b[1], atol=0.02)

    def observe(self, box, frame_w, frame_h):
        if box is None:
            return
        candidate = (classify(box, frame_w, frame_h, self.orientation), snap_crop(box))
        if self._same(candidate, (self.orientation, self.crop)):
            self._candidate = None
            return
        now = self.clock()
        if self._candidate is None or not self._same(candidate, self._candidate):
            self._candidate, self._since = candidate, now
            return
        if now - self._since >= self.hold_seconds:
            self.orientation, self.crop = candidate
            self._candidate = None


class LetterboxedV4L2Source(V4L2Source):
    """A capture source that crops black bars and reports portrait/landscape content."""

    ANALYZE_SECONDS = 0.5
    # Shown at its own shape within its frame (black around it), never stretched:
    # a cast's content can be any shape once its black bars are cropped.
    keep_aspect = True

    def __init__(self, cfg):
        super().__init__(cfg)
        self.tracker = OrientationTracker()
        self._last_analysis = 0.0

    def _on_frame(self, frame):
        now = time.monotonic()
        if now - self._last_analysis >= self.ANALYZE_SECONDS:
            self._last_analysis = now
            h, w = frame.shape[:2]
            self.tracker.observe(detect_content_box(frame), w, h)
        super()._on_frame(frame)

    @property
    def orientation(self):
        return self.tracker.orientation

    @property
    def crop(self):
        return self.tracker.crop
