import sys

import cv2

from .base import ThreadedSource, bgr_to_rgba


RECONNECT_SECONDS = 3.0


def parse_device(device):
    """Device paths stay strings; integer indexes (handy for Mac webcams in dev) become ints."""
    if isinstance(device, int):
        return device
    if isinstance(device, str) and device.isdigit():
        return int(device)
    return device


def open_capture(device, width=None, height=None, fps=None, fourcc='MJPG'):
    """
    Open a live capture device, by default requesting MJPG (what USB capture
    cards and webcams stream at 720p+). Pass fourcc=None to take the
    device's own format, e.g. for a v4l2loopback device.
    """
    device = parse_device(device)
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
    """

    DEVICE_KEYS = ('device', 'width', 'height', 'fps', 'fourcc')
    RECONNECT_SECONDS = RECONNECT_SECONDS

    def __init__(self, cfg):
        super().__init__(cfg)
        self._warned = False

    def update(self, cfg):
        if any(cfg.get(k) != self.cfg.get(k) for k in self.DEVICE_KEYS):
            return False
        self.cfg = dict(cfg)
        return True

    def _run(self):
        while not self._stop.is_set():
            cap = open_capture(self.cfg['device'], self.cfg.get('width'), self.cfg.get('height'),
                               self.cfg.get('fps'), self.cfg.get('fourcc', 'MJPG'))
            if cap is None:
                if not self._warned:
                    print(f"Capture device unavailable, retrying: {self.cfg['device']}")
                    self._warned = True
                self._stop.wait(self.RECONNECT_SECONDS)
                continue
            if self._warned:
                print(f"Capture device connected: {self.cfg['device']}")
                self._warned = False
            try:
                while not self._stop.is_set():
                    ok, frame = cap.read()
                    if not ok:
                        print(f"Capture device stopped delivering frames: {self.cfg['device']}")
                        break
                    self._on_frame(frame)
            finally:
                cap.release()
            self._stop.wait(self.RECONNECT_SECONDS)

    def _on_frame(self, frame):
        self._publish(bgr_to_rgba(self._fit_to_target(frame)))
