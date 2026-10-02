"""
Projector light output by time of day, following sunrise and sunset.

Sun times are computed locally (astral) from the configured location, so
this works without internet. The LS740-4K's light output is set through
its discrete light source modes, so each phase of the day maps to a mode:

  "brightness": {"day": "normal", "dusk": "eco", "night": "eco", "twilight_offset_min": 30}

The LS740-4K offers only Normal, Eco (about 20% dimmer) and Dynamic Black
over RS-232; its finer "Light Source Power" percentage is OSD-only. For a
dimmer night, the renderer can also scale its own output: see dim_at().

dusk covers civil twilight (dawn -> sunrise and sunset -> dusk), widened
by twilight_offset_min on the daylight side.
"""

import datetime as dt
from zoneinfo import ZoneInfo


DEFAULT_MODES = {'day': 'normal', 'dusk': 'eco', 'night': 'eco'}


def sun_times(date, lat, lon, tz):
    """(dawn, sunrise, sunset, dusk) for a date, or None if the sun doesn't rise/set that day."""
    from astral import Observer
    from astral.sun import sun
    try:
        s = sun(Observer(latitude=lat, longitude=lon), date=date, tzinfo=tz)
    except ValueError:
        return None
    return s['dawn'], s['sunrise'], s['sunset'], s['dusk']


def phase_at(when, lat, lon, twilight_offset_min=30):
    """'day', 'dusk' or 'night' for a timezone-aware datetime."""
    times = sun_times(when.date(), lat, lon, when.tzinfo)
    if times is None:
        # Polar day/night: fall back to whether the sun is up at noon.
        from astral import Observer
        from astral.sun import elevation
        noon = when.replace(hour=12, minute=0)
        return 'day' if elevation(Observer(latitude=lat, longitude=lon), noon) > 0 else 'night'
    dawn, sunrise, sunset, dusk = times
    offset = dt.timedelta(minutes=twilight_offset_min)
    if sunrise + offset <= when < sunset - offset:
        return 'day'
    if dawn <= when < sunrise + offset or sunset - offset <= when < dusk:
        return 'dusk'
    return 'night'


def mode_at(when, location, brightness_cfg):
    """The light source mode to use at `when`."""
    cfg = {**DEFAULT_MODES, **(brightness_cfg or {})}
    phase = phase_at(when, location['lat'], location['lon'], cfg.get('twilight_offset_min', 30))
    return cfg[phase]


def dim_at(when, location, brightness_cfg):
    """
    Software brightness factor (0.1-1.0) for the renderer at `when`, from
    "software_dim": {"day": 1.0, "dusk": 0.9, "night": 0.75}. Phases not
    listed (or no software_dim at all) mean full brightness.
    """
    dim = (brightness_cfg or {}).get('software_dim') or {}
    if not dim:
        return 1.0
    phase = phase_at(when, location['lat'], location['lon'], (brightness_cfg or {}).get('twilight_offset_min', 30))
    return float(dim.get(phase, 1.0))


def local_now(location):
    tz = location.get('timezone')
    return dt.datetime.now(ZoneInfo(tz)) if tz else dt.datetime.now().astimezone()
