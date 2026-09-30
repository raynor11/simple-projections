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


ADDRESS = "1600 Pennsylvania Ave NW, Washington, DC 20500"
CENSUS_MATCH = {'result': {'addressMatches': [
    {'matchedAddress': '1600 PENNSYLVANIA AVE NW, WASHINGTON, DC, 20500', 'coordinates': {'x': -77.0365, 'y': 38.8977}}]}}
CENSUS_NONE = {'result': {'addressMatches': []}}
TZ = {'timezone': 'America/New_York'}


def address_fetch(census=CENSUS_MATCH, nominatim=None, tz=TZ):
    calls = []

    def fetch(url):
        calls.append(url)
        if 'census.gov' in url:
            return census
        if 'nominatim' in url:
            return nominatim or []
        if 'open-meteo' in url:
            return tz
        raise OSError("unexpected")
    return fetch, calls


def test_address_is_geocoded_with_timezone_and_cached(tmp_path):
    cache = tmp_path / 'addr.json'
    fetch, calls = address_fetch()
    loc = resolve_location({'address': ADDRESS}, tmp_path / 'ip.json', fetch, address_cache_path=cache)
    assert (loc['lat'], loc['lon'], loc['timezone']) == (38.8977, -77.0365, 'America/New_York')
    # Cached: no more requests for the same address...
    resolve_location({'address': ADDRESS}, tmp_path / 'ip.json', fetch, address_cache_path=cache)
    assert len(calls) == 2
    # ...but a changed address is looked up again.
    resolve_location({'address': "Somewhere else"}, tmp_path / 'ip.json', fetch, address_cache_path=cache)
    assert len(calls) == 4


def test_address_falls_back_to_nominatim_then_ip(tmp_path):
    fetch, _ = address_fetch(census=CENSUS_NONE,
                             nominatim=[{'lat': '48.85', 'lon': '2.29', 'display_name': 'Paris'}])
    loc = resolve_location({'address': 'Paris'}, tmp_path / 'ip.json', fetch,
                           address_cache_path=tmp_path / 'a.json')
    assert loc['lat'] == 48.85

    def nothing_but_ip(url):
        if 'ipwho.is' in url:
            return IPWHOIS
        if 'census.gov' in url:
            return CENSUS_NONE
        return []
    loc = resolve_location({'address': 'Nowhere'}, tmp_path / 'ip2.json', nothing_but_ip,
                           address_cache_path=tmp_path / 'b.json')
    assert loc['lat'] == 45.52          # the IP location


def test_configured_timezone_overrides_lookup(tmp_path):
    fetch, _ = address_fetch()
    loc = resolve_location({'address': ADDRESS, 'timezone': 'UTC'}, tmp_path / 'ip.json', fetch,
                           address_cache_path=tmp_path / 'a.json')
    assert loc['timezone'] == 'UTC'
