"""Deterministic approximate market planning, never a count of all companies.

Geocoding is city-only. Successful lookups remain cached indefinitely;
failed lookups may retry after 15 minutes or a longer server cooldown.
The parent owns scheduling, attribution display and unresolved saturated leaves.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
import uuid

import aiohttp
from .sources.website import PublicResolver, UnsafeURL, normalize_url

REQUEST_SPACING = 15.0
FAILURE_CACHE_TTL = 15 * 60
RATE_LIMIT_FILE = Path.home() / '.cache/local-lead-workbench/nominatim.lock'
MAX_BODY_BYTES = 256_000
MAX_LEVEL = 3
MIN_CELL_KM = 0.5
INTERNAL_JOB_LIMIT = 120
JOB_TIMEOUT_SECONDS = 180
ATTRIBUTION = '© OpenStreetMap contributors; city geocoding by Nominatim (ODbL)'
# P0-5: a text-only plan is always reported as incomplete; the prefix identifies the reason.
INCOMPLETE_PREFIX = '结果不完整：'
INCOMPLETE_REASONS = {
    'geocode_failed': '结果不完整：城市范围定位失败，本轮只按城市名称做了文字检索（单次检索通常只返回首批结果）。'
                      '可点击“重试定位并补查”。',
    'catalogue_no_bounds': '结果不完整：内置目录中该城市没有范围坐标，城市定位也未成功，本轮只按城市名称做了文字检索'
                           '（单次检索通常只返回首批结果）。可点击“重试定位并补查”。',
}
TYPE_QUERIES = {
    'wholesale': ('clothing wholesaler', 'apparel wholesaler'),
    'retail': ('clothing retailer', 'clothing store'),
    'import': ('garment importer', 'apparel trading company'),
}


def _text(value, *, allow_empty=False):
    if not isinstance(value, str) or any(ord(c) < 32 for c in value) or '#!#' in value:
        raise ValueError('Expected a single plain text value')
    value = ' '.join(value.split())
    if (not value and not allow_empty) or len(value) > 200:
        raise ValueError('Text must contain 1–200 characters')
    return value


def _number(value, bound):
    if isinstance(value, bool):
        raise ValueError('Boolean coordinate')
    number = float(value)
    if not math.isfinite(number) or abs(number) > bound:
        raise ValueError('Coordinate out of range')
    return number


def _bbox(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError('Expected [south,north,west,east]')
    s, n, w, e = [_number(v, 90 if i < 2 else 180) for i, v in enumerate(value)]
    if s >= n or w >= e:
        raise ValueError('Invalid or antimeridian-crossing city bounds')
    return [s, n, w, e]


def _city_result(rows, country, city, state):
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        raise ValueError('No city result')
    row = rows[0]
    if row.get('addresstype') not in {'city', 'town', 'municipality'}:
        raise ValueError('Geocoder result is not a city administrative area')
    if row.get('category', row.get('class')) not in {'boundary', 'place'}:
        raise ValueError('Geocoder returned a POI instead of a city')
    if not isinstance(row.get('address'), dict) or row['address'].get('country_code', '').lower() != country:
        raise ValueError('Geocoder country mismatch')
    address = row['address']
    returned_city = address.get(row['addresstype']) or address.get('city') or row.get('name')
    if returned_city and _text(returned_city).casefold() != city.casefold():
        raise ValueError('Geocoder city mismatch')
    returned_state = address.get('state')
    if state and returned_state:
        state_names = {_text(returned_state).casefold()}
        state_code = address.get('ISO3166-2-lvl4', '')
        if isinstance(state_code, str) and state_code.casefold().startswith(country + '-'):
            state_names.add(state_code.split('-', 1)[1].casefold())
        if state.casefold() not in state_names:
            raise ValueError('Geocoder state mismatch')
    bounds = _bbox(row.get('boundingbox'))
    lat, lon = _number(row.get('lat'), 90), _number(row.get('lon'), 180)
    s, n, w, e = bounds
    if not s <= lat <= n or not w <= lon <= e:
        raise ValueError('City center is outside returned bounds')
    return {'geo': [lat, lon], 'bbox': bounds}


class CatalogueNoBounds(ValueError):
    """The selected catalogue place has no reference bounds."""


class GeocoderCooldown(ValueError):
    def __init__(self, not_before, message='City geocoder is cooling down; no request sent'):
        super().__init__(message)
        self.not_before = not_before


def _retry_not_before(header):
    now = time.time()
    until = now + FAILURE_CACHE_TTL
    if not isinstance(header, str) or len(header) > 128:
        return until
    header = header.strip()
    try:
        if re.fullmatch(r'[0-9]+', header):
            server_until = now + float(header)
        else:
            date = parsedate_to_datetime(header)
            server_until = date.replace(tzinfo=date.tzinfo or timezone.utc).timestamp()
        if math.isfinite(server_until):
            until = max(until, server_until)
    except (ValueError, TypeError, OverflowError):
        pass
    return until


async def _fetch_city(endpoint, params):
    # No redirects, inherited proxies, retries, or a second unvalidated DNS lookup.
    endpoint = normalize_url(endpoint)
    connector = aiohttp.TCPConnector(resolver=PublicResolver(), use_dns_cache=False)
    ua = os.environ.get('NOMINATIM_USER_AGENT', 'LocalLeadWorkbench/0.1 (city-market-planner; local desktop application)')
    if not ua.strip() or any(c in ua for c in '\r\n'):
        raise ValueError('Invalid Nominatim User-Agent')
    async with aiohttp.ClientSession(connector=connector, trust_env=False,
                                   timeout=aiohttp.ClientTimeout(total=20),
                                   headers={'User-Agent': ua, 'Accept': 'application/json'}) as session:
        async with session.get(endpoint, params=params, allow_redirects=False) as response:
            if response.status in {429, 503}:
                until = _retry_not_before(getattr(response, 'headers', {}).get('Retry-After'))
                raise GeocoderCooldown(until, f'City geocoder HTTP {response.status}; shared cooldown')
            if response.status != 200:
                raise ValueError(f'City geocoder HTTP {response.status}; no retry')
            if response.content_length is not None and response.content_length > MAX_BODY_BYTES:
                raise ValueError('City response exceeds body budget')
            body = bytearray()
            async for chunk in response.content.iter_chunked(16384):
                body.extend(chunk)
                if len(body) > MAX_BODY_BYTES:
                    raise ValueError('City response exceeds body budget')
            return json.loads(body)


@asynccontextmanager
async def _machine_lock():
    # Shared across application data directories and processes for this desktop
    # account. flock is nonblocking so waiting never blocks the event loop.
    RATE_LIMIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with RATE_LIMIT_FILE.open('a+') as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                await asyncio.sleep(0.05)
        try:
            yield lock
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _reservation(lock):
    lock.seek(0)
    content = lock.read().strip()
    try:
        saved = json.loads(content) if content else None
        # Existing installations stored only the last request's numeric timestamp.
        last = saved.get('last_request_at') if isinstance(saved, dict) else saved
        until = float(saved.get('not_before', 0)) if isinstance(saved, dict) else 0
        last = float(last) if last is not None else None
        if not math.isfinite(until) or (last is not None and not math.isfinite(last)):
            raise ValueError('Invalid limiter timestamp')
        return last, until
    except (ValueError, TypeError):
        # Damaged limiter state must not permit a request burst.
        return time.time(), 0


def _save_reservation(lock, last, until=0):
    lock.seek(0)
    lock.truncate()
    lock.write(json.dumps({'last_request_at': last, 'not_before': until}))
    lock.flush()
    os.fsync(lock.fileno())


async def _reserve_request(lock):
    last, until = _reservation(lock)
    now = time.time()
    if until > now:
        raise GeocoderCooldown(until)
    if last is not None:
        delay = max(0.0, REQUEST_SPACING - (now - last))
        if delay > REQUEST_SPACING:
            # Clock rollback must not tie up a worker for an unbounded sleep.
            raise GeocoderCooldown(last + REQUEST_SPACING)
        if delay:
            await asyncio.sleep(delay)
    _save_reservation(lock, time.time())


def _city_cache(city, state, country, data_dir):
    endpoint = os.environ.get('NOMINATIM_ENDPOINT', 'https://nominatim.openstreetmap.org/search')
    identity = [endpoint, city.casefold(), state.casefold(), country]
    key = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
    return Path(data_dir) / 'geocoding' / f'{key}.json'


def geocoder_retry_at(city, state, country, data_dir):
    """Epoch seconds before which the geocoder will not be asked again (server or shared cooldown), or None."""
    until = 0.0
    try:
        cached = json.loads(_city_cache(city, state, country.lower(), data_dir).read_text(encoding='utf-8'))
        until = float(cached.get('retry_not_before') or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    try:
        saved = json.loads(RATE_LIMIT_FILE.read_text() or 'null')
        if isinstance(saved, dict):
            until = max(until, float(saved.get('not_before') or 0))
    except (OSError, ValueError, TypeError):
        pass
    return until if math.isfinite(until) and until > time.time() else None


def _catalogue_fallback(country, state, city, skip_place=None):
    """Reference bounds of the uniquely named catalogue place, or None (local lookup, no network)."""
    try:
        from .regions import catalogue, country_config, index, unique_place_id
        place_id = unique_place_id(country.upper(), state, city)
        if not place_id or place_id == skip_place:
            return None
        place = index()[place_id]
        if not place.get('bbox') or not place.get('geo'):
            return None
        bounds = _bbox(place['bbox'])
        vintage = catalogue(country.upper())['metadata']['vintage']
        if country == 'us':
            attribution = f"U.S. Census Bureau TIGERweb · {vintage} · Place {place['geoid']}"
        else:
            attribution = f"{country_config(country.upper())['attribution']} · {vintage}"
        return {'geo': place['geo'], 'bbox': bounds}, attribution, vintage
    except (OSError, ValueError, KeyError, TypeError):
        return None


async def _geocode(city, state, country, data_dir, ignore_failure_cache=False):
    endpoint = os.environ.get('NOMINATIM_ENDPOINT', 'https://nominatim.openstreetmap.org/search')
    params = {'city': city, 'state': state, 'countrycodes': country,
              'featureType': 'city', 'format': 'jsonv2', 'addressdetails': '1',
              'limit': '1', 'accept-language': 'en'}
    identity = [endpoint, city.casefold(), state.casefold(), country]
    key = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
    cache = Path(data_dir) / 'geocoding' / f'{key}.json'
    # Read under the same lock so simultaneous city plans cannot double-fetch.
    async with _machine_lock() as lock:
        if cache.exists():
            try:
                cached = json.loads(cache.read_text(encoding='utf-8'))
                if cached['params'] != params:
                    # Case-only city/state differences share a cache identity.
                    assert [cached['params'][k].casefold() for k in ('city', 'state', 'countrycodes')] == [city.casefold(), state.casefold(), country]
                if cached['status'] == 'completed':
                    return _city_result(cached['response'], country, city, state), []
                failed_at = cached.get('failed_at')
                if failed_at is None:
                    # Existing failure caches predate the explicit epoch field.
                    failed_at = datetime.fromisoformat(cached['observed_at']).timestamp()
                failed_at = float(failed_at)
                if not math.isfinite(failed_at):
                    raise ValueError('Invalid failure cache timestamp')
                retry_at = float(cached.get('retry_not_before', 0))
                if not math.isfinite(retry_at):
                    raise ValueError('Invalid failure cache retry timestamp')
                # Relocation skips the 15-minute failure cache, never a server cooldown.
                if time.time() < (retry_at if ignore_failure_cache else max(failed_at + FAILURE_CACHE_TTL, retry_at)):
                    warning = str(cached['warning'])
                    if warning.startswith('City geocoding unavailable:'):
                        # Old caches did not retain exception types. Do not
                        # infer timeout from an empty message or retry to learn it.
                        detail = warning.removeprefix('City geocoding unavailable:').split(
                            '; root searches only', 1)[0].strip()
                        warning = '城市范围定位未完成（旧缓存未记录异常类型），暂用城市文字检索'
                        if detail:
                            warning += f'。原始信息：{detail}'
                    return None, [warning]
            except (ValueError, KeyError, TypeError, AssertionError, OSError):
                return None, ['City cache is unreadable; no automatic repeated lookup; review the cache manually']
        cached = {'params': params, 'endpoint': endpoint, 'observed_at': datetime.now(timezone.utc).isoformat()}
        try:
            normalize_url(endpoint)  # Reject literal internal endpoints before even reserving a request.
            await _reserve_request(lock)
            response = await _fetch_city(endpoint, params)
            cached['response'] = response
            city_result = _city_result(response, country, city, state)
            cached['status'] = 'completed'
            warnings = []
        except (aiohttp.ClientError, TimeoutError, ValueError, TypeError, OSError, UnsafeURL) as exc:
            if isinstance(exc, GeocoderCooldown):
                last, until = _reservation(lock)
                _save_reservation(lock, last, max(until, exc.not_before))
                cached['retry_not_before'] = exc.not_before
            city_result = None
            brief = '城市范围定位超时' if isinstance(exc, TimeoutError) else '城市范围定位未完成'
            warning = f'{brief}（{type(exc).__name__}），暂用城市文字检索'
            detail = str(exc).strip()
            if detail:
                warning += f'。详细信息：{detail}'
            cached.update(status='partial', failed_at=time.time(),
                          error_type=type(exc).__name__, error_message=detail,
                          warning=warning)
            warnings = [cached['warning']]
        cache.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache.with_name(cache.name + '.' + uuid.uuid4().hex + '.tmp')
        temporary.write_text(json.dumps(cached, ensure_ascii=False), encoding='utf-8')
        temporary.replace(cache)
        return city_result, warnings


def _job(query, geo, bbox, level, zoom=12, depth=10):
    job = dict(query=query, geo=geo, zoom=zoom, depth=depth, bbox=bbox, level=level)
    job['key'] = hashlib.sha256(json.dumps(job, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    return job


def _grid(job, count, level):
    s, n, w, e = _bbox(job['bbox'])
    children = []
    for row in range(count):
        for col in range(count):
            bounds = [s+(n-s)*row/count, s+(n-s)*(row+1)/count,
                      w+(e-w)*col/count, w+(e-w)*(col+1)/count]
            bounds = [round(v, 10) for v in bounds]
            geo = [round((bounds[0]+bounds[1])/2, 10), round((bounds[2]+bounds[3])/2, 10)]
            children.append(_job(job['query'], geo, bounds, level,
                                 min(21, job['zoom'] + (2 if count == 4 else 1)), job['depth']))
    return children


def exhaustion_reason(job, *, job_budget=False):
    if job_budget:
        limit = "本轮检索任务预算已用尽"
    elif job.get("bbox") is None:
        limit = "缺少城市范围，无法继续细分"
    elif job["level"] >= MAX_LEVEL:
        limit = "已达到当前区域细分层级上限"
    else:
        limit = "已达到当前最小检索区域"
    return (f"当前策略覆盖能力已耗尽：{limit}；已保留取得的企业，但仍有未覆盖结果。"
            "此项不能通过继续重试补齐，需调整检索策略；新一轮更新仅复核来源，不保证扩大覆盖。")


def split_job(job) -> list[dict]:
    """Four density refinements, or [] => saturated leaf remains unresolved.

    Only the worker knows whether a job hit INTERNAL_JOB_LIMIT. No source calls.
    Root subdivisions may overlap the existing coarse grid; keys deduplicate jobs.
    """
    if job.get('bbox') is None or job['level'] >= MAX_LEVEL:
        return []
    s, n, w, e = _bbox(job['bbox'])
    height = (n-s) * 111.32
    width = (e-w) * 111.32 * math.cos(math.radians((s+n)/2))
    if min(height, width) / 2 < MIN_CELL_KM:
        return []
    return _grid(job, 2, job['level'] + 1)


def keyword_terms(snapshot) -> set[str]:
    """Normalized user keywords; shared by planning and legacy-round filtering."""
    return {_text(keyword).casefold() for keyword in snapshot.get('keywords') or []}


async def plan_market(config, data_dir: Path) -> dict:
    city = _text(config.get('city'))
    state = _text(config.get('state', ''), allow_empty=True)
    country = _text(config.get('country_code', config.get('country', ''))).lower()
    if not re.fullmatch('[a-z]{2}', country):
        raise ValueError('country must be an ISO two-letter country code')
    types = config.get('company_types', [])
    keywords = config.get('keywords', [])
    if not isinstance(types, (list, tuple)) or not isinstance(keywords, (list, tuple)):
        raise ValueError('company_types and keywords must be lists')
    warnings = []
    # User keywords are the whole query set; company types only name/filter the
    # market and supply apparel default queries when no keyword is given.
    terms = keyword_terms({'keywords': keywords})
    basis = 'keywords'
    if not terms:
        basis = 'company_types'
        for kind in types:
            kind = _text(kind).casefold()
            if kind in TYPE_QUERIES:
                terms.update(TYPE_QUERIES[kind])
            else:
                warnings.append(f'Unsupported company type: {kind}; no type expansion applied')
    if not terms:
        raise ValueError('Select a supported company type or provide a keyword')
    location = ', '.join(part for part in (city, state, country.upper()) if part)
    attribution = ATTRIBUTION
    place_id = (config.get("geography") or {}).get("place_id")
    geometry, geo_warnings, source, catalogue_error = None, [], None, None
    try:
        if place_id:
            from .regions import index, catalogue, country_config
            place = index().get(place_id)
            if not place or place["kind"] != "place" or not place["state_id"].startswith(country.upper() + "-"):
                raise ValueError("Unknown reference place")
            if place["state_id"] != (config.get("geography") or {}).get("state_id"):
                raise ValueError("Reference place/state mismatch")
            if state.casefold() != index()[place["state_id"]]["name"].casefold() or city.casefold() not in {
                    place["name"].casefold(), place["official_name"].casefold()}:
                raise ValueError("Reference place/name mismatch")
            if not place.get("bbox") or not place.get("geo"):
                raise CatalogueNoBounds("所选地区目录未提供范围坐标；按自治市完整名称进行文字检索")
            bounds = _bbox(place["bbox"])
            if bounds[3]-bounds[2] > 180:
                raise ValueError("Reference place crosses the antimeridian")
            geometry = {"geo": place["geo"], "bbox": bounds}
            source = 'catalogue'
            if country == "us":
                attribution = f"U.S. Census Bureau TIGERweb · {catalogue()['metadata']['vintage']} · Place {place['geoid']}"
            else:
                attribution = f"{country_config(country.upper())['attribution']} · {catalogue(country.upper())['metadata']['vintage']}"
    except CatalogueNoBounds as exc:
        catalogue_error = 'no_bounds'
        geo_warnings.append(str(exc))
    except (OSError, ValueError) as exc:
        catalogue_error = 'invalid'
        geo_warnings.append(f'City cache unavailable: {str(exc).strip() or type(exc).__name__}; root searches only')
    whole_municipality = (config.get('geography') or {}).get('scope') == 'whole_municipality'
    # A municipality is not a geocoder "city": never look it up by name (keeps the ZA rule).
    if geometry is None and not (catalogue_error and whole_municipality):
        try:
            relocate = {'ignore_failure_cache': True} if config.get('_relocate') else {}
            geometry, warnings_found = await _geocode(city, state, country, data_dir, **relocate)
            geo_warnings.extend(warnings_found)
            if geometry:
                source, attribution = 'geocoder', ATTRIBUTION
        except (OSError, ValueError) as exc:
            detail = str(exc).strip() or type(exc).__name__
            geometry = None
            geo_warnings.append(f'City cache unavailable: {detail}; root searches only')
    if geometry is None and not whole_municipality:
        fallback = _catalogue_fallback(country, state, city, place_id if catalogue_error == 'no_bounds' else None)
        if fallback:
            geometry, attribution, vintage = fallback
            source = 'catalogue'
            geo_warnings.append(f'已改用内置城市范围（{vintage}）')
    unresolved, incomplete_reason, retry_at = [], None, None
    if geometry is None:
        incomplete_reason = 'catalogue_no_bounds' if catalogue_error == 'no_bounds' else 'geocode_failed'
        reason = INCOMPLETE_REASONS[incomplete_reason]
        retry_at = geocoder_retry_at(city, state, country, data_dir)
        if retry_at:
            stamp = datetime.fromtimestamp(retry_at, timezone.utc).isoformat()
            reason += f'定位服务冷却中，请在 {stamp} 后重试。'
        unresolved = [reason]
    # Geometry warnings describe the chosen fallback, not unfinished planning.
    incomplete = bool(warnings)
    warnings.extend(geo_warnings)
    jobs = []
    for term in sorted(terms):
        root = _job(f'{term} in {location}', geometry['geo'] if geometry else None,
                    geometry['bbox'] if geometry else None, 0)
        jobs.append(root)
        if geometry:
            jobs.extend(_grid(root, 4, 1))
    scope_label = '自治市' if (config.get('geography') or {}).get('scope') == 'whole_municipality' else '城市'
    plan = dict(jobs=jobs, coverage={
        'scope_label': f'{location} · {scope_label}参考范围分区检索' if geometry else
                       f'{location} · {scope_label}文字检索（范围定位未完成）',
        'bbox': geometry['bbox'] if geometry else None,
        'attribution': attribution if geometry else '',
        'query_basis': basis,
        'query_terms': sorted(terms)[:30],
        'geometry_source': source,
    }, warnings=sorted(set(warnings)), mode='grid' if geometry else 'text_only',
       status='partial' if incomplete else 'completed', geometry_source=source)
    if unresolved:
        # status stays 'completed': "incomplete" travels only through unresolved (finalize -> partial).
        plan.update(unresolved=unresolved, incomplete_reason=incomplete_reason,
                    geocoder_retry_at=datetime.fromtimestamp(retry_at, timezone.utc).isoformat() if retry_at else None)
    return plan
