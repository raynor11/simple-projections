import datetime as dt
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.projector.brightness import mode_at, phase_at

CHICAGO = {'lat': 41.88, 'lon': -87.63, 'timezone': 'America/Chicago'}
TZ = ZoneInfo('America/Chicago')
# 2026-09-29 in Chicago: sunrise ~6:48, sunset ~18:36, civil dusk ~19:03.


def at(hour, minute=0):
    return dt.datetime(2026, 9, 29, hour, minute, tzinfo=TZ)


def test_phases():
    assert phase_at(at(12), CHICAGO['lat'], CHICAGO['lon']) == 'day'
    assert phase_at(at(0), CHICAGO['lat'], CHICAGO['lon']) == 'night'
    assert phase_at(at(18, 45), CHICAGO['lat'], CHICAGO['lon']) == 'dusk'   # after sunset, before dusk
    assert phase_at(at(18, 20), CHICAGO['lat'], CHICAGO['lon']) == 'dusk'   # within the offset before sunset
    assert phase_at(at(18, 20), CHICAGO['lat'], CHICAGO['lon'], twilight_offset_min=0) == 'day'
    assert phase_at(at(6, 40), CHICAGO['lat'], CHICAGO['lon']) == 'dusk'    # dawn twilight
    assert phase_at(at(22), CHICAGO['lat'], CHICAGO['lon']) == 'night'


def test_mode_at_uses_configured_modes():
    cfg = {'day': 'normal', 'dusk': 'dynamic_eco', 'night': 'supereco'}
    assert mode_at(at(12), CHICAGO, cfg) == 'normal'
    assert mode_at(at(18, 45), CHICAGO, cfg) == 'dynamic_eco'
    assert mode_at(at(23), CHICAGO, cfg) == 'supereco'


def test_polar_night_falls_back():
    when = dt.datetime(2026, 12, 21, 12, 0, tzinfo=dt.timezone.utc)
    assert phase_at(when, 85.0, 0.0) == 'night'
