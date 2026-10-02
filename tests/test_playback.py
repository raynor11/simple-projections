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

    def render(self, layers, dim=1.0):
        self.layers = layers
        self.dim = dim


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


def test_software_dim_follows_schedule(monkeypatch):
    import src.projector.brightness as brightness
    monkeypatch.setattr(brightness, 'phase_at', lambda *a, **k: 'night')
    FakeSource.instances = []
    monkeypatch.setattr(playback_module, 'create_source', lambda cfg, ctx=None: FakeSource(cfg))
    config = {'canvas': {'width': 100, 'height': 100}, 'frames': [],
              'location': {'lat': 45.6, 'lon': -123.2, 'timezone': 'America/Los_Angeles'},
              'projector': {'serial': 'x', 'brightness': {'software_dim': {'night': 0.75}}}}
    p = Playback(config, FakeRenderer())
    p.start()
    import time as _time
    for _ in range(50):
        if p.dim != 1.0:
            break
        _time.sleep(0.02)
    p.tick(0.0)
    assert p.renderer.dim == 0.75
    p.close()


def test_skips_rendering_when_nothing_changed(playback):
    calls = []
    original = playback.renderer.render
    playback.renderer.render = lambda layers, dim=1.0: (calls.append(1), original(layers, dim))
    assert playback.tick(0.0) is True
    assert playback.tick(0.1) is False                    # same picture: no render, no flip
    assert playback.tick(0.2) is False
    FakeSource.instances[1]._publish(FakeSource.instances[1].latest()[1])   # a new frame from a source
    assert playback.tick(0.3) is True
    assert playback.tick(0.4) is False
    assert playback.tick(2.5) is True                     # periodic safety redraw
    assert len(calls) == 3


def test_animation_frames_always_render(playback):
    playback.tick(0.0)
    FakeSource.instances[0].is_active = True              # cast starts: fades in over 0.25s
    rendered = [playback.tick(0.6 + i * 0.04) for i in range(4)]
    assert all(rendered)


def test_render_rate_capped(playback):
    playback.tick(0.0)
    FakeSource.instances[1]._publish(FakeSource.instances[1].latest()[1])
    assert playback.tick(0.01) is False                   # changed, but within 1/30 s of the last frame
    assert playback.tick(0.026) is True                   # picked up on a following tick


def test_shrink_only_when_at_least_twice_the_target():
    import numpy as np
    from src.sources.base import FrameRing, Source
    s = Source({'type': 'x'})
    ring = FrameRing()
    frame = np.zeros((1080, 1920, 3), np.uint8)
    s.set_target_size(1152, 540)                          # under 2x in width: GPU scales
    assert s._shrink_for_target(frame, ring) is frame
    s.set_target_size(480, 270)                           # 4x: shrink on the CPU
    small = s._shrink_for_target(frame, ring)
    assert small.shape == (270, 480, 3)
    again = s._shrink_for_target(frame, ring)
    assert again.shape == (270, 480, 3) and again is not small   # ring rotates buffers
