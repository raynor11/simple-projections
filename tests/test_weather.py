import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sources.weather import parse_forecast, weather_rows, build_url, describe


PAYLOAD = {
    'current': {'temperature_2m': 71.6, 'weather_code': 2},
    'daily': {
        'time': ['2026-09-29', '2026-09-30', '2026-10-01', '2026-10-02'],
        'weather_code': [2, 61, 0, 95],
        'temperature_2m_max': [78.2, 70.1, 74.0, 80.5],
        'temperature_2m_min': [60.9, 58.0, 55.5, 66.0],
    },
}


def test_parse_forecast():
    data = parse_forecast(PAYLOAD)
    assert data['temp'] == 71.6
    assert data['days'][1]['date'] == dt.date(2026, 9, 30)
    assert len(data['days']) == 4


def test_weather_rows():
    rows = [text for text, _ in weather_rows(parse_forecast(PAYLOAD))]
    assert rows[0] == "72°"
    assert rows[1] == "Partly cloudy"
    assert rows[2] == "H 78°   L 61°"
    assert rows[3].startswith("Wed") and "Light rain" in rows[3]
    assert len(rows) == 6


def test_weather_rows_loading_and_stale():
    assert weather_rows(None)[0][0].startswith("Loading")
    assert "offline" in weather_rows(parse_forecast(PAYLOAD), stale=True)[-1][0]


def test_build_url_units():
    assert 'temperature_unit=fahrenheit' in build_url(1, 2, 'imperial')
    assert 'temperature_unit=celsius' in build_url(1, 2, 'metric')


def test_describe_unknown():
    assert describe(1234) == "Unknown"
