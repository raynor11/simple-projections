import datetime as dt
import json
import threading
import time
import urllib.parse
import urllib.request

from .text import TextSource


API_URL = "https://api.open-meteo.com/v1/forecast"
REFRESH_SECONDS = 600
RETRY_SECONDS = 60
# Show a stale marker once the data is this old (e.g. the network is down).
STALE_SECONDS = 3 * REFRESH_SECONDS

# WMO weather interpretation codes, as used by Open-Meteo.
WMO_CODES = {
    0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Freezing fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    66: "Freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Showers", 81: "Showers", 82: "Heavy showers",
    85: "Snow showers", 86: "Snow showers",
    95: "Thunderstorm", 96: "Thunderstorm, hail", 99: "Thunderstorm, hail",
}


def describe(code):
    return WMO_CODES.get(int(code), "Unknown") if code is not None else "Unknown"


def build_url(lat, lon, units):
    params = {
        'latitude': lat,
        'longitude': lon,
        'current': 'temperature_2m,weather_code',
        'daily': 'weather_code,temperature_2m_max,temperature_2m_min',
        'timezone': 'auto',
        'forecast_days': 4,
        'temperature_unit': 'fahrenheit' if units == 'imperial' else 'celsius',
    }
    return f"{API_URL}?{urllib.parse.urlencode(params)}"


def parse_forecast(payload):
    """Open-Meteo JSON -> {'temp', 'code', 'days': [{'date', 'code', 'high', 'low'}, ...]}."""
    current = payload['current']
    daily = payload['daily']
    days = [
        {'date': dt.date.fromisoformat(date), 'code': code, 'high': high, 'low': low}
        for date, code, high, low in zip(daily['time'], daily['weather_code'],
                                         daily['temperature_2m_max'], daily['temperature_2m_min'])
    ]
    return {'temp': current['temperature_2m'], 'code': current['weather_code'], 'days': days}


def weather_rows(data, stale=False, title=None):
    """The (text, relative size) rows to draw for the forecast."""
    rows = []
    if title:
        rows.append((title, 0.7))
    if data is None:
        return rows + [("Loading weather…", 1.0)]
    rows.append((f"{round(data['temp'])}°", 2.4))
    rows.append((describe(data['code']), 1.0))
    days = data['days']
    if days:
        today = days[0]
        rows.append((f"H {round(today['high'])}°   L {round(today['low'])}°", 0.8))
    for day in days[1:4]:
        rows.append((f"{day['date'].strftime('%a')}  {round(day['high'])}°/{round(day['low'])}°  "
                     f"{describe(day['code'])}", 0.6))
    if stale:
        rows.append(("(offline — last update shown)", 0.45))
    return rows


class WeatherSource(TextSource):
    """Current conditions and a 3-day forecast from Open-Meteo, refreshed every 10 minutes."""

    def __init__(self, cfg, location=None):
        super().__init__(cfg)
        self.location = location or {}
        self._data = None
        self._fetched_at = None
        self._stop = threading.Event()
        self._thread = None
        self._was_stale = False

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name='WeatherSource', daemon=True)
        self._thread.start()

    def close(self):
        self._stop.set()

    def update(self, cfg):
        refetch = any(cfg.get(k) != self.cfg.get(k) for k in ('lat', 'lon', 'units'))
        super().update(cfg)
        if refetch:
            self._fetched_at = None
            self._wake()
        return True

    def _wake(self):
        # Restart the fetch loop so config changes apply immediately.
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        self.start()

    def _coords(self):
        lat = self.cfg.get('lat', self.location.get('lat'))
        lon = self.cfg.get('lon', self.location.get('lon'))
        return lat, lon

    def _run(self):
        while not self._stop.is_set():
            lat, lon = self._coords()
            if lat is None or lon is None:
                print("Weather: no location configured (set top-level \"location\" or lat/lon on the source)")
                return
            try:
                url = build_url(lat, lon, self.cfg.get('units', 'imperial'))
                with urllib.request.urlopen(url, timeout=15) as resp:
                    self._data = parse_forecast(json.load(resp))
                self._fetched_at = time.monotonic()
                self._dirty = True
                wait = self.cfg.get('refresh_minutes', REFRESH_SECONDS / 60) * 60
            except Exception as e:
                print(f"Weather fetch failed: {e}")
                wait = RETRY_SECONDS
            self._stop.wait(wait)

    def _is_stale(self):
        return self._fetched_at is not None and time.monotonic() - self._fetched_at > STALE_SECONDS

    def rows(self):
        return weather_rows(self._data, stale=self._is_stale(), title=self.cfg.get('title'))

    def latest(self):
        stale = self._is_stale()
        if stale != self._was_stale:
            self._was_stale = stale
            self._dirty = True
        return super().latest()
