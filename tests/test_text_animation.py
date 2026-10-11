import sys
from pathlib import Path

import numpy as np
import pygame

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sources import create_source
from src.sources.text import AnimatedTextSource, TextSource

CFG = {'type': 'text', 'text': 'Welcome Home\nMom, Dad, and Rosie!', 'animation': ['wave', 'glow'],
       'wrap': False}


def make(cfg=CFG):
    pygame.font.init()
    clock = [0.0]
    src = AnimatedTextSource(dict(cfg), clock=lambda: clock[0])
    src.set_target_size(800, 300)
    return src, clock


def test_animation_option_picks_the_animated_source():
    assert isinstance(create_source(CFG), AnimatedTextSource)
    assert type(create_source({'type': 'text', 'text': 'hi'})) is TextSource


def test_frames_change_over_time_at_a_capped_rate():
    src, clock = make()
    v0, img0 = src.latest()
    clock[0] = 0.01
    assert src.latest()[0] == v0                 # too soon for the next animation frame
    clock[0] = 0.5
    v1, img1 = src.latest()
    assert v1 != v0 and not np.array_equal(img0, img1)


def test_no_wrap_keeps_each_line_whole():
    from src.sources.text import FontCache, fit_rows
    pygame.font.init()
    _, laid_out = fit_rows([(CFG['text'], 1.0)], FontCache(), 600, 400, wrap=False)
    assert laid_out[0][1] == ['Welcome Home', 'Mom, Dad, and Rosie!']
