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


# Uploading frames to the GPU is the Pi's bottleneck (~150 MB/s), so videos
# are decoded no bigger than about 720p's pixel count: a 1080x1920 frame is
# 6 MB, 24 times a second.
MAX_VIDEO_PIXELS = 1280 * 720


def decode_size(native, target, max_pixels=MAX_VIDEO_PIXELS):
    """
    (width, height) to decode a native-size video at: just big enough to
    cover its frame's on-canvas footprint, within max_pixels, never bigger
    than the video, keeping its shape. None means native size.
    """
    w, h = native
    if w <= 0 or h <= 0:
        return None
    scale = 1.0
    if target:
        scale = min(1.0, max(target[0] / w, target[1] / h))
    scale = min(scale, (max_pixels / (w * h)) ** 0.5)
    if scale >= 0.98:
        return None
    return max(2, int(w * scale) // 2 * 2), max(2, int(h * scale) // 2 * 2)


def hardware_pipelines(path, size=None):
    """
    GStreamer pipelines that decode H.264 on the Raspberry Pi 4's hardware
    decoder (v4l2h264dec), best first: colour conversion (and scaling to
    `size`) on the ISP (v4l2convert), then on the CPU (videoconvert). Empty if
    the container isn't one we can demux this way.
    """
    demux = DEMUXERS.get(Path(path).suffix.lower())
    if demux is None:
        return []
    src = f'filesrc location="{path}" ! {demux} ! h264parse ! v4l2h264dec'
    caps = 'video/x-raw,format=BGR' + (f',width={size[0]},height={size[1]}' if size else '')
    sink = f'{caps} ! appsink drop=true max-buffers=2 sync=false'
    return [f'{src} ! v4l2convert ! {sink}', f'{src} ! videoconvert ! videoscale ! {sink}']


def open_video(path, size=None):
    """
    (capture, decoder name). Uses the Pi's hardware H.264 decoder when
    OpenCV has GStreamer and the file is H.264; otherwise FFmpeg in
    software. PM_VIDEO_DECODER=software forces the fallback (to compare).
    """
    want_hw = (os.environ.get('PM_VIDEO_DECODER', 'auto') != 'software'
               and sys.platform.startswith('linux') and opencv_has_gstreamer())
    if want_hw:
        for name, pipeline in zip(('hardware (v4l2h264dec + v4l2convert)',
                                   'hardware (v4l2h264dec + videoconvert)'), hardware_pipelines(path, size)):
            cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
            if cap.isOpened():
                ok, _ = cap.read()     # a pipeline can open but fail to negotiate; check a frame decodes
                if ok:
                    cap.release()
                    return cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER), \
                        name + (f' at {size[0]}x{size[1]}' if size else '')
            cap.release()
    return cv2.VideoCapture(str(path)), 'software (FFmpeg)'


def video_info(path):
    """
    (frame rate, (width, height)), read from the file's header with FFmpeg
    (GStreamer appsinks often don't report them).
    """
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    cap.release()
    return min(max(fps, 1), 60), size


def video_fps(path):
    return video_info(path)[0]
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
        fps, native = video_info(path)
        interval = 1.0 / fps
        # The frame's on-screen size arrives just after start(); wait briefly for it.
        for _ in range(20):
            if self.target_size or self._stop.wait(0.05):
                break
        cap, decoder = open_video(path, decode_size(native, self.target_size))
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
                        cap, _ = open_video(path, decode_size(native, self.target_size))
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
