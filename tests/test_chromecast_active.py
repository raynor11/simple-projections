import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sources.chromecast import is_casting, ActiveDebouncer, BACKDROP_APP_ID


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
