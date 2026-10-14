"""
A date and clock styled like an iPhone lock screen: the date in a smaller,
lighter line above a large time ("Tuesday, October 13" over "6:42").
"""
import datetime
from pathlib import Path

import pygame

from .base import Source
from .text import FontCache, blurred_copy, parse_color, render_size, surface_to_rgba


FONTS = Path(__file__).resolve().parents[2] / 'assets' / 'fonts'
TIME_FONT = FONTS / 'InterDisplay-SemiBold.ttf'
DATE_FONT = FONTS / 'InterDisplay-Medium.ttf'

DATE_SCALE = 0.2      # the date's font size, as a share of the time's (like the lock screen)
DATE_GAP = 0.02       # space between the date and the time, as a share of the time's size
PADDING = 0.04        # of the frame's smaller side
WIDEST_TIME = {'12h': '12:59', '24h': '23:59'}   # sized for the widest time, so it never jumps


def format_time(now, style='12h'):
    """'6:42' (12-hour, no AM/PM, like an iPhone) or '18:42'."""
    if style == '24h':
        return now.strftime('%H:%M')
    return f"{now.hour % 12 or 12}:{now.minute:02d}"


def format_date(now):
    """'Tuesday, October 13'."""
    return f"{now:%A}, {now:%B} {now.day}"


class ClockSource(Source):
    """
    Config: "format" ("12h" default, or "24h"), "color" (the time; default
    white), "date_color" (default slightly translucent white), "shadow"
    (default true: a soft shadow for contrast over video), "font" and
    "date_font" (default Inter Display, a free San Francisco look-alike).
    Redrawn only when the minute changes.
    """

    def __init__(self, cfg, now=datetime.datetime.now):
        super().__init__(cfg)
        self.now = now
        self._shown = None
        self._time_fonts = self._date_fonts = None

    def update(self, cfg):
        self.cfg = dict(cfg)
        self._time_fonts = self._date_fonts = None
        self._shown = None
        return True

    def set_target_size(self, width, height):
        super().set_target_size(width, height)
        self._shown = None

    def _fonts(self):
        if self._time_fonts is None:
            self._time_fonts = FontCache(self.cfg.get('font') or str(TIME_FONT))
            self._date_fonts = FontCache(self.cfg.get('date_font') or str(DATE_FONT))
        return self._time_fonts, self._date_fonts

    def _fit(self, date_text, width, height):
        """The largest time font size at which the widest time and the date fit width x height."""
        time_fonts, date_fonts = self._fonts()
        widest = WIDEST_TIME.get(self.cfg.get('format', '12h'), '12:59')

        def fits(size):
            time_w, time_h = time_fonts.get(size).size(widest)
            date_w, date_h = date_fonts.get(size * DATE_SCALE).size(date_text)
            return max(time_w, date_w) <= width and date_h + size * DATE_GAP + time_h <= height

        lo, hi, best = 6, max(6, height), 6
        while lo <= hi:
            mid = (lo + hi) // 2
            if fits(mid):
                best, lo = mid, mid + 1
            else:
                hi = mid - 1
        return best

    def render(self, size, now):
        w, h = size
        pad = int(min(w, h) * PADDING)
        time_text, date_text = format_time(now, self.cfg.get('format', '12h')), format_date(now)
        time_size = self._fit(date_text, max(1, w - 2 * pad), max(1, h - 2 * pad))
        time_fonts, date_fonts = self._fonts()
        time_font, date_font = time_fonts.get(time_size), date_fonts.get(time_size * DATE_SCALE)
        time_color = parse_color(self.cfg.get('color'), (255, 255, 255, 240))
        date_color = parse_color(self.cfg.get('date_color'), (255, 255, 255, 215))

        date_img = date_font.render(date_text, True, date_color)
        time_img = time_font.render(time_text, True, time_color)
        total = date_img.get_height() + int(time_size * DATE_GAP) + time_img.get_height()
        y = (h - total) // 2

        text = pygame.Surface((w, h), pygame.SRCALPHA)
        text.blit(date_img, ((w - date_img.get_width()) // 2, y))
        text.blit(time_img, ((w - time_img.get_width()) // 2,
                             y + date_img.get_height() + int(time_size * DATE_GAP)))

        surface = pygame.Surface((w, h), pygame.SRCALPHA)
        surface.fill(parse_color(self.cfg.get('background'), (0, 0, 0, 0)))
        if self.cfg.get('shadow', True):
            radius = max(2, time_size // 14)
            shadow = blurred_copy(pygame.surfarray.array_alpha(text).T, radius, (0, 0, 0), 0.9)
            surface.blit(shadow, (-radius, -radius + max(1, time_size // 60)))
        surface.blit(text, (0, 0))
        return surface_to_rgba(surface)

    def latest(self):
        now = self.now()
        key = (now.strftime('%Y-%m-%d %H:%M'), self.target_size)
        if key != self._shown:
            self._shown = key
            self._publish(self.render(render_size(self.target_size), now))
        return super().latest()
