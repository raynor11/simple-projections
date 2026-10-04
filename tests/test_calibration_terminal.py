import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pygame

from src.calibration.terminal import parse, TOGGLE_RESIZE, TOGGLE_BIG

SHIFT, ALT, CTRL = pygame.KMOD_SHIFT, pygame.KMOD_ALT, pygame.KMOD_CTRL


def keys(text):
    decoded, leftover = parse(text)
    assert leftover == ''
    return decoded


def test_arrows_plain_and_application_mode():
    assert keys('\x1b[A\x1b[B\x1b[C\x1b[D') == [(pygame.K_UP, 0), (pygame.K_DOWN, 0),
                                               (pygame.K_RIGHT, 0), (pygame.K_LEFT, 0)]
    assert keys('\x1bOA') == [(pygame.K_UP, 0)]


def test_modified_arrows():
    assert keys('\x1b[1;2C') == [(pygame.K_RIGHT, SHIFT)]
    assert keys('\x1b[1;5D') == [(pygame.K_LEFT, CTRL)]
    assert keys('\x1b[1;3A') == [(pygame.K_UP, ALT)]
    assert keys('\x1b\x1b[B') == [(pygame.K_DOWN, ALT)]     # macOS Terminal Option+Down


def test_tab_undo_redo_quit():
    assert keys('\t\x1b[Z') == [(pygame.K_TAB, 0), (pygame.K_TAB, SHIFT)]
    assert keys('\x1a\x19') == [(pygame.K_z, CTRL), (pygame.K_y, CTRL)]
    assert keys('\x03') == [(pygame.K_q, 0)]


def test_letters_digits_and_toggles():
    assert keys('s1w') == [(pygame.K_s, 0), (pygame.K_1, 0), (pygame.K_w, 0)]
    assert keys('+-') == [(pygame.K_PLUS, 0), (pygame.K_MINUS, 0)]
    assert keys('mB') == [TOGGLE_RESIZE, TOGGLE_BIG]


def test_lone_escape_and_split_sequence():
    assert keys('\x1b') == [(pygame.K_ESCAPE, 0)]
    decoded, leftover = parse('a\x1b[1;')
    assert decoded == [(pygame.K_a, 0)] and leftover == '\x1b[1;'
    assert keys(leftover + '2A') == [(pygame.K_UP, SHIFT)]
