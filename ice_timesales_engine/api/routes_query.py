"""
routes_query.py -- /v1/sessionvol/* read-only endpoints.

All GET; Cloudflare-fronted; freshness envelope mirrors the VLM gateway
convention (source / stale / stale_age_seconds surfaced on every response).
"""

from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request

import commodity_meta
from api import price as price_mod
from api import windows as win
from api.cache import cache_headers
from ingest import classifier
from store import repository as repo

bp = Blueprint('sessionvol', __name__, url_prefix='/v1/sessionvol')


def _db():
    return current_app.config['DB']


def _csv_param(name):
    raw = (request.args.get(name) or '').strip()
    if not raw or raw.lower() == 'all':
        return None
    return [p.strip().upper() for p in raw.split(',') if p.strip()]


def _dates_param(cmd):
    """date=YYYY-MM-DD or from=..&to=.. -> list of ingested session dates."""
    one = request.args.get('date')
    if one:
        return [one]
    d_from, d_to = request.args.get('from'), request.args.get('to')
    if d_from and d_to:
        return [d for d in repo.available_dates(_db(), cmd) if d_from <= d <= d_to]
    return None


def _freshness(cmd, dates):
    f = repo.freshness(_db(), cmd)
    latest = f.get('latest_session')
    stale = bool(dates) and bool(latest) and max(dates) > latest
    age = None
    if f.get('last_ingest_at'):
        try:
            age = int((datetime.now(timezone.utc)
                       - datetime.fromisoformat(f['last_ingest_at'])).total_seconds())
        except ValueError:
            stale = True
    return {'source': f['source'], 'latest_session': latest,
            'last_ingest_at': f.get('last_ingest_at'),
            'stale': stale, 'stale_age_seconds': age}


def _respond(payload, dates, cmd):
    resp = jsonify(payload)
    latest = repo.freshness(_db(), cmd).get('latest_session')
    for k, v in cache_headers(dates or [], latest).items():
        resp.headers[k] = v
    return resp


@bp.get('/<cmd>/window')
def window(cmd):
    cmd = cmd.upper()
    dates = _dates_param(cmd)
    if not dates:
        return jsonify({'error': 'date=YYYY-MM-DD or from=&to= required'}), 400
    try:
        contracts = _csv_param('contracts')
        types = _csv_param('types')
        types = [t.lower() for t in types] if types else None
        groupby = request.args.get('groupby', 'type')

        totals = {'all': 0.0, 'clean': 0.0, 'by_type': {}, 'by_contract': {}}
        resolved = None
        per_date = {}
        cutoff = repo.bloomberg_cutoff(_db(), cmd)
        for d in dates:
            w = win.resolve(d, preset=request.args.get('preset'),
                            start=request.args.get('start'),
                            end=request.args.get('end'))
            resolved = w
            # Source rule C: seed-era dates from bar5m bloomberg, later
            # dates from the live ICE tables -- one source per era.
            src = 'bloomberg' if (cutoff and d <= cutoff) else None
            r = repo.window_sum(_db(), cmd, w['start'], w['end'],
                                contracts=contracts, types=types,
                                session_date=d, source=src)
            per_date[d] = r
            totals['all'] += r['all']
            totals['clean'] += r['clean']
            for k, v in r['by_type'].items():
                totals['by_type'][k] = totals['by_type'].get(k, 0.0) + v
            for k, v in r['by_contract'].items():
                totals['by_contract'][k] = totals['by_contract'].get(k, 0.0) + v
        # 'outright' is now a primary_type in its own right (incl. blank/
        # unstamped fills) -- no ask+bid synthesis needed.
        payload = {
            'commodity': cmd, 'dates': dates,
            'window': resolved, 'groupby': groupby,
            'totals': totals,
            'per_date': per_date if len(dates) > 1 else None,
            'freshness': _freshness(cmd, dates),
            # reconcile-vs-settle is an OPS check only (settle files are stale
            # carry-forward, not trusted) -- kept at /{cmd}/reconcile, dropped
            # from the trader-facing window payload.
        }
        return _respond(payload, dates, cmd)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400


