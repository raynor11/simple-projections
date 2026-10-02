import time
from pathlib import Path

import cv2

from .base import FrameRing, Source, ThreadedSource, bgr_to_rgba


VIDEO_SUFFIXES = {'.mp4', '.mov', '.avi', '.mkv', '.webm'}
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.bmp', '.webp'}


def create_file_source(cfg):
    path = Path(cfg['path'])
    suffix = path.suffix.lower()
    if not path.exists():
        raise FileNotFoundError(f"Media not found: {path}")
    if suffix in VIDEO_SUFFIXES:
        return VideoFileSource(cfg)
    if suffix in IMAGE_SUFFIXES:
        return ImageFileSource(cfg)
    raise ValueError(f"Unsupported media format: {suffix}")


class ImageFileSource(Source):
    """A still image, decoded once."""

    def __init__(self, cfg):
        super().__init__(cfg)
        self._raw = None

    def start(self):
        img = cv2.imread(str(self.cfg['path']), cv2.IMREAD_UNCHANGED)
        if img is None:
            raise RuntimeError(f"Failed to load image: {self.cfg['path']}")
        self._raw = bgr_to_rgba(img)
        self._publish(self._fit_to_target(self._raw))

    def set_target_size(self, width, height):
        changed = self.target_size != (max(1, int(width)), max(1, int(height)))
        super().set_target_size(width, height)
        if changed and self._raw is not None:
            self._publish(self._fit_to_target(self._raw))

    def update(self, cfg):
        return cfg.get('path') == self.cfg.get('path')


class VideoFileSource(ThreadedSource):
    """
    A looping video, decoded in a background thread and paced to its frame
    rate. Frames are published in OpenCV's BGR order at their native size:
    the GPU swaps the channels and scales them while drawing.
    """

    pixel_format = 'BGR'

    def update(self, cfg):
        return cfg.get('path') == self.cfg.get('path')

    def _run(self):
        cap = cv2.VideoCapture(str(self.cfg['path']))
        if not cap.isOpened():
            print(f"Failed to open video: {self.cfg['path']}")
            return
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        interval = 1.0 / min(max(fps, 1), 60)
        next_time = time.monotonic()
        ring, shrink_ring = FrameRing(), FrameRing()
        try:
            while not self._stop.is_set():
                ok, frame = cap.read(ring.next())
                if not ok:
                    # Loop back to the start at end of file.
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    ok, frame = cap.read(ring.next())
                    if not ok:
                        print(f"Video produced no frames: {self.cfg['path']}")
                        return
                self._publish(self._shrink_for_target(ring.filled(frame), shrink_ring))

                next_time += interval
                delay = next_time - time.monotonic()
                if delay > 0:
                    self._stop.wait(delay)
                else:
                    # Decoding can't keep up; don't try to catch up in a burst.
                    next_time = time.monotonic()
        finally:
            cap.release()
