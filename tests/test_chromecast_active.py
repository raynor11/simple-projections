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
