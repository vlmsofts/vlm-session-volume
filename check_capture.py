#!/usr/bin/env python3
"""
check_capture.py -- Capture-completeness monitor for the futures sidecar.

PART OF: VLM_Session_Volume_Project  (runs out of this folder).

WHY THIS EXISTS
  The engine (futures_session_volume.py) silently reports "no data" if a session
  boundary was never captured (e.g. price_tape.py wasn't running, or the RTD
  workbook was closed at 14:20). This monitor turns that silent gap into a LOUD,
  dated failure -- so an incomplete capture can never pass unnoticed.

WHAT IT CHECKS
  For a trading date, the sidecar written by Options_flow_analyzer/price_tape.py:
      <OPTIONS_FLOW_DATA>/<date>/ct_futures_volume.csv
  must contain every boundary that is DUE by now:
      open  (~21:00 prev evening)   0700 (07:00 ET)   1420 (14:20 ET)
  "Due" is decided by the clock (with a grace margin) unless --eod forces all
  three. Each due boundary must carry at least one of the 8-slot universe
  contracts (confirms the snapshot actually wrote the board, not an empty file).

  Holiday (config.CT_CLOSED_DATES) or weekend  -> nothing expected -> clean exit 0.

EXIT CODES
  0  OK (all due boundaries present)  OR  market closed (nothing expected)
  2  INCOMPLETE capture  -> alerts fire, message includes the absolute path
  3  CONFIG/USAGE error

ALERTS (optional, all off unless env vars are set; console is always used)
  WhatsApp/SMS via Twilio (no extra package -- uses the REST API directly):
      set  TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN
           TWILIO_FROM  = whatsapp:+<twilio sender>   (or +<number> for SMS)
           TWILIO_TO    = whatsapp:+<your number>     (or +<number> for SMS)
  Email via SMTP:
      set  SMTP_HOST, SMTP_USER, SMTP_PASS, ALERT_TO  (SMTP_PORT optional, default 587)

USAGE
  python check_capture.py                 # check today, boundaries due by now
  python check_capture.py --eod           # require all three (run ~14:40 ET)
  python check_capture.py --date 2026-06-17
"""
import argparse
import csv
import os
import sys
from datetime import datetime

# Self-contained: reuse the project's own config + symbology (same folder).
import config
from contract_resolver import build_capture_universe

# Optional source override (handy for testing; unset in production -> uses config).
SOURCE = os.environ.get('SESSION_VOL_SOURCE') or config.OPTIONS_FLOW_DATA

GRACE_MIN = 5   # minutes after a boundary time before we consider it "due"


def _sidecar_path(date_str, commodity):
    return os.path.join(SOURCE, date_str, f'{commodity.lower()}_futures_volume.csv')


def _is_closed(date_str):
    d = datetime.strptime(date_str, '%Y-%m-%d').date()
    return d.weekday() >= 5 or date_str in config.CT_CLOSED_DATES


def _due_boundaries(date_str, force_eod):
    """Which boundaries should already exist for this date by now."""
    if force_eod:
        return ['open', '0700', '1420']
    now = datetime.now()                      # machine clock = ET (project convention)
    today = now.strftime('%Y-%m-%d')
    if date_str < today:                      # past session -> all three are over
        return ['open', '0700', '1420']
    if date_str > today:                      # future -> nothing yet
        return []
    due = ['open']                            # session opened ~21:00 the prior evening
    mins = now.hour * 60 + now.minute
    if mins >= 7 * 60 + GRACE_MIN:
        due.append('0700')
    if mins >= 14 * 60 + 20 + GRACE_MIN:
        due.append('1420')
    return due


def _boundaries_present(path, universe_ice):
    """Return {boundary: bool} -- True if that boundary has >=1 universe contract."""
    seen = {}
    with open(path, newline='', encoding='utf-8') as fh:
        for row in csv.DictReader(fh):
            b = (row.get('boundary') or '').strip()
            c = (row.get('contract') or '').strip().upper()
            if b and c in universe_ice:
                seen[b] = True
    return seen


