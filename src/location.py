"""
Where the projector is, for local weather and sunrise/sunset.

Three ways to set it, most to least precise:

  "location": {"lat": 45.6, "lon": -123.2, "timezone": "America/Los_Angeles"}
  "location": {"address": "123 Main St, Springfield, IL 62701"}
  "location": "auto"   (or leave it out: looked up from the public IP address, city-level)

Addresses are geocoded once (US Census geocoder, falling back to
OpenStreetMap's Nominatim) and cached until the address changes; IP
lookups are cached for a week. Both keep working offline from the cache.
"""

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path


CACHE_PATH = Path("config/location.auto.json")
ADDRESS_CACHE_PATH = Path("config/location.address.json")
CACHE_MAX_AGE = 7 * 24 * 3600
TIMEOUT = 10


def is_auto(location):
    return (location is None or location == "auto"
            or (isinstance(location, dict) and ('lat' not in location or 'lon' not in location)
                and 'address' not in location))


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


def _census(address, fetch):
    """US Census Bureau geocoder: precise for US street addresses, including rural ones."""
    url = ("https://geocoding.geo.census.gov/geocoder/locations/onelineaddress?"
           + urllib.parse.urlencode({'address': address, 'benchmark': 'Public_AR_Current', 'format': 'json'}))
    matches = fetch(url)['result']['addressMatches']
    if not matches:
        raise ValueError("no match")
    coords = matches[0]['coordinates']
    return {'lat': coords['y'], 'lon': coords['x'], 'matched': matches[0]['matchedAddress']}


def _nominatim(address, fetch):
    """OpenStreetMap's geocoder, for addresses outside the US."""
    url = ("https://nominatim.openstreetmap.org/search?"
           + urllib.parse.urlencode({'q': address, 'format': 'json', 'limit': 1}))
    results = fetch(url)
    if not results:
        raise ValueError("no match")
    return {'lat': float(results[0]['lat']), 'lon': float(results[0]['lon']),
            'matched': results[0]['display_name']}


def _timezone_for(lat, lon, fetch):
    url = ("https://api.open-meteo.com/v1/forecast?"
           + urllib.parse.urlencode({'latitude': lat, 'longitude': lon, 'timezone': 'auto'}))
    return fetch(url).get('timezone')


def geocode_address(address, fetch=_fetch_json):
    """Coordinates and timezone for a street address. Raises if it can't be found."""
    errors = []
    for name, geocoder in (("Census", _census), ("Nominatim", _nominatim)):
        try:
            location = geocoder(address, fetch)
            break
        except Exception as e:
            errors.append(f"{name}: {e}")
    else:
        raise RuntimeError("; ".join(errors))
    try:
        location['timezone'] = _timezone_for(location['lat'], location['lon'], fetch)
    except Exception:
        location['timezone'] = None   # falls back to the system's timezone
    return location


def _write_cache(path, data):
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(data, indent=2))
    except OSError as e:
        print(f"Couldn't cache the location: {e}")


def resolve_address(configured, cache_path=ADDRESS_CACHE_PATH, fetch=_fetch_json):
    """The geocoded location for configured["address"] (cached until the address changes), or None."""
    address = configured['address'].strip()
    try:
        cached = json.loads(Path(cache_path).read_text())
        if cached.get('address') == address:
            return {**cached['location'], **_overrides(configured)}
    except (OSError, ValueError, KeyError):
        pass
    try:
        location = geocode_address(address, fetch)
    except Exception as e:
        print(f"Couldn't find the address {address!r} ({e})")
        return None
    print(f"Location from address: {location.get('matched')} ({location['lat']:.5f}, {location['lon']:.5f})")
    _write_cache(cache_path, {'address': address, 'location': location})
    return {**location, **_overrides(configured)}


def _overrides(configured):
    """Fields set alongside an address (e.g. timezone) win over the looked-up ones."""
    return {k: v for k, v in configured.items() if k in ('timezone',)}


def _read_cache(path):
    try:
        cached = json.loads(Path(path).read_text())
        return cached['location'], cached['fetched_at']
    except (OSError, ValueError, KeyError):
        return None, None


def resolve_location(configured, cache_path=CACHE_PATH, fetch=_fetch_json, now=time.time,
                     address_cache_path=ADDRESS_CACHE_PATH):
    """
    The location to use: the configured lat/lon if set, else the geocoded
    address, else the IP lookup (from the cache while it's fresh). Returns
    None if it can't be determined yet (no network and nothing cached).
    """
    if isinstance(configured, dict) and 'lat' in configured and 'lon' in configured:
        return configured
    if isinstance(configured, dict) and 'address' in configured:
        location = resolve_address(configured, address_cache_path, fetch)
        if location is not None:
            return location
        print("Falling back to the IP address location")
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
    _write_cache(cache_path, {'location': location, 'fetched_at': now()})
    return location
