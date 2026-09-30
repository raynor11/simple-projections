import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.location import resolve_location, lookup_ip_location, is_auto


IPWHOIS = {'success': True, 'city': 'Portland', 'region': 'Oregon', 'latitude': 45.52, 'longitude': -122.68,
           'timezone': {'id': 'America/Los_Angeles'}}


def fake_fetch(responses):
    calls = []

    def fetch(url):
        calls.append(url)
        result = responses[url] if url in responses else None
        if isinstance(result, Exception) or result is None:
            raise result or OSError("down")
        return result
    return fetch, calls


def test_configured_location_is_used_as_is(tmp_path):
    fetch, calls = fake_fetch({})
    loc = {'lat': 1.0, 'lon': 2.0}
    assert resolve_location(loc, tmp_path / 'c.json', fetch) is loc
    assert calls == []


def test_auto_looks_up_and_caches(tmp_path):
    cache = tmp_path / 'c.json'
    fetch, calls = fake_fetch({'https://ipwho.is/': IPWHOIS})
    loc = resolve_location(None, cache, fetch, now=lambda: 1000)
    assert loc == {'lat': 45.52, 'lon': -122.68, 'timezone': 'America/Los_Angeles', 'city': 'Portland, Oregon'}
    assert json.loads(cache.read_text())['location'] == loc
    # Fresh cache: no second lookup.
    assert resolve_location("auto", cache, fetch, now=lambda: 2000) == loc
    assert len(calls) == 1


def test_falls_back_to_second_provider():
    fetch, _ = fake_fetch({'https://ipapi.co/json/': {'latitude': 1, 'longitude': 2, 'timezone': 'UTC',
                                                       'city': 'X'}})
    assert lookup_ip_location(fetch)['lat'] == 1


def test_offline_uses_stale_cache_or_none(tmp_path):
    cache = tmp_path / 'c.json'
    fetch, _ = fake_fetch({})
    assert resolve_location(None, cache, fetch) is None
    cache.write_text(json.dumps({'location': {'lat': 3, 'lon': 4}, 'fetched_at': 0}))
    assert resolve_location(None, cache, fetch, now=lambda: 10**9) == {'lat': 3, 'lon': 4}


def test_is_auto():
    assert is_auto(None) and is_auto("auto") and is_auto({'timezone': 'UTC'})
    assert not is_auto({'lat': 1, 'lon': 2})
