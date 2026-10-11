import math
import time
from collections import OrderedDict

import cv2
import numpy as np
import pygame

from .base import Source


DEFAULT_SIZE = (800, 450)
MAX_RENDER_SIZE = (1920, 1080)


def parse_color(value, default):
    """'#rgb', '#rrggbb' or '#rrggbbaa' (or an [r, g, b(, a)] list) -> RGBA tuple."""
    if value is None:
        return default
    if isinstance(value, (list, tuple)):
        rgba = tuple(int(v) for v in value)
        return rgba if len(rgba) == 4 else rgba + (255,)
    s = str(value).lstrip('#')
    if len(s) == 3:
        s = ''.join(c * 2 for c in s)
    if len(s) == 6:
        s += 'ff'
    if len(s) != 8:
        raise ValueError(f"Bad color: {value!r}")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4, 6))


class FontCache:
    """
    Fonts by size, keeping the most recently used. Auto-fitting tries many
    sizes, so the cache is bounded rather than growing for as long as the
    app runs.
    """

    MAX_FONTS = 24

    def __init__(self, path=None):
        self.path = path
        self._fonts = OrderedDict()

    def get(self, size):
        size = max(1, int(size))
        font = self._fonts.get(size)
        if font is None:
            if not pygame.font.get_init():
                pygame.font.init()
            font = self._fonts[size] = pygame.font.Font(self.path, size)
            while len(self._fonts) > self.MAX_FONTS:
                self._fonts.popitem(last=False)
        else:
            self._fonts.move_to_end(size)
        return font


def wrap_text(text, font, max_width):
    """Word-wrap text to max_width pixels. Honours explicit newlines; a single overlong word gets its own line."""
    lines = []
    for paragraph in str(text).split('\n'):
        words = paragraph.split(' ')
        line = ''
        for word in words:
            candidate = word if not line else f"{line} {word}"
            if font.size(candidate)[0] <= max_width or not line:
                line = candidate
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


def layout_rows(rows, fonts, base_size, max_width, wrap=True):
    """
    Wrap each (text, scale) row at base_size*scale. Returns [(font, [lines])]
    and total height. With wrap=False only explicit newlines break lines.
    """
    laid_out = []
    total = 0
    for text, scale in rows:
        font = fonts.get(base_size * scale)
        lines = wrap_text(text, font, max_width) if wrap else str(text).split('\n')
        laid_out.append((font, lines))
        total += font.get_linesize() * len(lines)
    return laid_out, total


def fit_rows(rows, fonts, width, height, min_size=6, max_size=600, wrap=True):
    """
    Largest base font size at which every row fits inside width x height.
    Returns (base_size, laid_out rows).
    """
    def fits(size):
        laid_out, total = layout_rows(rows, fonts, size, width, wrap)
        widest = max((font.size(line)[0] for font, lines in laid_out for line in lines), default=0)
        return total <= height and widest <= width, laid_out

    best_size = min_size
    best = layout_rows(rows, fonts, min_size, width, wrap)[0]
    lo, hi = min_size + 1, max(min_size, min(max_size, height))
    while lo <= hi:
        mid = (lo + hi) // 2
        ok, laid_out = fits(mid)
        if ok:
            best_size, best, lo = mid, laid_out, mid + 1
        else:
            hi = mid - 1
    return best_size, best


def surface_to_rgba(surface):
    to_bytes = getattr(pygame.image, 'tobytes', None) or pygame.image.tostring
    w, h = surface.get_size()
    return np.frombuffer(to_bytes(surface, 'RGBA'), dtype=np.uint8).reshape(h, w, 4).copy()


def render_rows(rows, size, fonts, color, background, align='center', padding=0.06, wrap=True):
    """Render (text, scale) rows auto-fitted into a size=(w, h) RGBA image."""
    w, h = size
    pad = int(min(w, h) * padding)
    inner_w, inner_h = max(1, w - 2 * pad), max(1, h - 2 * pad)
    _, laid_out = fit_rows(rows, fonts, inner_w, inner_h, wrap=wrap)

    surface = pygame.Surface((w, h), pygame.SRCALPHA)
    surface.fill(background)
    total = sum(font.get_linesize() * len(lines) for font, lines in laid_out)
    y = pad + (inner_h - total) // 2
    for font, lines in laid_out:
        for line in lines:
            img = font.render(line, True, color)
            if align == 'left':
                x = pad
            elif align == 'right':
                x = w - pad - img.get_width()
            else:
                x = (w - img.get_width()) // 2
            surface.blit(img, (x, y))
            y += font.get_linesize()
    return surface_to_rgba(surface)


def render_size(target_size):
    tw, th = target_size or DEFAULT_SIZE
    mw, mh = MAX_RENDER_SIZE
    scale = min(1.0, mw / tw, mh / th)
    return max(1, int(tw * scale)), max(1, int(th * scale))


class TextSource(Source):
    """
    Static text, word-wrapped and auto-sized to fill the frame. Rendered at
    the frame's on-canvas pixel size so it stays crisp, and only re-rendered
    when the text, style or frame size changes.
    """

    def __init__(self, cfg):
        super().__init__(cfg)
        self._dirty = True
        self._fonts = FontCache(cfg.get('font'))

    def update(self, cfg):
        if cfg.get('font') != self.cfg.get('font'):
            self._fonts = FontCache(cfg.get('font'))
        self.cfg = dict(cfg)
        self._dirty = True
        return True

    def set_target_size(self, width, height):
        old = self.target_size
        super().set_target_size(width, height)
        if self.target_size != old:
            self._dirty = True

    def rows(self):
        return [(self.cfg.get('text', ''), 1.0)]

    def colors(self):
        return (parse_color(self.cfg.get('color'), (255, 255, 255, 255)),
                parse_color(self.cfg.get('background'), (0, 0, 0, 0)))

    def render(self, size):
        """Draw the content at size=(w, h); returns an RGBA array."""
        color, background = self.colors()
        return render_rows(self.rows(), size, self._fonts, color=color, background=background,
                           align=self.cfg.get('align', 'center'), wrap=self.cfg.get('wrap', True))

    def latest(self):
        if self._dirty:
            self._dirty = False
            self._publish(self.render(render_size(self.target_size)))
        return super().latest()


