import threading

from .audio import AudioLoop
from .cast import ActiveDebouncer, OnDemandCaptureSource, DEFAULT_GRACE_SECONDS


BACKDROP_APP_ID = 'E8C28D3C'   # the Chromecast's idle ambient screen
DISCOVERY_RETRY_SECONDS = 30


def is_casting(status, idle_app_ids=()):
    """Whether a pychromecast CastStatus means someone is actively casting."""
    if status is None or status.app_id is None:
        return False
    if status.app_id == BACKDROP_APP_ID or status.app_id in idle_app_ids:
        return False
    return (status.display_name or '').lower() != 'backdrop'


class CastWatcher:
    """Finds a Chromecast by name on the LAN and tracks whether something is being cast to it."""

    def __init__(self, name, idle_app_ids=(), grace_seconds=DEFAULT_GRACE_SECONDS):
        self.name = name
        self.idle_app_ids = set(idle_app_ids)
        self.debouncer = ActiveDebouncer(grace_seconds)
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread = None
        self._lock = threading.Lock()

    @property
    def active(self):
        with self._lock:
            return self.debouncer.value

    def _set_raw(self, raw):
        with self._lock:
            self.debouncer.set(raw)

    # pychromecast listener callbacks (called from its socket thread)
    def new_cast_status(self, status):
        self._set_raw(is_casting(status, self.idle_app_ids))

    def new_connection_status(self, status):
        if status.status in ('LOST', 'FAILED', 'DISCONNECTED', 'FAILED_RESOLVE'):
            self._set_raw(False)
            if status.status != 'LOST':  # pychromecast retries LOST connections itself
                self._lost.set()

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name='CastWatcher', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._lost.set()

    def _run(self):
        try:
            import pychromecast
            from pychromecast.discovery import stop_discovery
        except ImportError:
            print("Chromecast detection disabled: PyChromecast isn't installed")
            return

        warned = False
        while not self._stop.is_set():
            casts, browser = pychromecast.get_listed_chromecasts(friendly_names=[self.name],
                                                                 discovery_timeout=10)
            stop_discovery(browser)
            if not casts:
                if not warned:
                    print(f"Chromecast {self.name!r} not found on the network; retrying every "
                          f"{DISCOVERY_RETRY_SECONDS}s")
                    warned = True
                self._stop.wait(DISCOVERY_RETRY_SECONDS)
                continue

            warned = False
            cast = casts[0]
            self._lost.clear()
            try:
                cast.register_status_listener(self)
                cast.register_connection_listener(self)
                cast.wait(timeout=30)
                print(f"Connected to Chromecast {self.name!r}")
                self.new_cast_status(cast.status)
                self._lost.wait()
            except Exception as e:
                print(f"Chromecast connection error: {e}")
            finally:
                self._set_raw(False)
                try:
                    cast.disconnect(timeout=5)
                except Exception:
                    pass
            self._stop.wait(5)


class ChromecastSource(OnDemandCaptureSource):
    """
    The Chromecast via an HDMI capture card. Shown only while someone casts
    to it, which is detected over the network: the HDMI signal alone can't
    tell a cast from the ambient screensaver.
    """

    DEVICE_KEYS = OnDemandCaptureSource.DEVICE_KEYS + (
        'cast_name', 'audio_device', 'audio_sink', 'audio_delay_ms', 'idle_app_ids', 'grace_seconds')

    def __init__(self, cfg):
        super().__init__(cfg)
        self.watcher = CastWatcher(cfg['cast_name'], cfg.get('idle_app_ids', ()),
                                   cfg.get('grace_seconds', DEFAULT_GRACE_SECONDS))
        if cfg.get('audio_device'):
            self.audio = AudioLoop(cfg['audio_device'], cfg.get('audio_sink', 'projector'),
                                   cfg.get('audio_delay_ms', 120))

    def start(self):
        self.watcher.start()

    def close(self):
        self.watcher.stop()
        super().close()

    def _session_active(self):
        return self.watcher.active
