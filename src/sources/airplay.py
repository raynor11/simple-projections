import fcntl
import os
import struct
import time
from pathlib import Path

import cv2

from .cast import ActiveDebouncer, OnDemandCaptureSource, DEFAULT_GRACE_SECONDS
from .text import FontCache, render_rows, render_size


TCP_ESTABLISHED = '01'
PROC_TCP_FILES = ('/proc/net/tcp', '/proc/net/tcp6')
CHECK_SECONDS = 0.5
# An AirPlay session that sends no video is a phone handing a video over
# ("AirPlay video", which UxPlay doesn't take) or playing audio only. After
# HINT_AFTER_SECONDS without video, the frame explains that only sound is
# playing and how to get video.
HINT_AFTER_SECONDS = 5
HINT_SECONDS = 20
DEFAULT_HINT = [("AirPlay is playing sound only", 1.0),
                ("To show video, use Screen Mirroring or cast to the Chromecast", 0.6)]


def count_established(proc_net_tcp_text, port):
    """
    Count ESTABLISHED connections whose local port is `port`, from the
    contents of /proc/net/tcp or /proc/net/tcp6.
    """
    count = 0
    for line in proc_net_tcp_text.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 4:
            continue
        local, state = fields[1], fields[3]
        if state == TCP_ESTABLISHED and int(local.rsplit(':', 1)[1], 16) == port:
            count += 1
    return count


# struct v4l2_capability is 104 bytes; device_caps is the u32 at offset 88.
VIDIOC_QUERYCAP = 0x80685600
V4L2_CAP_VIDEO_CAPTURE = 0x1


def has_video_capture(device):
    """
    Whether a V4L2 device currently offers video capture. A v4l2loopback
    device with exclusive_caps=1 only does while something is writing to it,
    so this tells whether UxPlay is sending video (an audio-only AirPlay
    session never writes any). Unknown (not Linux, ioctl failed) counts as yes.
    """
    try:
        fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return False
    try:
        buf = fcntl.ioctl(fd, VIDIOC_QUERYCAP, bytes(104))
    except OSError:
        return True
    finally:
        os.close(fd)
    device_caps = struct.unpack_from('<I', buf, 88)[0]
    return bool(device_caps & V4L2_CAP_VIDEO_CAPTURE)


def airplay_connected(port):
    total = 0
    for path in PROC_TCP_FILES:
        try:
            total += count_established(Path(path).read_text(), port)
        except OSError:
            pass
    return total > 0


class AirPlaySource(OnDemandCaptureSource):
    """
    AirPlay mirroring, received by UxPlay (a separate service) and written
    into a v4l2loopback device.

    A session is running while a phone holds a TCP connection to UxPlay's
    port. That's checked directly because mirroring stops sending frames
    while the phone's screen is static, so "no recent frames" doesn't mean
    the session ended.
    """

    DEVICE_KEYS = OnDemandCaptureSource.DEVICE_KEYS + ('port', 'grace_seconds')
    HINT_COLOR = (255, 255, 255, 255)
    HINT_BACKGROUND = (20, 20, 20, 255)
    # v4l2loopback only accepts readers while UxPlay is writing, so retry
    # opening quickly once a session starts.
    RECONNECT_SECONDS = 0.5

    def __init__(self, cfg):
        cfg = dict(cfg)
        cfg.setdefault('fourcc', None)   # take the loopback's own format
        super().__init__(cfg)
        self.port = int(cfg.get('port', 7000))
        self.debouncer = ActiveDebouncer(cfg.get('grace_seconds', DEFAULT_GRACE_SECONDS))
        self._last_check = 0.0
        self._fonts = None
        self._reset_hint()

    def _reset_hint(self, started=None):
        self._session_started = started
        self._got_video = False
        self._hint_since = None
        self._hint_done = False

    def _hint_rows(self):
        hint = self.cfg.get('hint', True)
        if hint is False:
            return None
        return [(hint, 1.0)] if isinstance(hint, str) else DEFAULT_HINT

    def _render_hint(self, rows):
        if self._fonts is None:
            self._fonts = FontCache()
        rgba = render_rows(rows, render_size(self.target_size), self._fonts,
                           color=self.HINT_COLOR, background=self.HINT_BACKGROUND)
        return cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGR)   # this source publishes BGR

    def _start_capture(self):
        self._reset_hint(time.monotonic())
        super()._start_capture()

    def _on_frame(self, frame):
        self._got_video = True
        self._hint_since = None
        super()._on_frame(frame)

    def poll(self, now):
        super().poll(now)
        if not self._capturing or self._got_video or self._hint_done:
            return
        t = time.monotonic()
        if self._hint_since is None:
            rows = self._hint_rows()
            if rows and t - self._session_started >= HINT_AFTER_SECONDS:
                print("airplay: no video in this session; showing the sound-only hint")
                self._publish(self._render_hint(rows))
                self._hint_since = t
        elif t - self._hint_since >= HINT_SECONDS:
            self._clear()
            self._hint_since = None
            self._hint_done = True

    @property
    def active(self):
        # Only real video counts for frame rules, not the hint.
        return self.visible and self._got_video

    def _device_ready(self):
        return has_video_capture(self.cfg['device'])

    def _session_active(self):
        now = time.monotonic()
        if now - self._last_check >= CHECK_SECONDS:
            self._last_check = now
            self.debouncer.set(airplay_connected(self.port))
        return self.debouncer.value
