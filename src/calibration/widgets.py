"""
Drawing helpers for the calibration UI.

Accessibility: nothing is told apart by colour alone. Colours come from
the Okabe-Ito palette (distinguishable with the common kinds of colour
blindness), and every state also has a shape, weight, pattern or text
cue: selected frames are filled and thicker, hidden frames are dashed,
corners are numbered to match their keys, and toggled buttons invert
from dark to light rather than changing hue.
"""

from dataclasses import dataclass

import numpy as np
import pygame


# Okabe-Ito colour-blind-safe palette.
ORANGE = (230, 159, 0)
SKY_BLUE = (86, 180, 233)
BLUISH_GREEN = (0, 158, 115)
YELLOW = (240, 228, 66)
BLUE = (0, 114, 178)
VERMILLION = (213, 94, 0)
REDDISH_PURPLE = (204, 121, 167)

BACKGROUND = (18, 18, 18)
GRID = (45, 45, 45)
FRAME = (170, 170, 170)
FRAME_HOVER = (235, 235, 235)
SELECTED = (255, 255, 255)
SCREEN = SKY_BLUE
GUIDE = REDDISH_PURPLE
ACCENT = ORANGE                  # the handle being dragged / the selected corner
TEXT = (230, 230, 230)
TEXT_DIM = (160, 160, 160)
PANEL = (0, 0, 0, 200)
BUTTON = (48, 48, 48)
BUTTON_HOVER = (78, 78, 78)
BUTTON_ON = (235, 235, 235)       # toggled on: light button, dark text
BUTTON_ON_TEXT = (15, 15, 15)
BUTTON_BORDER = (120, 120, 120)


class Fonts:
    def __init__(self):
        self._cache = {}

    def get(self, size):
        size = max(8, int(size))
        if size not in self._cache:
            self._cache[size] = pygame.font.Font(None, size)
        return self._cache[size]


def text_with_backing(surface, font, text, pos, color=TEXT, pad=4, alpha=190):
    """Text on a dark translucent box, readable over anything."""
    img = font.render(text, True, color)
    box = pygame.Surface((img.get_width() + 2 * pad, img.get_height() + 2 * pad), pygame.SRCALPHA)
    box.fill((0, 0, 0, alpha))
    box.blit(img, (pad, pad))
    surface.blit(box, pos)
    return box.get_size()


def polyline(surface, points, color, width, closed=True, dashed=False, dash=10, gap=7):
    pts = [np.asarray(p, float) for p in points]
    pairs = list(zip(pts, pts[1:] + pts[:1])) if closed else list(zip(pts, pts[1:]))
    for a, b in pairs:
        if not dashed:
            pygame.draw.line(surface, color, a, b, width)
            continue
        length = np.linalg.norm(b - a)
        t = 0.0
        while t < length:
            p1 = a + (b - a) * (t / length)
            p2 = a + (b - a) * (min(length, t + dash) / length)
            pygame.draw.line(surface, color, p1, p2, width)
            t += dash + gap


def fill_polygon(surface, points, rgba):
    """Translucent polygon fill (blended in place, without allocating a full-window layer)."""
    import pygame.gfxdraw
    pygame.gfxdraw.filled_polygon(surface, [(int(round(x)), int(round(y))) for x, y in points], rgba)


def corner_handle(surface, pos, number, font, radius, active=False):
    """A numbered circular handle (the number is the key that selects it)."""
    r = radius + (3 if active else 0)
    pygame.draw.circle(surface, (0, 0, 0), pos, r + 2)
    pygame.draw.circle(surface, ACCENT if active else SELECTED, pos, r)
    label = font.render(str(number), True, (0, 0, 0))
    surface.blit(label, label.get_rect(center=(pos[0], pos[1] + 1)))


def square_handle(surface, pos, size, active=False):
    """A resize handle: a black-outlined square, orange while active."""
    s = size + (3 if active else 0)
    rect = pygame.Rect(0, 0, s * 2, s * 2)
    rect.center = pos
    pygame.draw.rect(surface, (0, 0, 0), rect.inflate(4, 4))
    pygame.draw.rect(surface, ACCENT if active else SELECTED, rect)


@dataclass
class Button:
    id: str
    label: str
    shortcut: str
    enabled: bool = True
    on: bool = False
    rect: pygame.Rect = None


def layout_buttons(buttons, font, width, margin, gap):
    """Place buttons left to right, wrapping to new rows. Returns the toolbar height."""
    x, y = margin, margin
    row_h = font.get_linesize() + 2 * gap
    for button in buttons:
        w = font.size(f"{button.label}  {button.shortcut}")[0] + 3 * gap
        if x + w > width - margin and x > margin:
            x, y = margin, y + row_h + gap
        button.rect = pygame.Rect(x, y, w, row_h)
        x += w + gap
    return y + row_h + margin


def draw_button(surface, button, font, hover):
    if button.on:
        bg, fg, hint = BUTTON_ON, BUTTON_ON_TEXT, (70, 70, 70)
    else:
        bg = BUTTON_HOVER if hover and button.enabled else BUTTON
        fg = TEXT if button.enabled else (110, 110, 110)
        hint = TEXT_DIM if button.enabled else (90, 90, 90)
    pygame.draw.rect(surface, bg, button.rect, border_radius=6)
    pygame.draw.rect(surface, SELECTED if hover and button.enabled else BUTTON_BORDER,
                     button.rect, width=2 if hover else 1, border_radius=6)
    label = font.render(button.label, True, fg)
    shortcut = font.render(button.shortcut, True, hint)
    pad = (button.rect.width - label.get_width() - shortcut.get_width()) // 3
    y = button.rect.centery - label.get_height() // 2
    surface.blit(label, (button.rect.x + pad, y))
    surface.blit(shortcut, (button.rect.right - pad - shortcut.get_width(), y))
