import threading

import cv2


FULL_CROP = (0.0, 0.0, 1.0, 1.0)


class Source:
    """
    Something that produces images for a frame.

    The render loop calls latest() every tick and only re-uploads the
    texture when the returned version changes, so sources publish a new
    image only when the content actually changed.
    """

    def __init__(self, cfg):
        self.cfg = dict(cfg)
        self.target_size = None
        self._lock = threading.Lock()
        self._version = 0
        self._image = None

    # -- lifecycle -----------------------------------------------------------

    def start(self):
        pass

    def close(self):
        pass

    def update(self, cfg):
        """
        Apply a changed config in place. Return False if the change can't be
        applied without recreating the source (e.g. a different device).
        """
        self.cfg = dict(cfg)
        return True

    def poll(self, now):
        """Called every render tick, whether or not the frame is showing."""
        pass

    # -- state read by the render loop --------------------------------------

    def latest(self):
        """Return (version, rgba ndarray) or None if nothing has been produced yet."""
        with self._lock:
            if self._image is None:
                return None
            return self._version, self._image

    @property
    def visible(self):
        """False hides the frame (e.g. a cast frame while nobody is casting)."""
        return True

    @property
    def active(self):
        """Whether this source is 'in use', for frame rules. Only cast sources ever say True."""
        return False

    @property
    def orientation(self):
        return 'landscape'

    @property
    def crop(self):
        """Sub-rect of the image to show, as normalized (x0, y0, x1, y1)."""
        return FULL_CROP

    def set_target_size(self, width, height):
        """The frame's on-canvas footprint in pixels; sources may render/downscale to it."""
        self.target_size = (max(1, int(width)), max(1, int(height)))

    # -- helpers for subclasses ----------------------------------------------

    def _publish(self, rgba):
        with self._lock:
            self._image = rgba
            self._version += 1

    def _clear(self):
        with self._lock:
            self._image = None
            self._version += 1

    def _fit_to_target(self, img):
        """Downscale img (never upscale) so it's no bigger than needed for the frame's footprint."""
        if self.target_size is None:
            return img
        tw, th = self.target_size
        h, w = img.shape[:2]
        scale = min(1.0, max(tw / w, th / h))
        if scale >= 0.95:
            return img
        return cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))),
                          interpolation=cv2.INTER_AREA)


class ThreadedSource(Source):
    """A source whose frames are produced by a background thread."""

    def __init__(self, cfg):
        super().__init__(cfg)
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=type(self).__name__, daemon=True)
        self._thread.start()

    def close(self):
        self._stop.set()
        if self._thread and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)
        self._thread = None

    def _run(self):
        raise NotImplementedError


def bgr_to_rgba(img):
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2RGBA)
    if img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_BGRA2RGBA)
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGBA)
