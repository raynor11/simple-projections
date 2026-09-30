import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import src.playback as playback_module
from src.playback import Playback
from src.sources.base import Source


class FakeSource(Source):
    instances = []

    def __init__(self, cfg):
        super().__init__(cfg)
        self.is_active = False
        self.closed = False
        FakeSource.instances.append(self)

    def start(self):
        self._publish(np.zeros((4, 4, 4), np.uint8))

    def close(self):
        self.closed = True

    @property
    def active(self):
        return self.is_active

    @property
    def visible(self):
        return self.cfg.get('type') != 'cast' or self.is_active


class FakeRenderer:
    canvas_width, canvas_height = 1000, 1000

    def __init__(self):
        self.layers = []

    def render(self, layers):
        self.layers = layers


@pytest.fixture
def playback(monkeypatch):
    FakeSource.instances = []
    monkeypatch.setattr(playback_module, 'create_source', lambda cfg, ctx=None: FakeSource(cfg))
    config = {'canvas': {'width': 1000, 'height': 1000}, 'frames': [
        {'id': 'cc', 'rect': [0.5, 0.5, 0.5, 0.5], 'source': {'type': 'cast'}},
        {'id': 'msg', 'rect': [0, 0, 0.4, 0.4], 'source': {'type': 'text', 'text': 'Hi'}, 'rules': [
            {'when': {'active_any': ['cc']}, 'set': {'rect': [0, 0, 0.2, 0.2],
                                                     'source': {'text': 'Casting'}}},
            {'when': {'between': ['23:00', '06:00']}, 'set': {'hidden': True}},
        ]},
    ]}
    clock = {'now': dt.datetime(2026, 9, 29, 12, 0)}
    p = Playback(config, FakeRenderer(), clock=lambda: clock['now'])
    p.start()
    p.wall = clock
    return p


def layer(p, frame_id):
    return next((l for l in p.renderer.layers if l.key.startswith(frame_id + ':')), None)


def test_cast_hidden_until_active_then_rule_shrinks_and_swaps_text(playback):
    playback.tick(0.0)
    assert layer(playback, 'cc') is None
    assert layer(playback, 'msg').corners['br'] == pytest.approx([400, 400])
    base_text = layer(playback, 'msg').source

    cast = FakeSource.instances[0]
    cast.is_active = True
    playback.tick(1.0)
    playback.tick(2.0)   # past the slide/fade durations
    assert layer(playback, 'cc').alpha == 1.0
    msg = layer(playback, 'msg')
    assert msg.corners['br'] == pytest.approx([200, 200])
    assert msg.source is not base_text and msg.source.cfg['text'] == 'Casting'

    cast.is_active = False
    playback.tick(3.0)
    playback.tick(4.0)
    assert layer(playback, 'cc') is None
    assert layer(playback, 'msg').source is base_text
    assert FakeSource.instances[-1].closed          # override source released


def test_time_rule_hides_frame(playback):
    playback.tick(0.0)
    playback.wall['now'] = dt.datetime(2026, 9, 29, 23, 30)
    playback.tick(1.0)
    playback.tick(2.0)
    assert layer(playback, 'msg') is None


def test_apply_config_keeps_updates_and_replaces_sources(playback, monkeypatch):
    playback.tick(0.0)
    cast, text = FakeSource.instances[0], FakeSource.instances[1]
    new_config = {'canvas': {'width': 1000, 'height': 1000}, 'frames': [
        {'id': 'msg', 'rect': [0, 0, 0.5, 0.5], 'source': {'type': 'text', 'text': 'Edited'}},
        {'id': 'new', 'rect': [0.5, 0, 0.5, 0.5], 'source': {'type': 'text', 'text': 'New'}},
        {'id': 'cc', 'rect': [0.5, 0.5, 0.5, 0.5], 'source': {'type': 'cast', 'device': 'other'}},
    ]}
    # FakeSource.update() always succeeds for same-type changes, like the text source.
    playback.apply_config(new_config)
    assert list(playback.frames) == ['msg', 'new', 'cc']
    assert playback.frames['msg'].base_source is text and text.cfg['text'] == 'Edited'
    assert playback.frames['cc'].base_source is cast


def test_apply_config_removes_frames_and_recreates_on_failed_update(playback):
    playback.tick(0.0)
    cast, text = FakeSource.instances[0], FakeSource.instances[1]
    cast.update = lambda cfg: False   # e.g. a device change a capture source can't take in place
    playback.apply_config({'canvas': {'width': 1000, 'height': 1000}, 'frames': [
        {'id': 'cc', 'rect': [0, 0, 1, 1], 'source': {'type': 'cast', 'device': 'other'}},
    ]})
    assert text.closed and cast.closed
    assert playback.frames['cc'].base_source is FakeSource.instances[-1]
