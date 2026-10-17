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
    'family': {'path': 'family.mp4', 'reverse': 'family-rev.mp4', 'end_reversed': True,
               'repeat': [2, 5], 'next': 'exit'},
    'exit': {'path': 'exit.mp4', 'next': 'empty'},
    'empty': {'path': 'empty.mp4', 'repeat': [1, 3], 'next': 'entrance'},
    'entrance': {'path': 'entrance.mp4', 'next': 'family'},
}


class FakePlayer(Source):
    """Opens in OPEN_SECONDS, starts when told to, plays 8 s and finishes."""

    OPEN_SECONDS = 0.3

    def __init__(self, name, reversed_, software, now):
        super().__init__({'type': 'file'})
        self.name, self.reversed, self.software = name, reversed_, software
        self.created = now
        self.duration = 8.0
        self.at = 'unset'
        self.started_at = None
        self.finished = False
        self.closed = False

    @property
    def hardware(self):
        return not self.software and not self.finished

    def go(self, at=None):
        self.at = at

    def tick(self, now):
        ready = now >= self.created + self.OPEN_SECONDS
        if self.started_at is None and ready and self.at != 'unset' and (self.at is None or now >= self.at):
            self.started_at = now if self.at is None else max(self.at, self.created + self.OPEN_SECONDS)
        if self.started_at is not None and now >= self.started_at + self.duration:
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
        self.max_hardware = 0

    def _new_player(self, name, reversed_, software):
        player = FakePlayer(name, reversed_, software, self.now)
        self.players.append(player)
        return player

    def run(self, until, step=0.05):
        while self.now < until:
            for p in self.players:
                p.tick(self.now)
            self.source.poll(self.now)
            self.source.set_shown(True)
            self.max_hardware = max(self.max_hardware, sum(p.hardware for p in self.players))
            self.now = round(self.now + step, 6)


def runs(players):
    out = []
    for p in players:
        if out and out[-1][0] == p.name:
            out[-1][1].append(p)
        else:
            out.append([p.name, [p]])
    return out


def test_plays_the_cycle_with_repeats_within_range():
    h = Harness()
    h.run(600)
    clips = runs(h.players)
    assert [r[0] for r in clips[:8]] == ['family', 'exit', 'empty', 'entrance'] * 2
    for name, plays in clips[:-1]:
        lo, hi = seq_mod.repeat_range(CLIPS[name])
        assert lo <= len(plays) <= hi, (name, len(plays))


def test_bouncing_clip_alternates_direction_and_ends_backwards():
    h = Harness()
    h.run(600)
    for name, plays in runs(h.players)[:-1]:
        if name == 'family':
            assert [p.reversed for p in plays] == [i % 2 == 1 for i in range(len(plays))]
            assert len(plays) % 2 == 0                 # finishes on a backwards play


def test_bounce_cuts_exactly_at_the_end_and_changes_fade():
    h = Harness(fade=0.4)
    h.run(0.6)
    first, second = h.players[:2]
    assert second.name == 'family' and second.reversed
    assert second.at == pytest.approx(first.started_at + 8.0)   # bounce: straight cut
    h.run(first.started_at + 8.05)
    assert [l for l in h.source.layers()][-1] == (second, 1.0)
    h.run(200)
    exit_play = next(p for p in h.players if p.name == 'exit')
    before = h.players[h.players.index(exit_play) - 1]
    assert exit_play.at == pytest.approx(before.started_at + 7.6)   # change: fades over the end


def test_fade_ramps_and_closes_the_outgoing_play():
    h = Harness(fade=0.4)
    h.run(200)
    exit_play = next(p for p in h.players if p.name == 'exit')
    before = h.players[h.players.index(exit_play) - 1]
    assert before.closed
    h2 = Harness(fade=0.4)
    h2.run(exit_play.started_at + 0.2)
    layers = h2.source.layers()
    assert len(layers) == 2 and 0 < layers[1][1] < 1


def test_never_two_hardware_decoders_at_once():
    h = Harness()
    h.run(600)
    assert h.max_hardware == 1
    assert {p.software for p in h.players} == {True, False}    # it alternates


def test_a_clip_that_fails_to_open_is_skipped():
    h = Harness()
    h.run(1.0)
    second = h.players[1]
    second.tick = lambda now: None
    second.finished = True                          # failed: never started
    h.run(3.0)
    assert h.players[0].closed
    assert len(h.players) >= 3 and h.source._current[2] is not second   # went straight on


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

    a, b = FakePlayer('a', False, False, 0), FakePlayer('b', False, True, 0)
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
    with pytest.raises(ValueError, match='end_reversed'):
        validate_config(_config(clips={**CLIPS, 'exit': {'path': 'x', 'next': 'empty', 'end_reversed': True}}))
