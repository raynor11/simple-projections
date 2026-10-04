import threading
import time

from .audio import AudioLoop
from .cast import ActiveDebouncer, OnDemandCaptureSource, DEFAULT_GRACE_SECONDS


BACKDROP_APP_ID = 'E8C28D3C'   # the Chromecast's idle ambient screen
DISCOVERY_RETRY_SECONDS = 30
DEFAULT_PAUSED_HIDE_SECONDS = 60
STOPPED_MEDIA_STATES = ('PAUSED', 'IDLE')
CONNECT_MEDIA_WAIT_SECONDS = 5


def is_casting(status, idle_app_ids=()):
    """Whether a pychromecast CastStatus means someone is actively casting."""
    if status is None or status.app_id is None:
        return False
    if status.app_id == BACKDROP_APP_ID or status.app_id in idle_app_ids:
        return False
    return (status.display_name or '').lower() != 'backdrop'


class CastWatcher:
    """
    Finds a Chromecast by name on the LAN and tracks whether something is being
    cast to it. "Stop casting" in many apps leaves the app open on the
    Chromecast with its video paused, so media that has been paused or idle
    for paused_hide_seconds also counts as not casting.
    """

    def __init__(self, name, idle_app_ids=(), grace_seconds=DEFAULT_GRACE_SECONDS,
                 paused_hide_seconds=DEFAULT_PAUSED_HIDE_SECONDS, clock=time.monotonic):
        self.name = name
        self.idle_app_ids = set(idle_app_ids)
        self.paused_hide_seconds = paused_hide_seconds
        self.clock = clock
        self.debouncer = ActiveDebouncer(grace_seconds, clock)
        self._app_id = None
        self._app_casting = False
        self._media_state = None
        self._media_since = None
        # Right after connecting we don't know how long a paused video has
        # been paused, so wait briefly for the media status before showing.
        self._connected_at = None
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread = None
        self._lock = threading.Lock()

    @property
    def active(self):
        with self._lock:
            # Re-evaluated on every read: a pause times out without any new status.
            self.debouncer.set(self._raw())
            return self.debouncer.value

    def _raw(self):
        if not self._app_casting:
            return False
        if (self._connected_at is not None and self._media_state is None
                and self.clock() - self._connected_at < CONNECT_MEDIA_WAIT_SECONDS):
            return False
        stopped_for = (self.clock() - self._media_since
                       if self._media_state in STOPPED_MEDIA_STATES else 0)
        return stopped_for < self.paused_hide_seconds

    def _reset(self):
        with self._lock:
            self._connected_at = None
            self._app_id = None
            self._app_casting = False
            self._media_state = None
            self.debouncer.set(False)

    # pychromecast listener callbacks (called from its socket thread)
    def new_cast_status(self, status):
        with self._lock:
            app_id = status.app_id if status else None
            if app_id != self._app_id:
                self._app_id = app_id
                self._media_state = None   # a new app has no media yet
            self._app_casting = is_casting(status, self.idle_app_ids)
            self.debouncer.set(self._raw())

    def new_media_status(self, status):
        with self._lock:
            state = status.player_state
            if state != self._media_state:
                first_since_connect = self._connected_at is not None
                self._media_state = state
                self._media_since = self.clock()
                if first_since_connect and state in STOPPED_MEDIA_STATES:
                    # Already paused when we connected: treat it as paused long ago.
                    self._media_since -= self.paused_hide_seconds
            self._connected_at = None
            self.debouncer.set(self._raw())

    def new_connection_status(self, status):
        if status.status in ('LOST', 'FAILED', 'DISCONNECTED', 'FAILED_RESOLVE'):
            self._reset()
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
        except ImportError:
            print("Chromecast detection disabled: PyChromecast isn't installed")
            return

        warned = False
        while not self._stop.is_set():
            casts, browser = pychromecast.get_listed_chromecasts(friendly_names=[self.name],
                                                                 discovery_timeout=10)
            if not casts:
                browser.stop_discovery()
                if not warned:
                    print(f"Chromecast {self.name!r} not found on the network; retrying every "
                          f"{DISCOVERY_RETRY_SECONDS}s")
                    warned = True
                self._stop.wait(DISCOVERY_RETRY_SECONDS)
                continue

            warned = False
            cast = casts[0]
            self._lost.clear()
            with self._lock:
                self._connected_at = self.clock()
            try:
                cast.register_status_listener(self)
                cast.register_connection_listener(self)
                cast.media_controller.register_status_listener(self)
                cast.wait(timeout=30)
                print(f"Connected to Chromecast {self.name!r}")
                self.new_cast_status(cast.status)
                # If pychromecast's socket thread dies it reports nothing, so check on it.
                while not self._lost.wait(5):
                    if not cast.socket_client.is_alive():
                        print(f"Chromecast {self.name!r} connection thread exited; reconnecting")
                        break
            except Exception as e:
                print(f"Chromecast connection error: {e}")
            finally:
                self._reset()
                try:
                    cast.disconnect(timeout=5)
                except Exception:
                    pass
                # The cast resolves its host through the browser's zeroconf, so
                # discovery must outlive the connection.
                browser.stop_discovery()
            self._stop.wait(5)


class ChromecastSource(OnDemandCaptureSource):
    """
    The Chromecast via an HDMI capture card. Shown only while someone casts
    to it, which is detected over the network: the HDMI signal alone can't
    tell a cast from the ambient screensaver.
    """

    DEVICE_KEYS = OnDemandCaptureSource.DEVICE_KEYS + (
        'cast_name', 'audio_device', 'audio_sink', 'audio_delay_ms', 'idle_app_ids', 'grace_seconds',
        'paused_hide_seconds')

    def __init__(self, cfg):
        super().__init__(cfg)
        self.watcher = CastWatcher(cfg['cast_name'], cfg.get('idle_app_ids', ()),
                                   cfg.get('grace_seconds', DEFAULT_GRACE_SECONDS),
                                   cfg.get('paused_hide_seconds', DEFAULT_PAUSED_HIDE_SECONDS))
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
