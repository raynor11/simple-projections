import sys

import cv2

from .base import FrameRing, ThreadedSource


RECONNECT_SECONDS = 3.0
# Capture size when the config doesn't say: plenty for a frame on a
# 1080p canvas, and what USB capture cards and webcams stream as MJPEG.
DEFAULT_SIZE = (1280, 720)


def parse_device(device):
    """Device paths stay strings; integer indexes (handy for Mac webcams in dev) become ints."""
    if isinstance(device, int):
        return device
    if isinstance(device, str) and device.isdigit():
        return int(device)
    return device


def gstreamer_mjpeg_pipeline(device, width, height, fps):
    """
    Read MJPEG from a V4L2 device and decode it on the Pi's hardware JPEG
    decoder (v4l2jpegdec), converting colour on the ISP (v4l2convert).
    """
    rate = f',framerate={int(fps)}/1' if fps else ''
    return (f'v4l2src device={device} ! image/jpeg,width={width},height={height}{rate} ! '
            f'v4l2jpegdec ! v4l2convert ! video/x-raw,format=BGR ! appsink drop=true max-buffers=1 sync=false')


def open_capture(device, width=None, height=None, fps=None, fourcc='MJPG', decoder='opencv'):
    """
    Open a live capture device. decoder='gstreamer' (Linux, MJPEG devices)
    tries the Pi's hardware JPEG decoder first and falls back to OpenCV's
    own V4L2 capture + libjpeg-turbo, which is the default until measured
    faster on the Pi.
    """
    device = parse_device(device)
    if decoder == 'gstreamer' and sys.platform.startswith('linux') and isinstance(device, str) and width:
        cap = cv2.VideoCapture(gstreamer_mjpeg_pipeline(device, width, height, fps), cv2.CAP_GSTREAMER)
        if cap.isOpened():
            return cap
        cap.release()
        print(f"Hardware JPEG decoding unavailable for {device}; using OpenCV")
    return _open_v4l2(device, width, height, fps, fourcc)


def _open_v4l2(device, width=None, height=None, fps=None, fourcc='MJPG'):
    """
    Open a live capture device, by default requesting MJPG (what USB capture
    cards and webcams stream at 720p+). Pass fourcc=None to take the
    device's own format, e.g. for a v4l2loopback device.
    """
    on_linux = sys.platform.startswith('linux')
    cap = cv2.VideoCapture(device, cv2.CAP_V4L2 if on_linux else cv2.CAP_ANY)
    if not cap.isOpened():
        cap.release()
        return None
    if on_linux and fourcc:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
    if width and height:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    if fps:
        cap.set(cv2.CAP_PROP_FPS, fps)
    # Keep latency low: we only ever want the newest frame.
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


class V4L2Source(ThreadedSource):
    """
    A live camera or capture card, read on a background thread. Reconnects
    automatically if the device is unplugged or not there yet at startup.
    Frames are published in OpenCV's BGR order at capture size; the GPU
    swaps the channels and scales them.
    """

    pixel_format = 'BGR'

    DEVICE_KEYS = ('device', 'width', 'height', 'fps', 'fourcc', 'decoder')
    RECONNECT_SECONDS = RECONNECT_SECONDS

    def __init__(self, cfg):
        super().__init__(cfg)
        self._warned = False
        self._shrink_ring = FrameRing()

    def update(self, cfg):
        if any(cfg.get(k) != self.cfg.get(k) for k in self.DEVICE_KEYS):
            return False
        self.cfg = dict(cfg)
        return True

    def _device_ready(self):
        """Whether it's worth trying to open the device now (subclasses can wait for it)."""
        return True

    def _run(self):
        while not self._stop.is_set():
            if not self._device_ready():
                self._stop.wait(self.RECONNECT_SECONDS)
                continue
            cap = open_capture(self.cfg['device'], self.cfg.get('width', DEFAULT_SIZE[0]),
                               self.cfg.get('height', DEFAULT_SIZE[1]), self.cfg.get('fps'),
                               self.cfg.get('fourcc', 'MJPG'), self.cfg.get('decoder', 'opencv'))
            if cap is None:
                if not self._warned:
                    print(f"Capture device unavailable, retrying: {self.cfg['device']}")
                    self._warned = True
                self._stop.wait(self.RECONNECT_SECONDS)
                continue
            if self._warned:
                print(f"Capture device connected: {self.cfg['device']}")
                self._warned = False
            ring = FrameRing()
            try:
                while not self._stop.is_set():
                    ok, frame = cap.read(ring.next())
                    if not ok:
                        print(f"Capture device stopped delivering frames: {self.cfg['device']}")
                        break
                    self._on_frame(ring.filled(frame))
            finally:
                cap.release()
            self._stop.wait(self.RECONNECT_SECONDS)

    def _on_frame(self, frame):
        self._publish(self._shrink_for_target(frame, self._shrink_ring))
