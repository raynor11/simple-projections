import os
import sys
from pathlib import Path

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
sys.path.insert(0, str(Path(__file__).parent.parent))

import pygame
import pytest

from src.calibration import CalibrationUI


@pytest.fixture
def ui():
    pygame.init()
    config = {'canvas': {'width': 1920, 'height': 1080}, 'frames': [
        {'id': 'cc', 'rect': [0.5, 0.5, 0.4, 0.4],
         'source': {'type': 'chromecast', 'device': 'x', 'cast_name': 'y'}},
        {'id': 'weather', 'rect': [0.1, 0.1, 0.3, 0.3], 'source': {'type': 'weather'}, 'rules': [
            {'when': {'active_any': ['cc']}, 'set': {'source': {'units': 'metric'}}},
            {'when': {'between': ['23:00', '06:00']}, 'set': {'hidden': True}},
        ]},
    ]}
    ui = CalibrationUI(1920, 1080, config)
    ui.current_frame_idx = 1
    yield ui
    pygame.quit()


def weather(ui):
    return ui.config['frames'][1]


def test_move_and_resize_base_rect(ui):
    ui.handle_key(pygame.K_RIGHT)
    ui.handle_key(pygame.K_DOWN, pygame.KMOD_SHIFT)
    assert weather(ui)['rect'][:2] == pytest.approx([0.105, 0.15])
    ui.handle_key(pygame.K_RIGHT, pygame.KMOD_CTRL)
    ui.handle_key(pygame.K_UP, pygame.KMOD_CTRL)
    assert weather(ui)['rect'][2:] == pytest.approx([0.305, 0.305])
    assert ui.modified


def test_casting_preview_edits_the_rule_not_the_base(ui):
    ui.handle_key(pygame.K_r)               # preview: casting
    ui.render_grid(pygame.Surface((960, 540)))
    assert 'rect' not in weather(ui)['rules'][0]['set']   # drawing never edits the config
    ui.handle_key(pygame.K_LEFT)
    assert weather(ui)['rules'][0]['set']['rect'] == pytest.approx([0.095, 0.1, 0.3, 0.3])
    assert weather(ui)['rect'] == [0.1, 0.1, 0.3, 0.3]


def test_night_preview_on_hidden_rule_edits_base(ui):
    ui.handle_key(pygame.K_r)
    ui.handle_key(pygame.K_r)               # preview: night -> hidden rule matches
    ui.handle_key(pygame.K_RIGHT)
    assert weather(ui)['rect'][0] == pytest.approx(0.105)
    assert 'rect' not in weather(ui)['rules'][1]['set']


def test_portrait_layout_starts_from_landscape(ui):
    ui.handle_key(pygame.K_p)
    ui.handle_key(pygame.K_RIGHT)
    assert weather(ui)['rect_portrait'] == pytest.approx([0.105, 0.1, 0.3, 0.3])
    assert weather(ui)['rect'] == [0.1, 0.1, 0.3, 0.3]


def test_screen_mode_nudges_screen_corner(ui):
    ui.handle_key(pygame.K_e)
    ui.handle_key(pygame.K_3)
    ui.handle_key(pygame.K_LEFT, pygame.KMOD_SHIFT)
    assert ui.config['screen']['corners']['br'] == [1910, 1080]


def test_add_and_delete_frame(ui):
    ui.handle_key(pygame.K_n)
    assert ui.get_current_frame()['source']['type'] == 'text'
    assert len(ui.config['frames']) == 3
    ui.handle_key(pygame.K_d)
    assert len(ui.config['frames']) == 2
