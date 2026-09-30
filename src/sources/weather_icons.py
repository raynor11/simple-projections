"""
Weather condition icons, drawn as vector shapes.

Each icon is drawn on a 100x100 unit grid at 4x the requested size and
smoothly scaled down, which anti-aliases the edges -- so they stay crisp
at any frame size without shipping image files.
"""

import math
from functools import lru_cache

import pygame


SUPERSAMPLE = 4
CLEAR = (0, 0, 0, 0)

SUN = (255, 196, 40, 255)
MOON = (236, 234, 214, 255)
CLOUD = (228, 233, 241, 255)
DARK_CLOUD = (156, 165, 182, 255)
BACK_CLOUD = (176, 184, 199, 255)
RAIN = (86, 172, 255, 255)
SNOW = (255, 255, 255, 255)
BOLT = (255, 214, 10, 255)
FOG = (196, 202, 212, 255)

ICON_KINDS = ('clear_day', 'clear_night', 'partly_day', 'partly_night', 'cloudy', 'fog',
              'drizzle', 'rain', 'freezing', 'snow', 'thunder')

# WMO weather codes (as used by Open-Meteo) -> icon, with day/night variants where it matters.
_CODE_ICONS = {
    0: 'clear', 1: 'clear', 2: 'partly', 3: 'cloudy',
    45: 'fog', 48: 'fog',
    51: 'drizzle', 53: 'drizzle', 55: 'drizzle', 56: 'freezing', 57: 'freezing',
    61: 'rain', 63: 'rain', 65: 'rain', 66: 'freezing', 67: 'freezing',
    71: 'snow', 73: 'snow', 75: 'snow', 77: 'snow',
    80: 'rain', 81: 'rain', 82: 'rain', 85: 'snow', 86: 'snow',
    95: 'thunder', 96: 'thunder', 99: 'thunder',
}


def icon_for_code(code, is_day=True):
    kind = _CODE_ICONS.get(int(code) if code is not None else -1, 'cloudy')
    if kind in ('clear', 'partly'):
        kind += '_day' if is_day else '_night'
    return kind


class _Pen:
    """Draws in 0..100 grid units onto the supersampled surface."""

    def __init__(self, surface):
        self.surface = surface
        self.k = surface.get_width() / 100.0

    def p(self, x, y):
        return (x * self.k, y * self.k)

    def circle(self, color, x, y, r):
        pygame.draw.circle(self.surface, color, self.p(x, y), r * self.k)

    def line(self, color, x0, y0, x1, y1, width):
        w = max(1, round(width * self.k))
        a, b = self.p(x0, y0), self.p(x1, y1)
        pygame.draw.line(self.surface, color, a, b, w)
        # Round the ends.
        pygame.draw.circle(self.surface, color, a, w / 2)
        pygame.draw.circle(self.surface, color, b, w / 2)

    def rect(self, color, x0, y0, x1, y1):
        pygame.draw.rect(self.surface, color, pygame.Rect(*self.p(x0, y0), (x1 - x0) * self.k,
                                                          (y1 - y0) * self.k))

    def polygon(self, color, points):
        pygame.draw.polygon(self.surface, color, [self.p(x, y) for x, y in points])


def _sun(pen, x, y, r, rays=True):
    pen.circle(SUN, x, y, r)
    if rays:
        for i in range(8):
            a = i * math.pi / 4
            c, s = math.cos(a), math.sin(a)
            pen.line(SUN, x + c * r * 1.38, y + s * r * 1.38, x + c * r * 1.72, y + s * r * 1.72, r * 0.2)


def _moon(pen, x, y, r):
    pen.circle(MOON, x, y, r)
    pen.circle(CLEAR, x + r * 0.5, y - r * 0.32, r * 0.84)


def _cloud(pen, x, y, w, color, gap=0.0):
    """A flat-bottomed cloud of width w centred on (x, y); gap > 0 draws a clear halo (to cut it out of what's behind)."""
    parts = ((-0.25, 0.05, 0.20), (-0.02, -0.10, 0.28), (0.25, 0.04, 0.21))
    for dx, dy, r in parts:
        pen.circle(color, x + dx * w, y + dy * w, r * w + gap)
    pen.rect(color, x - 0.25 * w - gap, y + 0.02 * w - gap, x + 0.25 * w + gap, y + 0.25 * w + gap)


def _cut_cloud(pen, x, y, w, color):
    _cloud(pen, x, y, w, CLEAR, gap=w * 0.05)
    _cloud(pen, x, y, w, color)


def _drops(pen, count, length, color=RAIN, width=5.0):
    xs = (32, 50, 68) if count == 3 else (38, 62)
    for i, x in enumerate(xs):
        y = 66 + (i % 2) * 6
        pen.line(color, x, y, x - length * 0.35, y + length, width)


def _flake(pen, x, y, r):
    for i in range(3):
        a = i * math.pi / 3 + math.pi / 2
        c, s = math.cos(a), math.sin(a)
        pen.line(SNOW, x - c * r, y - s * r, x + c * r, y + s * r, r * 0.32)


def _draw(kind, pen):
    if kind == 'clear_day':
        _sun(pen, 50, 50, 21)
    elif kind == 'clear_night':
        _moon(pen, 50, 50, 30)
    elif kind == 'partly_day':
        _sun(pen, 38, 37, 15)
        _cut_cloud(pen, 57, 60, 62, CLOUD)
    elif kind == 'partly_night':
        _moon(pen, 38, 34, 19)
        _cut_cloud(pen, 57, 60, 62, CLOUD)
    elif kind == 'cloudy':
        _cloud(pen, 38, 40, 54, BACK_CLOUD)
        _cut_cloud(pen, 57, 57, 70, CLOUD)
    elif kind == 'fog':
        _cloud(pen, 50, 36, 66, CLOUD)
        for y, x0, x1 in ((68, 18, 82), (80, 26, 74), (92, 34, 66)):
            pen.line(FOG, x0, y, x1, y, 5)
    elif kind in ('drizzle', 'rain', 'freezing', 'snow', 'thunder'):
        dark = kind in ('rain', 'thunder')
        _cloud(pen, 50, 38, 80, DARK_CLOUD if dark else CLOUD)
        if kind == 'drizzle':
            _drops(pen, 3, 9)
        elif kind == 'rain':
            _drops(pen, 3, 20)
        elif kind == 'freezing':
            pen.line(RAIN, 36, 66, 30, 84, 5)
            pen.line(RAIN, 66, 66, 60, 84, 5)
            _flake(pen, 50, 80, 8)
        elif kind == 'snow':
            for x, y in ((32, 72), (68, 72), (50, 86)):
                _flake(pen, x, y, 8)
        elif kind == 'thunder':
            pen.polygon(BOLT, [(56, 56), (38, 80), (50, 80), (42, 98), (66, 72), (53, 72), (62, 56)])
    else:
        raise ValueError(f"Unknown icon: {kind!r}")


@lru_cache(maxsize=64)
def draw_icon(kind, size):
    """A size x size RGBA pygame Surface with the icon."""
    size = max(4, int(size))
    big = pygame.Surface((size * SUPERSAMPLE, size * SUPERSAMPLE), pygame.SRCALPHA)
    big.fill(CLEAR)
    _draw(kind, _Pen(big))
    return pygame.transform.smoothscale(big, (size, size))
