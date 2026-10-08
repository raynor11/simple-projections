import datetime as dt
import os
import sys
from pathlib import Path

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pygame
import pytest

from src.sources.text import FontCache
from src.sources.weather import build_url, describe, parse_forecast, render_blocks, weather_blocks
from src.sources.weather_icons import ICON_KINDS, draw_icon, icon_for_code


PAYLOAD = {
    'current': {'temperature_2m': 71.6, 'weather_code': 2, 'is_day': 0},
    'daily': {
        'time': ['2026-09-29', '2026-09-30', '2026-10-01', '2026-10-02'],
        'weather_code': [2, 61, 0, 95],
        'temperature_2m_max': [78.2, 70.1, 74.0, 80.5],
        'temperature_2m_min': [60.9, 58.0, 55.5, 66.0],
    },
}


@pytest.fixture(scope='module', autouse=True)
def pygame_ready():
    pygame.init()
    yield


def texts(blocks):
    out = []
    for kind, content in blocks:
        for row in ([content] if kind == 'row' else content):
            out.append([value for _, value, _ in row])
    return out


def test_parse_forecast():
    data = parse_forecast(PAYLOAD)
    assert data['temp'] == 71.6
    assert data['is_day'] is False
    assert data['days'][1]['date'] == dt.date(2026, 9, 30)
    assert len(data['days']) == 4


def test_weather_blocks():
    rows = texts(weather_blocks(parse_forecast(PAYLOAD)))
    assert rows[0] == ['partly_night', '72°']      # current icon follows day/night
    assert rows[1] == ['Partly cloudy']
    assert rows[2] == ['H 78°   L 61°']
    assert rows[3] == ['Wed', 'rain', '70° / 58°']
    assert rows[5] == ['Fri', 'thunder', '80° / 66°']
    assert len(rows) == 6


def test_weather_blocks_loading_title_and_stale():
    assert texts(weather_blocks(None))[0][0].startswith("Loading")
    assert texts(weather_blocks(parse_forecast(PAYLOAD), title="Home"))[0] == ["Home"]
    assert "offline" in texts(weather_blocks(parse_forecast(PAYLOAD), stale=True))[-1][0]


@pytest.mark.parametrize('code, is_day, kind', [
    (0, True, 'clear_day'), (0, False, 'clear_night'), (2, False, 'partly_night'), (3, True, 'cloudy'),
    (45, True, 'fog'), (53, True, 'drizzle'), (63, True, 'rain'), (66, True, 'freezing'),
    (75, True, 'snow'), (81, True, 'rain'), (96, True, 'thunder'), (1234, True, 'cloudy'),
])
def test_icon_for_code(code, is_day, kind):
    assert icon_for_code(code, is_day) == kind


@pytest.mark.parametrize('kind', ICON_KINDS)
def test_every_icon_draws(kind):
    icon = draw_icon(kind, 64)
    assert icon.get_size() == (64, 64)
    alpha = np.array(pygame.surfarray.pixels_alpha(icon))
    assert alpha.max() == 255 and alpha.min() == 0     # something drawn, background clear


@pytest.mark.parametrize('size', [(560, 400), (300, 600), (900, 260)])
def test_render_fills_frame_with_content(size):
    img = render_blocks(weather_blocks(parse_forecast(PAYLOAD)), size, FontCache(),
                        (255, 255, 255, 255), (0, 0, 0, 0))
    assert img.shape == (size[1], size[0], 4)
    drawn = np.argwhere(img[..., 3] > 0)
    # Content stays inside the frame and uses a good share of it.
    (y0, x0), (y1, x1) = drawn.min(axis=0), drawn.max(axis=0)
    assert (x1 - x0) > 0.5 * size[0] or (y1 - y0) > 0.5 * size[1]


def test_build_url():
    assert 'temperature_unit=fahrenheit' in build_url(1, 2, 'imperial')
    assert 'temperature_unit=celsius' in build_url(1, 2, 'metric')
    assert 'is_day' in build_url(1, 2, 'imperial')


def test_describe_unknown():
    assert describe(1234) == "Unknown"


def test_retry_backs_off_from_a_quick_first_retry():
    from src.sources.weather import next_retry, FIRST_RETRY_SECONDS, RETRY_SECONDS
    waits = [None]
    for _ in range(6):
        waits.append(next_retry(waits[-1]))
    assert waits[1] == FIRST_RETRY_SECONDS == 5
    assert waits[1:] == [5, 10, 20, 40, 60, 60]
    assert max(waits[1:]) == RETRY_SECONDS
