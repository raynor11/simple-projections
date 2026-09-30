"""
Where the projector is, for local weather and sunrise/sunset.

Set "location": {"lat": ..., "lon": ..., "timezone": ...} for an exact
spot, or leave it out (or use "auto") to look it up from the network's
public IP address -- city-level, which is plenty for weather and sun
times. The lookup is cached, so it keeps working offline and isn't
repeated on every start.
"""

import json
import time
import urllib.request
from pathlib import Path


CACHE_PATH = Path("config/location.auto.json")
CACHE_MAX_AGE = 7 * 24 * 3600
TIMEOUT = 10


def is_auto(location):
    return (location is None or location == "auto"
            or (isinstance(location, dict) and ('lat' not in location or 'lon' not in location)))


def _fetch_json(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'projection-mapper'})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as resp:
        return json.load(resp)


def _from_ipwhois(data):
    if not data.get('success'):
        raise ValueError(data.get('message') or 'lookup failed')
    tz = data.get('timezone')
    return {'lat': data['latitude'], 'lon': data['longitude'],
            'timezone': tz.get('id') if isinstance(tz, dict) else tz,
            'city': ', '.join(p for p in (data.get('city'), data.get('region')) if p)}


def _from_ipapi(data):
    if data.get('error'):
        raise ValueError(data.get('reason') or 'lookup failed')
    return {'lat': data['latitude'], 'lon': data['longitude'], 'timezone': data.get('timezone'),
            'city': ', '.join(p for p in (data.get('city'), data.get('region')) if p)}


PROVIDERS = (
    ("https://ipwho.is/", _from_ipwhois),
    ("https://ipapi.co/json/", _from_ipapi),
)


def lookup_ip_location(fetch=_fetch_json):
    """Approximate location of this network's public IP. Raises if every provider fails."""
    errors = []
    for url, parse in PROVIDERS:
        try:
            return parse(fetch(url))
        except Exception as e:
            errors.append(f"{url}: {e}")
    raise RuntimeError("; ".join(errors))


def _read_cache(path):
    try:
        cached = json.loads(Path(path).read_text())
        return cached['location'], cached['fetched_at']
    except (OSError, ValueError, KeyError):
        return None, None


def resolve_location(configured, cache_path=CACHE_PATH, fetch=_fetch_json, now=time.time):
    """
    The location to use: the configured lat/lon if set, otherwise the IP
    lookup (from the cache while it's fresh). Returns None if it can't be
    determined yet (no network and nothing cached).
    """
    if not is_auto(configured):
        return configured
    cached, fetched_at = _read_cache(cache_path)
    if cached and now() - fetched_at < CACHE_MAX_AGE:
        return cached
    try:
        location = lookup_ip_location(fetch)
    except Exception as e:
        print(f"Location lookup failed ({e})" + ("; using the cached location" if cached else ""))
        return cached
    print(f"Location from IP address: {location.get('city') or 'unknown city'} "
          f"({location['lat']:.2f}, {location['lon']:.2f})")
    try:
        Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
        Path(cache_path).write_text(json.dumps({'location': location, 'fetched_at': now()}, indent=2))
    except OSError as e:
        print(f"Couldn't cache the location: {e}")
    return location
