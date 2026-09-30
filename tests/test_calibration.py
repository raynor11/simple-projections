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


@pytest.mark.parametrize('window', [(1470, 956), (960, 540), (600, 1000)])
def test_renders_at_any_window_shape(ui, window):
    surface = pygame.Surface(window)
    ui.render_grid(surface)
    ui.handle_key(pygame.K_h)
    ui.handle_key(pygame.K_e)
    ui.render_grid(surface)
    assert not ui.show_help


def test_help_wraps_to_width():
    pygame.font.init()
    font = pygame.font.Font(None, 20)
    lines = CalibrationUI._wrap(["aaaa", "bbbb", "cccc"], font, font.size("aaaa     bbbb")[0])
    assert lines == ["aaaa     bbbb", "cccc"]


# -- mouse ---------------------------------------------------------------------
# The fixture's canvas is 1920x1080 with no detected screen, so on a
# 1920x1080 window normalized screen (x, y) is at pixel (1920x, 1080y).

def ready(ui):
    ui.render_grid(pygame.Surface((1920, 1080)))
    return ui


def mouse(ui, kind, pos, button=1):
    ui.handle_event(pygame.event.Event(kind, pos=pos, button=button, rel=(0, 0), buttons=(1, 0, 0)))


def drag(ui, start, end):
    mouse(ui, pygame.MOUSEBUTTONDOWN, start)
    mouse(ui, pygame.MOUSEMOTION, end)
    mouse(ui, pygame.MOUSEBUTTONUP, end)


def px(x, y):
    return (round(1920 * x), round(1080 * y))


def test_click_selects_frame(ui):
    ready(ui)
    mouse(ui, pygame.MOUSEBUTTONDOWN, px(0.7, 0.7))
    mouse(ui, pygame.MOUSEBUTTONUP, px(0.7, 0.7))
    assert ui.current_frame_idx == 0
    assert not ui.modified                       # a click without moving changes nothing


def test_drag_moves_frame_and_undo_restores(ui):
    ready(ui)
    drag(ui, px(0.25, 0.25), px(0.30, 0.25))
    assert weather(ui)['rect'] == pytest.approx([0.15, 0.1, 0.3, 0.3], abs=1e-3)
    ui.handle_key(pygame.K_z, pygame.KMOD_CTRL)
    assert weather(ui)['rect'] == [0.1, 0.1, 0.3, 0.3]
    ui.handle_key(pygame.K_y, pygame.KMOD_CTRL)
    assert weather(ui)['rect'] == pytest.approx([0.15, 0.1, 0.3, 0.3], abs=1e-3)


def test_drag_snaps_to_screen_centre(ui):
    ready(ui)
    drag(ui, px(0.25, 0.25), px(0.25 + 0.397, 0.25))   # left edge would land at 0.497
    assert weather(ui)['rect'][0] == pytest.approx(0.5)


def test_drag_handle_resizes(ui):
    ready(ui)
    ui.render_grid(pygame.Surface((1920, 1080)))
    drag(ui, px(0.4, 0.4), px(0.45, 0.5))                # the bottom-right handle
    assert weather(ui)['rect'] == pytest.approx([0.1, 0.1, 0.35, 0.4], abs=1e-3)


def test_wheel_scales_selected_frame(ui):
    ready(ui)
    ui.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=2))
    x, y, w, h = weather(ui)['rect']
    assert w > 0.3 and x + w / 2 == pytest.approx(0.25, abs=1e-3)


def test_drag_screen_corner(ui):
    ready(ui)
    ui.handle_key(pygame.K_e)
    handles, numbered = ui._handles()
    assert numbered
    drag(ui, handles['tl'], (100, 200))
    assert ui.config['screen']['corners']['tl'] == [100, 200]


def test_toolbar_buttons(ui):
    ready(ui)
    add = next(b for b in ui.buttons if b.id == 'add')
    mouse(ui, pygame.MOUSEBUTTONDOWN, add.rect.center)
    assert len(ui.config['frames']) == 3
    ui.render_grid(pygame.Surface((1920, 1080)))
    undo = next(b for b in ui.buttons if b.id == 'undo')
    assert undo.enabled
    mouse(ui, pygame.MOUSEBUTTONDOWN, undo.rect.center)
    assert len(ui.config['frames']) == 2


def test_quit_asks_again_when_unsaved(ui):
    ui.handle_key(pygame.K_RIGHT)
    ui.handle_key(pygame.K_q)
    assert ui.running                            # warned instead of quitting
    ui.handle_key(pygame.K_q)
    assert not ui.running


def test_text_size_controls(ui):
    ui.handle_key(pygame.K_EQUALS)
    ui.handle_key(pygame.K_EQUALS)
    assert ui.ui_scale == 1.25
    for _ in range(10):
        ui.handle_key(pygame.K_MINUS)
    assert ui.ui_scale == 0.75
