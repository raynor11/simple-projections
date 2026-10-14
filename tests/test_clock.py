import datetime
import sys
from pathlib import Path

import pygame

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sources import create_source
from src.sources.clock import ClockSource, TIME_FONT, DATE_FONT, format_date, format_time


def test_formats_like_an_iphone():
    evening = datetime.datetime(2026, 10, 13, 18, 42)
    assert format_time(evening) == '6:42'
    assert format_time(datetime.datetime(2026, 10, 13, 0, 5)) == '12:05'
    assert format_time(evening, '24h') == '18:42'
    assert format_date(evening) == 'Tuesday, October 13'


def test_bundled_fonts_exist():
    assert TIME_FONT.exists() and DATE_FONT.exists()


def test_redraws_only_when_the_minute_changes():
    pygame.font.init()
    t = [datetime.datetime(2026, 10, 13, 18, 42, 5)]
    clock = ClockSource({'type': 'clock'}, now=lambda: t[0])
    clock.set_target_size(800, 400)
    v1 = clock.latest()[0]
    t[0] = t[0].replace(second=50)
    assert clock.latest()[0] == v1
    t[0] = t[0].replace(minute=43, second=0)
    assert clock.latest()[0] != v1


def test_clock_type_is_registered():
    assert isinstance(create_source({'type': 'clock'}), ClockSource)
