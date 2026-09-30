import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.rules import effective_frame, in_window, parse_hhmm
from src.config_io import validate_config


NOON = dt.datetime(2026, 9, 29, 12, 0)
LATE = dt.datetime(2026, 9, 29, 23, 30)
EARLY = dt.datetime(2026, 9, 30, 5, 59)

WEATHER = {
    'id': 'weather',
    'rect': [0, 0, 0.3, 0.3],
    'source': {'type': 'weather', 'units': 'imperial'},
    'rules': [
        {'when': {'active_any': ['cc', 'air']}, 'set': {'rect': [0, 0, 0.1, 0.1]}},
        {'when': {'between': ['23:00', '06:00']}, 'set': {'hidden': True}},
    ],
}


def test_no_match_uses_base():
    cfg, index = effective_frame(WEATHER, set(), NOON)
    assert index is None and cfg is WEATHER


def test_active_any():
    cfg, index = effective_frame(WEATHER, {'air'}, NOON)
    assert index == 0 and cfg['rect'] == [0, 0, 0.1, 0.1]
    assert WEATHER['rect'] == [0, 0, 0.3, 0.3]   # base untouched


def test_first_match_wins():
    cfg, index = effective_frame(WEATHER, {'cc'}, LATE)
    assert index == 0 and not cfg.get('hidden')


def test_between_wraps_past_midnight():
    assert effective_frame(WEATHER, set(), LATE)[0]['hidden'] is True
    assert effective_frame(WEATHER, set(), EARLY)[0]['hidden'] is True
    assert effective_frame(WEATHER, set(), NOON)[1] is None


def test_in_window_edges():
    assert in_window(parse_hhmm('23:00'), parse_hhmm('23:00'), parse_hhmm('06:00'))
    assert not in_window(parse_hhmm('06:00'), parse_hhmm('23:00'), parse_hhmm('06:00'))
    assert in_window(parse_hhmm('09:00'), parse_hhmm('08:00'), parse_hhmm('17:00'))


def test_active_all_and_inactive_all():
    frame = {'id': 'f', 'rules': [
        {'when': {'active_all': ['a', 'b']}, 'set': {'hidden': True}},
        {'when': {'inactive_all': ['a', 'b']}, 'set': {'rect': [0, 0, 1, 1]}},
    ]}
    assert effective_frame(frame, {'a', 'b'}, NOON)[1] == 0
    assert effective_frame(frame, {'a'}, NOON)[1] is None
    assert effective_frame(frame, set(), NOON)[1] == 1


def test_self_reference_ignored():
    frame = {'id': 'cc', 'rules': [{'when': {'active_any': ['cc']}, 'set': {'hidden': True}}]}
    assert effective_frame(frame, {'cc'}, NOON)[1] is None


def test_source_override_merges_same_type_and_replaces_other_type():
    frame = {'id': 't', 'source': {'type': 'text', 'text': 'Hi', 'color': '#fff'}, 'rules': [
        {'when': {'active_any': ['cc']}, 'set': {'source': {'text': 'Casting!'}}},
        {'when': {'active_any': ['air']}, 'set': {'source': {'type': 'weather'}}},
    ]}
    assert effective_frame(frame, {'cc'}, NOON)[0]['source'] == {'type': 'text', 'text': 'Casting!', 'color': '#fff'}
    assert effective_frame(frame, {'air'}, NOON)[0]['source'] == {'type': 'weather'}


def config_with_rules(rules):
    return {'canvas': {'width': 1920, 'height': 1080}, 'frames': [
        {'id': 'cc', 'rect': [0, 0, 1, 1], 'source': {'type': 'chromecast', 'device': 'x', 'cast_name': 'y'}},
        {'id': 'air', 'rect': [0, 0, 1, 1], 'source': {'type': 'airplay', 'device': '/dev/video10'}},
        dict(WEATHER, rules=rules),
    ]}


def test_validate_accepts_good_rules():
    validate_config(config_with_rules(WEATHER['rules'][:1]))


@pytest.mark.parametrize('rules, message', [
    ([{'when': {'active_any': ['nope']}, 'set': {'hidden': True}}], 'unknown frame'),
    ([{'when': {'sunny': True}, 'set': {'hidden': True}}], 'unknown condition'),
    ([{'when': {'between': ['25:00', '06:00']}, 'set': {'hidden': True}}], 'Bad time'),
    ([{'when': {'active_any': ['cc']}, 'set': {'color': 'red'}}], "can't set"),
    ([{'when': {'active_any': ['cc']}, 'set': {'rect': [0, 0, 1]}}], 'rect'),
    ([{'when': {'active_any': ['cc']}}], 'needs'),
])
def test_validate_rejects_bad_rules(rules, message):
    with pytest.raises(ValueError, match=message):
        validate_config(config_with_rules(rules))


def test_rule_geometry_replaces_frames_shape():
    quad = {'tl': [0, 0], 'tr': [1, 0.1], 'br': [1, 1], 'bl': [0, 0.9]}
    frame = {'id': 'f', 'rect': [0, 0, 1, 1], 'rect_portrait': [0, 0, 0.5, 1], 'rules': [
        {'when': {'active_any': ['cc']}, 'set': {'quad': quad}}]}
    cfg, _ = effective_frame(frame, {'cc'}, NOON)
    assert cfg['quad'] == quad and 'rect' not in cfg and 'rect_portrait' not in cfg
