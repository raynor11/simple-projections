"""
Frame rules: change a frame while something else is going on.

    "rules": [
      {"when": {"active_any": ["cc", "air"]}, "set": {"rect": [0.02, 0.04, 0.15, 0.12]}},
      {"when": {"between": ["23:00", "06:00"]}, "set": {"hidden": true}}
    ]

Rules are checked top to bottom and the first match wins; with no match
the frame uses its own config. All conditions in one "when" must hold.
"""

import re


ID_LIST_CONDITIONS = ('active_any', 'active_all', 'inactive_all')
CONDITIONS = ID_LIST_CONDITIONS + ('between',)
SETTABLE = ('hidden', 'rect', 'rect_portrait', 'corners', 'source')
HHMM = re.compile(r'^([01]?\d|2[0-3]):([0-5]\d)$')


def parse_hhmm(value):
    """'HH:MM' -> minutes since midnight."""
    match = HHMM.match(str(value))
    if not match:
        raise ValueError(f"Bad time {value!r}; expected HH:MM")
    return int(match.group(1)) * 60 + int(match.group(2))


def in_window(minutes, start, end):
    """Whether minutes-since-midnight falls in [start, end), wrapping past midnight if end < start."""
    if start <= end:
        return start <= minutes < end
    return minutes >= start or minutes < end


def condition_holds(when, active_ids, now, self_id=None):
    """
    Evaluate one rule's "when". A frame's own id is ignored in its
    conditions, so a rule can't depend on the state it changes.
    """
    for key, value in when.items():
        if key in ID_LIST_CONDITIONS:
            ids = [i for i in value if i != self_id]
            if not ids:
                return False
            if key == 'active_any' and not any(i in active_ids for i in ids):
                return False
            if key == 'active_all' and not all(i in active_ids for i in ids):
                return False
            if key == 'inactive_all' and any(i in active_ids for i in ids):
                return False
        elif key == 'between':
            start, end = (parse_hhmm(v) for v in value)
            if not in_window(now.hour * 60 + now.minute, start, end):
                return False
        else:
            return False
    return True


def apply_overrides(base_cfg, overrides):
    """Frame config with a rule's "set" applied. A source override of the same type merges fields."""
    cfg = dict(base_cfg)
    for key, value in overrides.items():
        if key == 'source':
            base_source = base_cfg.get('source') or {}
            if 'type' not in value or value['type'] == base_source.get('type'):
                value = {**base_source, **value}
        cfg[key] = value
    return cfg


def effective_frame(base_cfg, active_ids, now):
    """
    The frame config to use right now: base_cfg with the first matching
    rule's overrides applied. Returns (cfg, index of the matching rule or None).
    """
    for index, rule in enumerate(base_cfg.get('rules') or []):
        if condition_holds(rule.get('when') or {}, active_ids, now, base_cfg.get('id')):
            return apply_overrides(base_cfg, rule.get('set') or {}), index
    return base_cfg, None


def validate_rules(frame, frame_ids):
    """Raise ValueError if a frame's rules are malformed or reference unknown frames."""
    what = f"Frame {frame.get('id')!r}"
    rules = frame.get('rules')
    if rules is None:
        return
    if not isinstance(rules, list):
        raise ValueError(f"{what} rules must be a list")
    for n, rule in enumerate(rules, 1):
        where = f"{what} rule {n}"
        if not isinstance(rule, dict) or not isinstance(rule.get('when'), dict) \
                or not isinstance(rule.get('set'), dict):
            raise ValueError(f"{where} needs a \"when\" object and a \"set\" object")
        if not rule['when']:
            raise ValueError(f"{where} has an empty \"when\"")
        for key, value in rule['when'].items():
            if key not in CONDITIONS:
                raise ValueError(f"{where} has unknown condition {key!r}")
            if key in ID_LIST_CONDITIONS:
                if not isinstance(value, list) or not value:
                    raise ValueError(f"{where} {key} must be a non-empty list of frame ids")
                unknown = [i for i in value if i not in frame_ids]
                if unknown:
                    raise ValueError(f"{where} {key} references unknown frame(s): {unknown}")
            if key == 'between':
                if not isinstance(value, list) or len(value) != 2:
                    raise ValueError(f"{where} between must be [\"HH:MM\", \"HH:MM\"]")
                for v in value:
                    parse_hhmm(v)
        for key in rule['set']:
            if key not in SETTABLE:
                raise ValueError(f"{where} can't set {key!r} (allowed: {', '.join(SETTABLE)})")
