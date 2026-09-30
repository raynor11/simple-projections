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
    def __init__(self, path=None):
        self.path = path
        self._fonts = {}

    def get(self, size):
        size = max(1, int(size))
        font = self._fonts.get(size)
        if font is None:
            if not pygame.font.get_init():
                pygame.font.init()
            font = self._fonts[size] = pygame.font.Font(self.path, size)
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


def layout_rows(rows, fonts, base_size, max_width):
    """Wrap each (text, scale) row at base_size*scale. Returns [(font, [lines])] and total height."""
    laid_out = []
    total = 0
    for text, scale in rows:
        font = fonts.get(base_size * scale)
        lines = wrap_text(text, font, max_width)
        laid_out.append((font, lines))
        total += font.get_linesize() * len(lines)
    return laid_out, total


def fit_rows(rows, fonts, width, height, min_size=6, max_size=600):
    """
    Largest base font size at which every row fits inside width x height.
    Returns (base_size, laid_out rows).
    """
    def fits(size):
        laid_out, total = layout_rows(rows, fonts, size, width)
        widest = max((font.size(line)[0] for font, lines in laid_out for line in lines), default=0)
        return total <= height and widest <= width, laid_out

    best_size = min_size
    best = layout_rows(rows, fonts, min_size, width)[0]
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


def render_rows(rows, size, fonts, color, background, align='center', padding=0.06):
    """Render (text, scale) rows auto-fitted into a size=(w, h) RGBA image."""
    w, h = size
    pad = int(min(w, h) * padding)
    inner_w, inner_h = max(1, w - 2 * pad), max(1, h - 2 * pad)
    _, laid_out = fit_rows(rows, fonts, inner_w, inner_h)

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

    def latest(self):
        if self._dirty:
            self._dirty = False
            self._publish(render_rows(
                self.rows(), render_size(self.target_size), self._fonts,
                color=parse_color(self.cfg.get('color'), (255, 255, 255, 255)),
                background=parse_color(self.cfg.get('background'), (0, 0, 0, 0)),
                align=self.cfg.get('align', 'center'),
            ))
        return super().latest()
