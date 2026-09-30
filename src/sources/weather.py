import datetime as dt
import json
import threading
import time
import urllib.parse
import urllib.request

import pygame

from ..location import resolve_location
from .text import TextSource, surface_to_rgba
from .weather_icons import draw_icon, icon_for_code


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
        'current': 'temperature_2m,weather_code,is_day',
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
    return {'temp': current['temperature_2m'], 'code': current['weather_code'],
            'is_day': bool(current.get('is_day', 1)), 'days': days}


# -- layout ------------------------------------------------------------------
#
# The forecast is a list of blocks, each a centred row of items or a table
# (rows whose columns line up). Items are ('text', str, scale) or
# ('icon', kind, scale); scale is relative to a base size that's searched
# for so the whole layout fills the frame.

ITEM_GAP = 0.3       # between items in a row, x base size
LINE_GAP = 0.14      # between rows, x base size


def weather_blocks(data, stale=False, title=None):
    blocks = []
    if title:
        blocks.append(('row', [('text', title, 0.7)]))
    if data is None:
        return blocks + [('row', [('text', "Loading weather…", 1.0)])]
    blocks.append(('row', [('icon', icon_for_code(data['code'], data.get('is_day', True)), 2.6),
                           ('text', f"{round(data['temp'])}°", 2.6)]))
    blocks.append(('row', [('text', describe(data['code']), 1.0)]))
    days = data['days']
    if days:
        today = days[0]
        blocks.append(('row', [('text', f"H {round(today['high'])}°   L {round(today['low'])}°", 0.8)]))
    if len(days) > 1:
        blocks.append(('table', [
            [('text', day['date'].strftime('%a'), 0.62), ('icon', icon_for_code(day['code']), 0.8),
             ('text', f"{round(day['high'])}° / {round(day['low'])}°", 0.62)]
            for day in days[1:4]
        ]))
    if stale:
        blocks.append(('row', [('text', "(offline — last update shown)", 0.45)]))
    return blocks


def _item_size(item, base, fonts):
    kind, value, scale = item
    px = max(1, round(base * scale))
    if kind == 'icon':
        return px, px
    font = fonts.get(px)
    return font.size(value)[0], font.get_height()


def _layout(blocks, base, fonts):
    """Sizes for every block at a base size: [(block, width, height, extra)], total width, total height."""
    gap, line_gap = base * ITEM_GAP, base * LINE_GAP
    measured, total_w, total_h = [], 0, 0
    for kind, content in blocks:
        rows = [content] if kind == 'row' else content
        sizes = [[_item_size(item, base, fonts) for item in row] for row in rows]
        columns = [max(row[i][0] for row in sizes) for i in range(len(sizes[0]))] if kind == 'table' else None
        row_heights = [max(h for _, h in row) for row in sizes]
        if kind == 'table':
            width = sum(columns) + gap * (len(columns) - 1)
        else:
            width = sum(w for w, _ in sizes[0]) + gap * (len(sizes[0]) - 1)
        height = sum(row_heights) + line_gap * (len(rows) - 1)
        measured.append((kind, rows, sizes, columns, row_heights, width, height))
        total_w = max(total_w, width)
        total_h += height
    total_h += line_gap * (len(blocks) - 1)
    return measured, total_w, total_h


def _fit_base(blocks, fonts, width, height):
    lo, hi, best = 4, max(4, height), 4
    while lo <= hi:
        mid = (lo + hi) // 2
        _, w, h = _layout(blocks, mid, fonts)
        if w <= width and h <= height:
            best, lo = mid, mid + 1
        else:
            hi = mid - 1
    return best


def _draw_item(surface, item, x, y, row_height, base, fonts, color):
    kind, value, scale = item
    w, h = _item_size(item, base, fonts)
    top = y + (row_height - h) / 2
    if kind == 'icon':
        surface.blit(draw_icon(value, w), (x, top))
    else:
        surface.blit(fonts.get(max(1, round(base * scale))).render(value, True, color), (x, top))
    return w


def render_blocks(blocks, size, fonts, color, background, padding=0.06):
    w, h = size
    pad = int(min(w, h) * padding)
    base = _fit_base(blocks, fonts, max(1, w - 2 * pad), max(1, h - 2 * pad))
    measured, _, total_h = _layout(blocks, base, fonts)
    gap, line_gap = base * ITEM_GAP, base * LINE_GAP

    surface = pygame.Surface((w, h), pygame.SRCALPHA)
    surface.fill(background)
    y = (h - total_h) / 2
    for kind, rows, sizes, columns, row_heights, block_w, block_h in measured:
        left = (w - block_w) / 2
        for row, row_sizes, row_h in zip(rows, sizes, row_heights):
            x = left
            for i, item in enumerate(row):
                if kind == 'table':
                    # Day names left-aligned, icons centred, temperatures left-aligned.
                    offset = (columns[i] - row_sizes[i][0]) / 2 if item[0] == 'icon' else 0
                    _draw_item(surface, item, x + offset, y, row_h, base, fonts, color)
                    x += columns[i] + gap
                else:
                    x += _draw_item(surface, item, x, y, row_h, base, fonts, color) + gap
            y += row_h + line_gap
    return surface_to_rgba(surface)


class WeatherSource(TextSource):
    """
    Current conditions and a 3-day forecast with icons, from Open-Meteo,
    refreshed every 10 minutes. Uses the configured location, or looks it
    up from the network's IP address when none is set.
    """

    def __init__(self, cfg, location=None):
        super().__init__(cfg)
        self.location_cfg = location
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
        if 'lat' in self.cfg and 'lon' in self.cfg:
            return self.cfg['lat'], self.cfg['lon']
        location = resolve_location(self.location_cfg)
        if location is None:
            return None, None
        return location['lat'], location['lon']

    def _run(self):
        warned = False
        while not self._stop.is_set():
            lat, lon = self._coords()
            if lat is None or lon is None:
                if not warned:
                    print("Weather: location unknown (no network yet?); retrying. "
                          "Set \"location\": {\"lat\": ..., \"lon\": ...} to skip the lookup.")
                    warned = True
                self._stop.wait(RETRY_SECONDS)
                continue
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

    def render(self, size):
        color, background = self.colors()
        blocks = weather_blocks(self._data, stale=self._is_stale(), title=self.cfg.get('title'))
        return render_blocks(blocks, size, self._fonts, color, background)

    def latest(self):
        stale = self._is_stale()
        if stale != self._was_stale:
            self._was_stale = stale
            self._dirty = True
        return super().latest()
