import os
import sys
from pathlib import Path

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
sys.path.insert(0, str(Path(__file__).parent.parent))

import pygame
import pytest

from src.sources.text import FontCache, fit_rows, wrap_text, parse_color, TextSource


@pytest.fixture(scope='module', autouse=True)
def fonts_ready():
    pygame.font.init()
    yield


def test_wrap_text_respects_width_and_newlines():
    font = FontCache().get(20)
    lines = wrap_text("one two three four five six seven\nnext", font, 120)
    assert len(lines) > 2
    assert lines[-1] == "next"
    assert all(font.size(line)[0] <= 120 for line in lines if ' ' in line)


@pytest.mark.parametrize("size", [(400, 100), (200, 300), (900, 500)])
def test_fit_rows_fits_and_is_maximal(size):
    fonts = FontCache()
    rows = [("The quick brown fox jumps over the lazy dog", 1.0)]
    w, h = size
    base, laid_out = fit_rows(rows, fonts, w, h)
    total = sum(font.get_linesize() * len(lines) for font, lines in laid_out)
    widest = max(font.size(line)[0] for font, lines in laid_out for line in lines)
    assert total <= h and widest <= w
    # One size bigger no longer fits (the search found the largest).
    bigger = fonts.get(base + 1)
    bigger_lines = wrap_text(rows[0][0], bigger, w)
    assert (bigger.get_linesize() * len(bigger_lines) > h
            or max(bigger.size(line)[0] for line in bigger_lines) > w)


def test_parse_color():
    assert parse_color('#fff', None) == (255, 255, 255, 255)
    assert parse_color('#00000080', None) == (0, 0, 0, 128)
    assert parse_color(None, (1, 2, 3, 4)) == (1, 2, 3, 4)


def test_text_source_renders_at_target_size_and_rerenders_on_change():
    src = TextSource({'type': 'text', 'text': 'Hello'})
    src.set_target_size(320, 180)
    version, img = src.latest()
    assert img.shape == (180, 320, 4)
    assert img[..., 3].max() == 255          # some opaque text pixels
    assert src.latest()[0] == version        # unchanged -> same version
    src.update({'type': 'text', 'text': 'Changed'})
    assert src.latest()[0] != version


def test_font_cache_is_bounded():
    fonts = FontCache()
    for size in range(10, 80):
        fonts.get(size)
    assert len(fonts._fonts) == FontCache.MAX_FONTS
    assert 79 in fonts._fonts and 10 not in fonts._fonts
