"""
windows.py -- resolve preset/custom time windows to concrete naive-ET bounds.

Presets (per session_date, from reused config -- [start, end) half-open):
  night = [prev_day 21:00, session_date 07:00)
  day   = [session_date 07:00, session_date 14:20)
  full  = [prev_day 21:00, session_date 14:20)

Custom: start/end given as 'HH:MM' (ET). An HH:MM >= 21:00 start is anchored
to the PREVIOUS calendar day (overnight session start); anything else is on
the session date. Cross-midnight custom windows (start clock-time > end
clock-time) anchor start to the previous day.
"""

from datetime import date, datetime, time, timedelta

from ingest.normalize import window_bounds

PRESETS = ('night', 'day', 'full')


def _fmt(dt: datetime) -> str:
    return dt.strftime('%Y-%m-%dT%H:%M:%S')


def resolve_preset(preset: str, session_date: str) -> tuple:
    b = window_bounds(date.fromisoformat(session_date))
    if preset == 'night':
        return _fmt(b['night_start']), _fmt(b['night_end'])
    if preset == 'day':
        return _fmt(b['day_start']), _fmt(b['day_end'])
    if preset == 'full':
        return _fmt(b['night_start']), _fmt(b['day_end'])
    raise ValueError(f'Unknown preset {preset!r}; expected one of {PRESETS}')


def _parse_hhmm(s: str) -> time:
    hh, mm = s.strip().split(':')
    return time(int(hh), int(mm))


def resolve_custom(start_hhmm: str, end_hhmm: str, session_date: str) -> tuple:
    """Custom [start, end) for one session date. 21:00+ start or a start
    clock-time later than the end anchors start to the previous calendar day."""
    sd = date.fromisoformat(session_date)
    t_start, t_end = _parse_hhmm(start_hhmm), _parse_hhmm(end_hhmm)
    start_day = sd - timedelta(days=1) if (t_start >= time(21, 0) or t_start > t_end) else sd
    start_dt = datetime.combine(start_day, t_start)
    end_dt = datetime.combine(sd, t_end)
    if end_dt <= start_dt:
        raise ValueError(f'Empty window: {start_hhmm}..{end_hhmm} on {session_date}')
    return _fmt(start_dt), _fmt(end_dt)


def resolve(session_date: str, preset: str = None,
            start: str = None, end: str = None) -> dict:
    """-> {'preset', 'start', 'end'} concrete bounds for one session date."""
    if preset:
        s, e = resolve_preset(preset, session_date)
        return {'preset': preset, 'start': s, 'end': e}
    if start and end:
        s, e = resolve_custom(start, end, session_date)
        return {'preset': None, 'start': s, 'end': e}
    raise ValueError('Provide preset=night|day|full OR start+end (HH:MM ET)')