def _alert(subject, body):
    """Fire whatever alert channels are configured. Console is always printed."""
    print(body, file=sys.stderr)
    sent = []
    # WhatsApp/SMS via Twilio REST API (stdlib only -- no twilio package needed).
    # WhatsApp: set TWILIO_FROM/TWILIO_TO with a "whatsapp:" prefix.
    # SMS:      set them as plain "+<number>".
    sid = os.environ.get('TWILIO_ACCOUNT_SID'); tok = os.environ.get('TWILIO_AUTH_TOKEN')
    t_from = os.environ.get('TWILIO_FROM'); t_to = os.environ.get('TWILIO_TO')
    if sid and tok and t_from and t_to:
        try:
            import base64, urllib.parse, urllib.request
            api = f'https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json'
            data = urllib.parse.urlencode(
                {'From': t_from, 'To': t_to, 'Body': f'{subject}\n{body}'}).encode()
            req = urllib.request.Request(api, data=data)
            req.add_header('Authorization',
                           'Basic ' + base64.b64encode(f'{sid}:{tok}'.encode()).decode())
            urllib.request.urlopen(req, timeout=20).read()
            sent.append('twilio')
        except Exception as exc:
            print(f'WARN: Twilio alert failed: {exc}', file=sys.stderr)
    # Email (SMTP)
    host = os.environ.get('SMTP_HOST'); user = os.environ.get('SMTP_USER')
    pw = os.environ.get('SMTP_PASS'); to = os.environ.get('ALERT_TO')
    if host and user and pw and to:
        try:
            import smtplib
            from email.message import EmailMessage
            m = EmailMessage(); m['From'] = user; m['To'] = to
            m['Subject'] = subject; m.set_content(body)
            with smtplib.SMTP(host, int(os.environ.get('SMTP_PORT', '587'))) as s:
                s.starttls(); s.login(user, pw); s.send_message(m)
            sent.append('email')
        except Exception as exc:
            print(f'WARN: email alert failed: {exc}', file=sys.stderr)
    if sent:
        print(f'[alert sent via: {", ".join(sent)}]', file=sys.stderr)
    elif not (sid and tok and t_from and t_to) and not (host and user and pw and to):
        print('[no alert channel configured -- console only. '
              'Set TWILIO_* or SMTP_* to enable.]', file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description='Futures sidecar capture-completeness monitor.')
    ap.add_argument('--commodity', default='CT')
    ap.add_argument('--date', default=None, help='YYYY-MM-DD (default: today)')
    ap.add_argument('--eod', action='store_true', help='Require all three boundaries.')
    args = ap.parse_args()

    comm = args.commodity
    date_str = args.date or datetime.now().strftime('%Y-%m-%d')
    path = _sidecar_path(date_str, comm)
    proj = config.REPO_ROOT

    header = (f'CAPTURE CHECK -- {comm} -- {date_str}\n'
              f'  project : {proj}\n'
              f'  sidecar : {path}')

    # Market-closed days: nothing to capture.
    if _is_closed(date_str):
        print(header)
        print(f'  result  : OK -- market closed {date_str}, no capture expected.')
        return 0

    due = _due_boundaries(date_str, args.eod)
    if not due:
        print(header)
        print(f'  result  : OK -- no boundaries due yet for {date_str}.')
        return 0

    if not os.path.isfile(path):
        body = (f'{header}\n  DUE     : {", ".join(due)}\n'
                f'  result  : FAIL -- sidecar file MISSING. Is price_tape.py running '
                f'with the RTD workbook open?\n  FIX     : {path}')
        _alert(f'[CAPTURE FAIL] {comm} {date_str} -- sidecar missing', body)
        return 2

    universe = build_capture_universe(date_str)
    universe_ice = {info.ice_code for info in universe.values()}
    present = _boundaries_present(path, universe_ice)
    missing = [b for b in due if not present.get(b)]

    print(header)
    print(f'  due     : {", ".join(due)}')
    print(f'  present : {", ".join(b for b in config.FUT_BOUNDARIES if present.get(b)) or "(none)"}')

    if missing:
        body = (f'{header}\n  DUE     : {", ".join(due)}\n'
                f'  MISSING : {", ".join(missing)}\n'
                f'  result  : FAIL -- boundary(s) not captured. Likely price_tape.py '
                f'stopped or the RTD workbook was closed at that time.')
        _alert(f'[CAPTURE FAIL] {comm} {date_str} -- missing {",".join(missing)}', body)
        return 2

    print(f'  result  : OK -- all due boundaries captured.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:
        # Loud-fail with the offending detail (project hard rule).
        print(f'CAPTURE CHECK ERROR: {exc}', file=sys.stderr)
        sys.exit(3)