@bp.get('/<cmd>/profile')
def profile(cmd):
    """Single date (date=) -> {profile: [...]}, unchanged.
    Range (from=&to=) -> {per_date: {date: [...]}} -- the SAME window preset/
    custom bounds are resolved per session date, so day/night/custom filters
    behave identically in every view mode. The client stitches (continuous)
    or overlays (compare) the per-date series."""
    cmd = cmd.upper()
    dates = _dates_param(cmd)
    if not dates:
        return jsonify({'error': 'date=YYYY-MM-DD or from=&to= required'}), 400
    try:
        bucket = {'1m': 1, '5m': 5, '15m': 15, '60m': 60, 'full': 'full'}[
            request.args.get('bucket', '15m')]
    except KeyError:
        return jsonify({'error': 'bucket must be 1m|5m|15m|60m|full'}), 400
    try:
        types = _csv_param('types')
        types = [t.lower() for t in types] if types else None
        contracts = _csv_param('contracts')

        # Only fall back to the 'full' preset when NO custom start/end was
        # given -- otherwise the default preset would silently override the
        # caller's custom window (win.resolve checks preset first).
        c_start, c_end = request.args.get('start'), request.args.get('end')
        preset = request.args.get('preset') or (None if (c_start and c_end) else 'full')

        cutoff = repo.bloomberg_cutoff(_db(), cmd)
        per_date, windows = {}, {}
        seed_dates = 0
        for d in dates:
            w = win.resolve(d, preset=preset, start=c_start, end=c_end)
            windows[d] = w
            # Source rule C: seed-era dates from bar5m bloomberg, later
            # dates from the live ICE tables -- one source per era.
            src = 'bloomberg' if (cutoff and d <= cutoff) else None
            seed_dates += bool(src)
            per_date[d] = repo.profile(_db(), cmd, w['start'], w['end'], bucket,
                                       contracts=contracts, types=types,
                                       session_date=d, source=src)

        payload = {'commodity': cmd, 'dates': dates,
                   'bucket_minutes': bucket,
                   'freshness': _freshness(cmd, dates)}
        # Seed grain is 5 min -- flag when a finer request was coarsened.
        # ('full' collapses everything into one bucket regardless of grain,
        # so it's never "coarsened up" the way a sub-5m request is.)
        if seed_dates and bucket != 'full' and bucket < 5:
            payload['bucket_minutes_effective'] = 5
        if len(dates) == 1:
            d = dates[0]
            payload.update({'date': d, 'window': windows[d],
                            'profile': per_date[d]})
        else:
            payload.update({'windows': windows, 'per_date': per_date})
        return _respond(payload, dates, cmd)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400


@bp.get('/<cmd>/contracts')
def contracts(cmd):
    cmd = cmd.upper()
    d = request.args.get('date')
    if not d:
        return jsonify({'error': 'date=YYYY-MM-DD required'}), 400
    cutoff = repo.bloomberg_cutoff(_db(), cmd)
    src = 'bloomberg' if (cutoff and d <= cutoff) else None
    return _respond({'commodity': cmd, 'date': d,
                     'contracts': repo.traded_contracts(_db(), cmd, d, source=src),
                     'freshness': _freshness(cmd, [d])}, [d], cmd)


@bp.get('/<cmd>/reconcile')
def reconcile(cmd):
    cmd = cmd.upper()
    d = request.args.get('date')
    if not d:
        return jsonify({'error': 'date=YYYY-MM-DD required'}), 400
    return _respond({'commodity': cmd, 'date': d,
                     'reconcile': repo.reconcile_rows(_db(), cmd, d),
                     'freshness': _freshness(cmd, [d])}, [d], cmd)


@bp.get('/<cmd>/price')
def price(cmd):
    """Futures settlement price + OHLC (front month, or a specific ice_code)
    for one date or a date range -- read from the ICE eod capture root
    (futures_settle_<date>.csv, config.ICE_ROOT), the SAME read-only source
    this engine's blotter ingest already uses for volume. A date with no
    settle file (weekend, holiday, today's session not yet closed) returns
    a real None, not an error -- that's an honest absence. A genuinely
    unreadable file on a SINGLE date (date=) is a real 502 for that request;
    in a RANGE (from=/to=), one bad date's read error is caught per-date
    (errored_dates) rather than blanking out every other date's real,
    successfully-read settle in the same response."""
    cmd = cmd.upper()
    dates = _dates_param(cmd)
    if not dates:
        return jsonify({'error': 'date=YYYY-MM-DD or from=&to= required'}), 400
    ice_code = (request.args.get('contract') or '').strip().upper() or None
    try:
        if len(dates) == 1:
            row = price_mod.settle_for(cmd, dates[0], ice_code=ice_code)
            payload = {'commodity': cmd, 'date': dates[0], 'settle': row}
        else:
            series, errored_dates = price_mod.settle_series(cmd, dates, ice_code=ice_code)
            avg_vals = [v['settle'] for v in series.values() if v is not None]
            payload = {
                'commodity': cmd, 'dates': dates, 'series': series,
                'avg_settle': (sum(avg_vals) / len(avg_vals)) if avg_vals else None,
                'errored_dates': errored_dates,
            }
        return jsonify(payload)
    except price_mod.PriceUnavailable as exc:
        return jsonify({'error': f'price unavailable: {exc}'}), 502


@bp.get('/catalog')
def catalog():
    db = _db()
    return jsonify({
        'commodities': [
            {'code': c, 'name': commodity_meta.COMMODITY_NAME[c],
             'months': commodity_meta.month_picker(c),
             'available_dates': repo.available_dates(db, c),
             'freshness': _freshness(c, None)}
            for c in commodity_meta.SUPPORTED_COMMODITIES
        ],
        'presets': list(win.PRESETS),
        # Every queryable bucket, live and cancelled. The cancelled half is
        # DERIVED from classifier.CANCELLED_TYPES rather than hand-listed, so a
        # future widening of R11 cannot leave cancelled volume unadvertised and
        # therefore unretrievable (requirement 1 of the 2026-08-24 tag ruling).
        'types': (['outright', 'leg', 'efs', 'efp', 'block', 'other']
                  + list(classifier.CANCELLED_TYPES)),
        'cancelled_types': list(classifier.CANCELLED_TYPES),
    })
