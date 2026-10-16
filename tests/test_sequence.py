import random
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config_io import validate_config
from src.sources import sequence as seq_mod
from src.sources.base import Source
from src.sources.sequence import SequenceSource


CLIPS = {
    'family': {'path': 'family.mp4', 'repeat': [2, 4], 'next': 'exit'},
    'exit': {'path': 'exit.mp4', 'next': 'empty'},
    'empty': {'path': 'empty.mp4', 'repeat': [1, 3], 'next': 'entrance'},
    'entrance': {'path': 'entrance.mp4', 'next': 'family'},
}


class FakePlayer(Source):
    """Takes OPEN_SECONDS to open, then plays 8 s and finishes."""

    OPEN_SECONDS = 0.3

    def __init__(self, name, now):
        super().__init__({'type': 'file'})
        self.name = name
        self.created = now
        self.started_at = None
        self.finished = False
        self.closed = False

    def tick(self, now):
        if self.started_at is None and now >= self.created + self.OPEN_SECONDS:
            self.started_at = now
        if self.started_at is not None and now >= self.started_at + 8.0:
            self.finished = True

    def close(self):
        self.closed = True
        self.finished = True


class Harness:
    def __init__(self, fade=0.4, seed=1):
        self.players = []
        self.source = SequenceSource({'type': 'sequence', 'start': 'family', 'fade_seconds': fade,
                                      'clips': CLIPS}, rng=random.Random(seed))
        self.source._new_player = self._new_player
        self.source.set_target_size(1920, 1080)
        self.now = 0.0

    def _new_player(self, name):
        player = FakePlayer(name, self.now)
        self.players.append(player)
        return player

    def run(self, until, step=0.05):
        while self.now < until:
            for p in self.players:
                p.tick(self.now)
            self.source.poll(self.now)
            self.source.set_shown(True)
            self.now = round(self.now + step, 6)


def test_plays_the_cycle_with_repeats_within_range():
    h = Harness()
    h.run(400)
    names = [p.name for p in h.players]
    # Collapse into runs: family x N, exit, empty x M, entrance, ...
    runs = []
    for n in names:
        if runs and runs[-1][0] == n:
            runs[-1][1] += 1
        else:
            runs.append([n, 1])
    order = [r[0] for r in runs]
    assert order[:8] == ['family', 'exit', 'empty', 'entrance'] * 2
    for name, count in runs[:-1]:
        lo, hi = seq_mod.repeat_range(CLIPS[name])
        assert lo <= count <= hi, (name, count)


def test_next_opens_only_after_the_current_finishes_then_fades_in():
    h = Harness(fade=0.4)
    h.run(7.0)
    first = h.players[0]
    assert len(h.players) == 1                      # one decoder at a time
    h.run(first.started_at + 8.1)
    assert len(h.players) == 2 and first.finished
    second = h.players[1]
    assert [l[0] for l in h.source.layers()] == [first]   # last frame held while the next opens
    h.run(h.now + FakePlayer.OPEN_SECONDS + 0.1)
    h.run(second.started_at + 0.2)
    layers = h.source.layers()
    assert [l[0] for l in layers] == [first, second]
    assert 0 < layers[1][1] < 1                     # mid-fade, drawn on top
    h.run(second.started_at + 0.5)
    assert first.closed
    assert [l[0] for l in h.source.layers()] == [second]


def test_no_fade_cuts_once_the_next_starts():
    h = Harness(fade=0)
    h.run(9.0)
    first, second = h.players[:2]
    h.run(second.started_at + 0.1)
    assert first.closed


def test_a_clip_that_fails_to_open_is_skipped():
    h = Harness()
    h.run(8.5)
    second = h.players[1]
    second.tick = lambda now: None
    second.finished = True                          # failed: never started
    h.run(9.0)
    assert h.players[0].closed and h.source._current[1] is second
    h.run(9.5)
    assert len(h.players) == 3


def test_hidden_releases_players_and_restarts_from_the_start():
    h = Harness()
    h.run(20)
    live = [p for p in h.players if not p.closed]
    for t in (20.0, 21.0, 22.5):
        h.source.poll(t)
        h.source.set_shown(False)
    assert all(p.closed for p in live)
    assert h.source.layers() == []
    count = len(h.players)
    h.source.set_shown(True)
    h.source.poll(23.0)
    assert h.players[count].name == 'family'


def test_playback_draws_each_part_as_a_layer(monkeypatch):
    import src.playback as playback_module
    from src.playback import Playback

    a, b = FakePlayer('a', 0), FakePlayer('b', 0)
    for p in (a, b):
        p._publish(np.zeros((2, 2, 3), np.uint8))

    class Multi(Source):
        def layers(self):
            return [(a, 1.0), (b, 0.5)]

    class R:
        canvas_width = canvas_height = 100

        def render(self, layers, dim=1.0):
            self.layers = layers

    monkeypatch.setattr(playback_module, 'create_source', lambda cfg, ctx=None: Multi(cfg))
    pb = Playback({'canvas': {'width': 100, 'height': 100},
                   'frames': [{'id': 'h', 'rect': [0, 0, 1, 1], 'source': {'type': 'sequence'}}]}, R())
    pb.start()
    pb.tick(now=1.0)
    assert [(l.source, l.alpha) for l in pb.renderer.layers] == [(a, 1.0), (b, 0.5)]
    assert pb.renderer.layers[0].key != pb.renderer.layers[1].key


def _config(**source):
    src = {'type': 'sequence', 'start': 'family', 'clips': dict(CLIPS), **source}
    return {'canvas': {'width': 100, 'height': 100},
            'frames': [{'id': 'h', 'rect': [0, 0, 1, 1], 'source': src}]}


def test_validation():
    validate_config(_config())
    with pytest.raises(ValueError, match='start'):
        validate_config(_config(start='nope'))
    with pytest.raises(ValueError, match='next'):
        validate_config(_config(clips={**CLIPS, 'exit': {'path': 'x', 'next': 'nope'}}))
    with pytest.raises(ValueError, match='repeat'):
        validate_config(_config(clips={**CLIPS, 'exit': {'path': 'x', 'next': 'empty', 'repeat': [3, 1]}}))
    with pytest.raises(ValueError, match='repeat'):
        validate_config(_config(clips={**CLIPS, 'exit': {'path': 'x', 'next': 'empty', 'repeat': 0}}))
    cfg = _config()
    del cfg['frames'][0]['source']['start']
    with pytest.raises(ValueError, match='start'):
        validate_config(cfg)
