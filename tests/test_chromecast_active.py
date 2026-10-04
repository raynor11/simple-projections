import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sources.chromecast import is_casting, BACKDROP_APP_ID
from src.sources.cast import ActiveDebouncer


def status(app_id, display_name=None):
    return SimpleNamespace(app_id=app_id, display_name=display_name)


def test_is_casting():
    assert not is_casting(None)
    assert not is_casting(status(None))
    assert not is_casting(status(BACKDROP_APP_ID, 'Backdrop'))
    assert not is_casting(status('ABC123', 'Backdrop'))
    assert not is_casting(status('HOME1'), idle_app_ids={'HOME1'})
    assert is_casting(status('233637DE', 'YouTube'))


def test_debouncer_grace_period():
    clock = SimpleNamespace(t=0.0)
    d = ActiveDebouncer(grace_seconds=3, clock=lambda: clock.t)
    assert not d.value
    d.set(True)
    assert d.value
    clock.t = 10
    d.set(False)
    clock.t = 12
    assert d.value            # still within grace
    d.set(True)               # next video starts: stays active
    d.set(False)
    clock.t = 14.9
    assert d.value
    clock.t = 15.1
    assert not d.value


class FakeBrowser:
    def __init__(self):
        self.stopped = False

    def stop_discovery(self):
        self.stopped = True


class FakeCast:
    """Records whether discovery was still running when the connection started."""

    def __init__(self, browser, watcher):
        self.browser, self.watcher = browser, watcher
        self.status = status('233637DE', 'YouTube')
        self.socket_client = SimpleNamespace(is_alive=lambda: True)
        self.media_controller = SimpleNamespace(register_status_listener=lambda listener: None)
        self.discovery_running_at_connect = None

    def register_status_listener(self, listener):
        pass

    def register_connection_listener(self, listener):
        pass

    def wait(self, timeout=None):
        # pychromecast resolves the host through the browser's zeroconf here
        self.discovery_running_at_connect = not self.browser.stopped
        self.watcher.stop()

    def disconnect(self, timeout=None):
        pass


def test_watcher_keeps_discovery_running_while_connected(monkeypatch):
    from src.sources.chromecast import CastWatcher
    watcher = CastWatcher('Living Room Projector')
    browser = FakeBrowser()
    cast = FakeCast(browser, watcher)
    fake = SimpleNamespace(get_listed_chromecasts=lambda **kw: ([cast], browser))
    monkeypatch.setitem(sys.modules, 'pychromecast', fake)
    watcher._stop.wait = lambda timeout=None: True   # no retry delays
    watcher._run()
    assert cast.discovery_running_at_connect
    assert browser.stopped


def make_watcher(paused_hide_seconds=60, grace_seconds=3):
    from src.sources.chromecast import CastWatcher
    clock = SimpleNamespace(t=0.0)
    w = CastWatcher('Living Room Projector', grace_seconds=grace_seconds,
                    paused_hide_seconds=paused_hide_seconds, clock=lambda: clock.t)
    return w, clock


def media(state):
    return SimpleNamespace(player_state=state)


def test_paused_video_stops_counting_as_casting():
    w, clock = make_watcher()
    w.new_cast_status(status('0BBC55A6', 'Nebula'))
    w.new_media_status(media('PLAYING'))
    assert w.active
    clock.t = 10
    w.new_media_status(media('PAUSED'))
    clock.t = 69
    assert w.active                       # a short pause keeps the frame
    clock.t = 71
    assert w.active                       # timed out, but within the grace period
    clock.t = 75
    assert not w.active
    w.new_media_status(media('PLAYING'))  # resuming brings it straight back
    assert w.active


def test_app_without_media_session_stays_active():
    w, clock = make_watcher()
    w.new_cast_status(status('233637DE', 'Some App'))
    clock.t = 1000
    assert w.active


def test_new_app_forgets_old_media_state():
    w, clock = make_watcher()
    w.new_cast_status(status('0BBC55A6', 'Nebula'))
    w.new_media_status(media('PAUSED'))
    clock.t = 100
    w.new_cast_status(status('233637DE', 'YouTube'))
    assert w.active


def test_idle_app_is_not_casting():
    w, clock = make_watcher()
    w.new_cast_status(status(BACKDROP_APP_ID, 'Backdrop'))
    assert not w.active