class AnimatedTextSource(TextSource):
    """
    Text that moves, for occasions like a welcome-home message. "animation"
    lists the effects:

    - "wave": the letters bob up and down in turn, so a ripple runs through them.
    - "glow": a soft glow (glow_color) behind the letters brightens and dims.

    Every letter, its glow and a soft dark shadow (which keeps light text
    readable over a bright video) are drawn once; each animation frame only
    moves and fades them, so it stays cheap on the Pi.
    """

    FPS = 24           # match typical video; each frame is a texture upload
    # Text fills this much of the frame: room above and below for the wave
    # and glow, only a little at the sides for the glow.
    SHRINK_X, SHRINK_Y = 0.94, 0.8

    def __init__(self, cfg, clock=time.monotonic):
        super().__init__(cfg)
        self.clock = clock
        self._glyphs = None
        self._size = None
        self._last_frame = None

    def update(self, cfg):
        super().update(cfg)
        self._glyphs = None
        return True

    def effects(self):
        value = self.cfg.get('animation') or []
        return {value} if isinstance(value, str) else set(value)

    def _blurred(self, alpha, radius, color, strength):
        """A blurred, coloured copy of a glyph's alpha, padded by radius on every side."""
        padded = np.pad(alpha, radius)
        blur = cv2.GaussianBlur(padded.astype(np.float32), (0, 0), max(1.0, radius / 2.5))
        a = np.clip(blur * strength, 0, 255).astype(np.uint8)
        rgba = np.empty(a.shape + (4,), np.uint8)
        rgba[..., :3] = color[:3]
        rgba[..., 3] = a
        h, w = a.shape
        return pygame.image.frombuffer(rgba.tobytes(), (w, h), 'RGBA').copy()

    def _build(self, size):
        """Lay the text out once and pre-render each letter with its glow and shadow."""
        w, h = size
        color, _ = self.colors()
        glow_color = parse_color(self.cfg.get('glow_color'), (255, 200, 90, 255))
        pad = int(min(w, h) * 0.06)
        inner_w, inner_h = max(1, int((w - 2 * pad) * self.SHRINK_X)), max(1, int((h - 2 * pad) * self.SHRINK_Y))
        _, laid_out = fit_rows(self.rows(), self._fonts, inner_w, inner_h, wrap=self.cfg.get('wrap', True))
        total = sum(font.get_linesize() * len(lines) for font, lines in laid_out)
        y = (h - total) // 2
        align = self.cfg.get('align', 'center')
        glyphs = []
        line_height = 1
        for font, lines in laid_out:
            line_height = font.get_linesize()
            radius = max(2, int(line_height * 0.22))
            for line in lines:
                width = font.size(line)[0]
                x0 = pad if align == 'left' else (w - pad - width if align == 'right' else (w - width) // 2)
                for i, ch in enumerate(line):
                    if ch.isspace():
                        continue
                    img = font.render(ch, True, color)
                    alpha = pygame.surfarray.array_alpha(img).T          # (h, w)
                    glyphs.append({
                        'img': img,
                        'pos': (x0 + font.size(line[:i])[0], y),
                        'radius': radius,
                        'glow': self._blurred(alpha, radius, glow_color, 2.2),
                        'shadow': self._blurred(alpha, radius, (0, 0, 0), 1.6),
                    })
                y += font.get_linesize()
        self._glyphs, self._size, self._line_height = glyphs, size, line_height

    def render(self, size):
        if self._glyphs is None or self._size != size:
            self._build(size)
        effects = self.effects()
        t = self.clock()
        _, background = self.colors()
        surface = pygame.Surface(size, pygame.SRCALPHA)
        surface.fill(background)
        amplitude = float(self.cfg.get('wave_height', 0.12)) * self._line_height if 'wave' in effects else 0
        wave_period = float(self.cfg.get('wave_seconds', 1.8))
        glow_period = float(self.cfg.get('glow_seconds', 2.4))
        pulse = 0.5 + 0.5 * math.sin(2 * math.pi * t / glow_period)
        glow_alpha = int(255 * (0.35 + 0.65 * pulse))
        shadow_offset = max(1, self._line_height // 30)
        for i, g in enumerate(self._glyphs):
            dy = amplitude * math.sin(2 * math.pi * t / wave_period - i * 0.45)
            x, y = g['pos'][0], g['pos'][1] + dy
            r = g['radius']
            surface.blit(g['shadow'], (x - r + shadow_offset, y - r + shadow_offset))
            if 'glow' in effects:
                g['glow'].set_alpha(glow_alpha)
                surface.blit(g['glow'], (x - r, y - r))
            surface.blit(g['img'], (x, y))
        return surface_to_rgba(surface)

    def latest(self):
        now = self.clock()
        if self._dirty or self._last_frame is None or now - self._last_frame >= 1 / self.FPS:
            self._dirty = False
            self._last_frame = now
            self._publish(self.render(render_size(self.target_size)))
        return Source.latest(self)
