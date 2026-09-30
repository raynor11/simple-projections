import time

from .base import ThreadedSource
from .letterbox import LetterboxedV4L2Source


DEFAULT_GRACE_SECONDS = 3.0


class ActiveDebouncer:
    """
    Goes active immediately, but only goes inactive after the raw signal has
    been off for grace_seconds -- so the frame doesn't blink out between
    videos in a queue or across a brief reconnect.
    """

    def __init__(self, grace_seconds=DEFAULT_GRACE_SECONDS, clock=time.monotonic):
        self.grace_seconds = grace_seconds
        self.clock = clock
        self._raw = False
        self._off_since = None

    def set(self, raw):
        if raw:
            self._off_since = None
        elif self._raw:
            self._off_since = self.clock()
        self._raw = raw

    @property
    def value(self):
        if self._raw:
            return True
        return self._off_since is not None and self.clock() - self._off_since < self.grace_seconds


class OnDemandCaptureSource(LetterboxedV4L2Source):
    """
    A capture source that is only read (and its audio only played) while a
    cast session is running. Subclasses report the session via
    _session_active(). The frame is visible -- and counts as active for
    frame rules -- once the session has delivered a video frame, so
    audio-only sessions don't show an empty frame.
    """

    def __init__(self, cfg):
        super().__init__(cfg)
        self.audio = None
        self._capturing = False

    def _session_active(self):
        raise NotImplementedError

    def _start_capture(self):
        self.tracker.reset()
        self._clear()
        ThreadedSource.start(self)
        self._capturing = True

    def _stop_capture(self):
        if self._capturing:
            ThreadedSource.close(self)
            self._clear()
            self._capturing = False
        if self.audio:
            self.audio.stop()

    def start(self):
        pass  # capture starts with the first session, from poll()

    def close(self):
        self._stop_capture()

    def poll(self, now):
        if self._session_active():
            if not self._capturing:
                print(f"{self.cfg['type']}: session started")
                self._start_capture()
            if self.audio:
                self.audio.ensure_running()
        elif self._capturing:
            print(f"{self.cfg['type']}: session ended")
            self._stop_capture()

    @property
    def visible(self):
        return self._capturing and self.latest() is not None

    @property
    def active(self):
        return self.visible
