import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.projector.power import (
    PowerSequencer, PROJECTOR_OFF, PROJECTOR_ON, SHUTDOWN_HOST, NORMAL, SHUTDOWN, parse_ups_status, on_battery,
)


def run(seq, timeline):
    """timeline: [(t, battery, projector_power)] -> [(t, actions)] for steps that produced actions."""
    out = []
    for t, battery, power in timeline:
        actions = seq.step(t, battery, power)
        if actions:
            out.append((t, actions))
    return out


def test_switch_flick_is_ignored():
    seq = PowerSequencer(debounce_s=10)
    assert run(seq, [(0, True, None), (5, True, None), (8, False, None), (30, False, None)]) == []
    assert seq.state == NORMAL


def test_normal_shutdown():
    seq = PowerSequencer(debounce_s=10, projector_off_timeout_s=90)
    events = run(seq, [(0, True, None), (9, True, None), (10, True, None),
                       (12, True, 'cooling'), (20, True, 'off')])
    assert events == [(10, [PROJECTOR_OFF]), (20, [SHUTDOWN_HOST])]
    assert seq.state == SHUTDOWN
    assert seq.step(30, False, 'off') == []    # committed once shutdown starts


def test_power_returns_while_projector_turning_off():
    seq = PowerSequencer(debounce_s=10)
    events = run(seq, [(0, True, None), (10, True, None), (15, True, 'cooling'), (16, False, None)])
    assert events == [(10, [PROJECTOR_OFF]), (16, [PROJECTOR_ON])]
    assert seq.state == NORMAL


def test_projector_never_answers_still_shuts_down():
    seq = PowerSequencer(debounce_s=10, projector_off_timeout_s=90)
    events = run(seq, [(0, True, None), (10, True, None), (50, True, None), (100, True, None)])
    assert events == [(10, [PROJECTOR_OFF]), (100, [SHUTDOWN_HOST])]


def test_off_is_resent_if_projector_stays_on():
    seq = PowerSequencer(debounce_s=10, projector_off_timeout_s=90)
    events = run(seq, [(0, True, None), (10, True, None), (20, True, 'on'), (26, True, 'on')])
    assert events == [(10, [PROJECTOR_OFF]), (26, [PROJECTOR_OFF])]


def test_unknown_ups_status_never_starts_or_cancels():
    seq = PowerSequencer(debounce_s=10)
    assert run(seq, [(0, None, None), (100, None, None)]) == []
    run(seq, [(200, True, None)])
    assert run(seq, [(205, None, None), (211, None, None)]) == [(211, [PROJECTOR_OFF])]


def test_parse_ups_status():
    assert on_battery(parse_ups_status("OB DISCHRG\n")) is True
    assert on_battery(parse_ups_status("OL CHRG")) is False
    assert on_battery(None) is None
