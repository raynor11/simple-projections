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


def test_hardware_pipelines_and_fallback(monkeypatch):
    import src.sources.file as file_mod
    pipes = file_mod.hardware_pipelines('/media/clip.mp4')
    assert pipes[0].startswith('filesrc location="/media/clip.mp4" ! qtdemux ! h264parse ! v4l2h264dec ! v4l2convert')
    assert 'videoconvert' in pipes[1] and pipes[0].endswith('appsink drop=false max-buffers=2 sync=false')
    assert file_mod.hardware_pipelines('/media/clip.avi') == []

    opened = []

    class FakeCap:
        def __init__(self, arg, backend=None):
            opened.append(arg)
            self.hw = backend is not None
        def isOpened(self):
            return True
        def read(self, *a):
            return (not self.hw), None                        # hardware pipelines fail to decode
        def release(self):
            pass

    monkeypatch.setattr(file_mod.cv2, 'VideoCapture', FakeCap)
    monkeypatch.setattr(file_mod.sys, 'platform', 'linux')
    monkeypatch.setattr(file_mod, 'opencv_has_gstreamer', lambda: True)
    cap, name = file_mod.open_video('/media/clip.mp4')
    assert name == 'software (FFmpeg)' and len(opened) == 3   # tried both pipelines, then FFmpeg
    monkeypatch.setenv('PM_VIDEO_DECODER', 'software')
    opened.clear()
    file_mod.open_video('/media/clip.mp4')
    assert len(opened) == 1                                   # forced straight to software


def test_screen_rotation_names_corners_as_the_viewer_sees_them():
    from src.playback import screen_corners, raw_corner_key
    raw = {'tl': [0, 0], 'tr': [100, 0], 'br': [100, 50], 'bl': [0, 50]}
    config = {'canvas': {'width': 1920, 'height': 1080}, 'screen': {'corners': raw}}
    assert screen_corners(config) == raw
    config['screen']['rotation'] = 90      # a portrait screen lit by a projector on its side
    rotated = screen_corners(config)
    assert rotated == {'tl': [100, 0], 'tr': [100, 50], 'br': [0, 50], 'bl': [0, 0]}
    assert raw_corner_key(config, 'tl') == 'tr'
    config['screen']['rotation'] = 270
    assert screen_corners(config)['tl'] == [0, 50]


def test_fit_within_keeps_the_content_shape():
    import numpy as np
    from src.playback import fit_within
    frame = np.float64([[0, 0], [900, 0], [900, 1600], [0, 1600]])       # a 9:16 frame
    tall = fit_within(frame, 9 / 19.5)                                    # an iPhone screen
    w, h = tall[1][0] - tall[0][0], tall[3][1] - tall[0][1]
    assert abs(w / h - 9 / 19.5) < 1e-6 and abs(h - 1600) < 1e-6 and abs(tall[0][0] - (900 - w) / 2) < 1e-6
    wide = fit_within(frame, 4 / 3)                                       # a landscape photo
    w, h = wide[1][0] - wide[0][0], wide[3][1] - wide[0][1]
    assert abs(w - 900) < 1e-6 and abs(w / h - 4 / 3) < 1e-6
    assert fit_within(frame, 9 / 16) is frame                             # same shape: unchanged


def test_decode_size_fits_the_frame_within_the_pixel_budget():
    from src.sources.file import decode_size, hardware_pipelines
    # A portrait 1080x1920 video filling a ~920x1690 frame: capped near 720p's pixel count.
    w, h = decode_size((1080, 1920), (920, 1690))
    assert w * h <= 1280 * 720 and abs(w / h - 1080 / 1920) < 0.01 and w % 2 == 0
    # A small frame: just big enough to cover it.
    assert decode_size((1920, 1080), (480, 270)) == (480, 270)
    # Already small enough: native size.
    assert decode_size((640, 360), (1920, 1080)) is None
    assert 'width=712,height=1264' in hardware_pipelines('/m/v.mov', (712, 1264))[0]


def test_tall_portrait_video_skips_the_isp():
    from src.sources.file import hardware_pipelines, isp_can_scale
    assert not isp_can_scale((1080, 1920))     # trips the bcm2835-codec driver
    assert isp_can_scale((1920, 1080)) and isp_can_scale((720, 1080))
    pipes = hardware_pipelines('/m/v.mov', (720, 1280), isp=False)
    assert len(pipes) == 1 and 'v4l2convert' not in pipes[0] and 'videoscale' in pipes[0]


def test_rotate_corners_turns_the_image_clockwise():
    from src.playback import rotate_corners
    frame = {'tl': 'A', 'tr': 'B', 'br': 'C', 'bl': 'D'}
    assert rotate_corners(frame, 0) is frame
    # A video encoded turned 90 degrees clockwise is drawn upright with 270:
    # its top-left (the upright picture's bottom-left) goes to the frame's bottom-left.
    assert rotate_corners(frame, 270) == {'tl': 'D', 'tr': 'A', 'br': 'B', 'bl': 'C'}
    assert rotate_corners(frame, 90) == {'tl': 'B', 'tr': 'C', 'br': 'D', 'bl': 'A'}


def test_quarter_turned_sources_get_a_swapped_target_size():
    from src.sources.base import Source
    s = Source({'type': 'file', 'rotate': 270})
    s.set_target_size(918, 1833)
    assert s.target_size == (1833, 918)


def test_hidden_video_pauses_after_a_moment(monkeypatch, tmp_path):
    from src.sources import file as file_mod
    clock = [100.0]
    monkeypatch.setattr(file_mod.time, 'monotonic', lambda: clock[0])
    video = tmp_path / 'clip.mp4'
    video.write_bytes(b'')
    src = file_mod.VideoFileSource({'type': 'file', 'path': str(video)})
    src.set_shown(False)
    assert src._shown.is_set()               # a brief hide (e.g. a rule flicker) doesn't pause
    clock[0] += 2.5
    src.set_shown(False)
    assert not src._shown.is_set()           # hidden long enough: decoding stops
    src.set_shown(True)
    assert src._shown.is_set()
