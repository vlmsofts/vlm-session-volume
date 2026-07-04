"""
cache.py -- Cache-Control headers + Cloudflare cache-tag purge.

The daily collector is the SOLE writer, so invalidation is event-driven and
clean: after a day's ingest, purge tag sv:{cmd}:{session_date}. Past/closed
sessions get a long immutable TTL; the latest session a short TTL.
Cloudflare purge is a no-op when CF_ZONE_ID / CF_API_TOKEN are unset.
"""

import json
import sys
import urllib.request

import config


def cache_tag(commodity: str, session_date: str) -> str:
    return f'sv:{commodity.upper()}:{session_date}'


def cache_headers(session_dates: list, latest_session: str) -> dict:
    """Cache-Control + Cache-Tag for a response covering session_dates."""
    is_latest = bool(latest_session) and latest_session in (session_dates or [])
    ttl = config.CACHE_TTL_LATEST_SESSION if is_latest else config.CACHE_TTL_CLOSED_SESSION
    tags = ','.join(cache_tag('CT', d) for d in (session_dates or []))
    hdrs = {'Cache-Control': f'public, max-age={ttl}'}
    if tags:
        hdrs['Cache-Tag'] = tags
    return hdrs


def purge_day(commodity: str, session_date: str) -> bool:
    """Purge the Cloudflare edge cache for one commodity-day. Returns True on
    success or no-op (unconfigured); False on an API error (logged, not fatal
    -- TTLs are the backstop)."""
    if not (config.CF_ZONE_ID and config.CF_API_TOKEN):
        return True   # unconfigured -> no-op
    url = f'https://api.cloudflare.com/client/v4/zones/{config.CF_ZONE_ID}/purge_cache'
    body = json.dumps({'tags': [cache_tag(commodity, session_date)]}).encode()
    req = urllib.request.Request(
        url, data=body, method='POST',
        headers={'Authorization': f'Bearer {config.CF_API_TOKEN}',
                 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            ok = json.load(resp).get('success', False)
            if not ok:
                print(f'WARNING: Cloudflare purge returned success=false for '
                      f'{cache_tag(commodity, session_date)}', file=sys.stderr)
            return ok
    except Exception as exc:
        print(f'WARNING: Cloudflare purge failed: {exc}', file=sys.stderr)
        return False
