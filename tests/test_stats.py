import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.stats import Stats, format_summary


def test_summary_per_interval():
    t = {'wall': 0.0, 'cpu': 0.0}
    stats = Stats(interval=10, clock=lambda: t['wall'], cpu_clock=lambda: t['cpu'])
    for i in range(100):
        stats.record_tick(10.0 if i < 95 else 30.0, rendered=i % 2 == 0)
    for _ in range(50):
        stats.record_render(upload_ms=2.0, draw_ms=1.0, upload_bytes=1_000_000, uploads=1, dropped=0)
    stats.record_render(0, 0, 0, 0, dropped=20)
    assert stats.maybe_report() is None                  # interval not over
    t['wall'], t['cpu'] = 10.0, 5.0
    s = stats.maybe_report()
    assert s['fps'] == pytest.approx(5.0)                 # 50 renders / 10 s
    assert s['ticks_per_s'] == pytest.approx(10.0)
    assert s['tick_ms_p95'] >= 10.0
    assert s['upload_mb_s'] == pytest.approx(5.0)
    assert s['dropped_per_s'] == pytest.approx(2.0)
    assert s['cpu_percent'] == pytest.approx(50.0)
    assert 'FPS 5.0' in format_summary(s)
    assert stats.maybe_report() is None                   # a new window started
