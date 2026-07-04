"""
routes_query.py -- /v1/sessionvol/* read-only endpoints.

All GET; Cloudflare-fronted; freshness envelope mirrors the VLM gateway
convention (source / stale / stale_age_seconds surfaced on every response).
"""

from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request

import commodity_meta
from api import windows as win
from api.cache import cache_headers
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
        for d in dates:
            w = win.resolve(d, preset=request.args.get('preset'),
                            start=request.args.get('start'),
                            end=request.args.get('end'))
            resolved = w
            r = repo.window_sum(_db(), cmd, w['start'], w['end'],
                                contracts=contracts, types=types)
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
    cmd = cmd.upper()
    d = request.args.get('date')
    if not d:
        return jsonify({'error': 'date=YYYY-MM-DD required'}), 400
    try:
        bucket = {'1m': 1, '5m': 5, '15m': 15, '60m': 60}[
            request.args.get('bucket', '15m')]
    except KeyError:
        return jsonify({'error': 'bucket must be 1m|5m|15m|60m'}), 400
    try:
        w = win.resolve(d, preset=request.args.get('preset', 'full'),
                        start=request.args.get('start'),
                        end=request.args.get('end'))
        types = _csv_param('types')
        rows = repo.profile(_db(), cmd, w['start'], w['end'], bucket,
                            contracts=_csv_param('contracts'),
                            types=[t.lower() for t in types] if types else None)
        return _respond({'commodity': cmd, 'date': d, 'window': w,
                         'bucket_minutes': bucket, 'profile': rows,
                         'freshness': _freshness(cmd, [d])}, [d], cmd)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400


@bp.get('/<cmd>/contracts')
def contracts(cmd):
    cmd = cmd.upper()
    d = request.args.get('date')
    if not d:
        return jsonify({'error': 'date=YYYY-MM-DD required'}), 400
    return _respond({'commodity': cmd, 'date': d,
                     'contracts': repo.traded_contracts(_db(), cmd, d),
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
        'types': ['outright', 'leg', 'efs', 'efp', 'block', 'efs_delete', 'other'],
    })
