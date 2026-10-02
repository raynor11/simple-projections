import os
import sys
import time
from functools import lru_cache
from pathlib import Path

import cv2

from .base import FrameRing, Source, ThreadedSource, bgr_to_rgba


VIDEO_SUFFIXES = {'.mp4', '.mov', '.avi', '.mkv', '.webm'}
DEMUXERS = {'.mp4': 'qtdemux', '.mov': 'qtdemux', '.mkv': 'matroskademux', '.webm': 'matroskademux'}


@lru_cache(maxsize=1)
def opencv_has_gstreamer():
    return any(line.strip().startswith('GStreamer:') and 'YES' in line
               for line in cv2.getBuildInformation().splitlines())


def hardware_pipelines(path):
    """
    GStreamer pipelines that decode H.264 on the Raspberry Pi 4's hardware
    decoder (v4l2h264dec), best first: colour conversion on the ISP
    (v4l2convert), then on the CPU (videoconvert). Empty if the container
    isn't one we can demux this way.
    """
    demux = DEMUXERS.get(Path(path).suffix.lower())
    if demux is None:
        return []
    src = f'filesrc location="{path}" ! {demux} ! h264parse ! v4l2h264dec'
    sink = 'video/x-raw,format=BGR ! appsink drop=true max-buffers=2 sync=false'
    return [f'{src} ! v4l2convert ! {sink}', f'{src} ! videoconvert ! {sink}']


def open_video(path):
    """
    (capture, decoder name). Uses the Pi's hardware H.264 decoder when
    OpenCV has GStreamer and the file is H.264; otherwise FFmpeg in
    software. PM_VIDEO_DECODER=software forces the fallback (to compare).
    """
    want_hw = (os.environ.get('PM_VIDEO_DECODER', 'auto') != 'software'
               and sys.platform.startswith('linux') and opencv_has_gstreamer())
    if want_hw:
        for name, pipeline in zip(('hardware (v4l2h264dec + v4l2convert)',
                                   'hardware (v4l2h264dec + videoconvert)'), hardware_pipelines(path)):
            cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
            if cap.isOpened():
                ok, _ = cap.read()     # a pipeline can open but fail to negotiate; check a frame decodes
                if ok:
                    cap.release()
                    return cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER), name
            cap.release()
    return cv2.VideoCapture(str(path)), 'software (FFmpeg)'


def video_fps(path):
    """The file's frame rate, read from its header with FFmpeg (GStreamer appsinks often don't report it)."""
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    cap.release()
    return min(max(fps, 1), 60)
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
        path = self.cfg['path']
        interval = 1.0 / video_fps(path)
        cap, decoder = open_video(path)
        if not cap.isOpened():
            print(f"Failed to open video: {path}")
            return
        print(f"Video {Path(path).name}: {decoder} decoding")
        next_time = time.monotonic()
        ring, shrink_ring = FrameRing(), FrameRing()
        try:
            while not self._stop.is_set():
                ok, frame = cap.read(ring.next())
                if not ok:
                    # Loop back to the start at end of file. A GStreamer pipeline
                    # can't rewind reliably, so it's reopened instead.
                    if decoder.startswith('software'):
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    else:
                        cap.release()
                        cap, _ = open_video(path)
                    ok, frame = cap.read(ring.next())
                    if not ok:
                        print(f"Video produced no frames: {path}")
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
