"""
cotton_futures_volume_history_blpapi.py
=======================================
Pulls DEEP DAILY HISTORY of futures volume (+ OI, price, OHLC, EFP/EFS) for the
8-slot CT universe from Bloomberg Terminal via blpapi, in ONE HistoricalDataRequest.

This is the SEED/BACKFILL puller for the vlm_session_volume engine's permanent
FULL-SESSION history. It must run on the machine where the Bloomberg Terminal is
running (localhost:8194). It is read-only against Bloomberg and writes a single
clean long-format CSV; it touches nothing else.

  • FULL-session daily volume per generic → this is what we seed (deep, years back).
  • The night/day intraday split is NOT available here (Bloomberg gives one number
    per day); that stays forward-only from the live sidecar. Full-session + the
    multi-year December comparison come from THIS pull.

Generics (continuous, roll-handled by Bloomberg): the front & 2nd of each tracked
futures month — Mar/May/Jul/Dec. October & August are intentionally excluded.

Output: cotton_futures_volume_history.csv  (one row per date × generic)
  columns: date, generic, ticker, volume, open_int, px_last, px_high, px_low,
           px_open, efp_volume, efs_volume

Usage:
  python cotton_futures_volume_history_blpapi.py
  python cotton_futures_volume_history_blpapi.py --start 20050101 --end 20260617
  python cotton_futures_volume_history_blpapi.py --output "C:\\Data\\ct_fut_vol_history.csv"
"""

import blpapi
import csv
import datetime
import argparse
import sys
from pathlib import Path

# ── Configuration ─────────────────────────────────────────────────────────────
DEFAULT_OUTPUT = Path(__file__).parent / "cotton_futures_volume_history.csv"
TIMEOUT_MS     = 30000   # 30s per event wait (history responses can be large)

# 8-slot universe — 1st & 2nd generic of each tracked futures month.
# (No CTOCT*, no August — hard-excluded.)
SECURITIES = [
    "CTMAR1 Comdty", "CTMAR2 Comdty",
    "CTMAY1 Comdty", "CTMAY2 Comdty",
    "CTJUL1 Comdty", "CTJUL2 Comdty",
    "CTDEC1 Comdty", "CTDEC2 Comdty",
]

# Time-series fields valid in a HistoricalDataRequest. Any field Bloomberg can't
# serve for a given date simply comes back blank (handled by safe_*); it never
# kills the request.
HIST_FIELDS = [
    "PX_VOLUME",                    # daily full-session futures volume  ← the metric
    "OPEN_INT",                     # open interest (reaches back furthest)
    "PX_LAST",                      # settle / last
    "PX_HIGH", "PX_LOW", "PX_OPEN", # OHLC context (matches oi_data schema)
    "EXCHANGE_FOR_PHYSICAL_VOLUME", # EFP (best-effort; blank if not served)
    "EXCHANGE_FOR_SWAP_VOLUME",     # EFS (best-effort)
]

CSV_HEADERS = [
    "date", "generic", "ticker",
    "volume", "open_int", "px_last", "px_high", "px_low", "px_open",
    "efp_volume", "efs_volume",
]


# ── Bloomberg helpers (same connection pattern as cotton_options_blpapi) ────────
def start_session():
    opts = blpapi.SessionOptions()
    opts.setServerHost("localhost")
    opts.setServerPort(8194)
    session = blpapi.Session(opts)
    if not session.start():
        sys.exit("ERROR: Could not connect to Bloomberg. Is the terminal running?")
    if not session.openService("//blp/refdata"):
        sys.exit("ERROR: Could not open //blp/refdata service.")
    return session


def send_hist_request(session, securities, fields, start_date, end_date):
    """Send ONE HistoricalDataRequest for all securities/fields; return list of
    (security, [per-date fieldData elements])."""
    svc = session.getService("//blp/refdata")
    req = svc.createRequest("HistoricalDataRequest")
    for s in securities:
        req.getElement("securities").appendValue(s)
    for f in fields:
        req.getElement("fields").appendValue(f)
    req.set("startDate", start_date)                 # YYYYMMDD
    req.set("endDate", end_date)                      # YYYYMMDD
    req.set("periodicitySelection", "DAILY")
    req.set("periodicityAdjustment", "ACTUAL")
    req.set("nonTradingDayFillOption", "ACTIVE_DAYS_ONLY")  # real trading days only
    session.sendRequest(req)

    out = []  # (security, fieldDataArray)
    done = False
    while not done:
        ev = session.nextEvent(TIMEOUT_MS)
        for msg in ev:
            if not msg.hasElement("securityData"):
                continue
            sd = msg.getElement("securityData")
            sec = sd.getElementAsString("security")
            if sd.hasElement("securityError"):
                err = sd.getElement("securityError").getElementAsString("message")
                print(f"  ! securityError {sec}: {err}")
                continue
            fda = sd.getElement("fieldData")
            rows = [fda.getValue(i) for i in range(fda.numValues())]
            out.append((sec, rows))
            print(f"  {sec}: {len(rows)} daily rows")
        if ev.eventType() == blpapi.Event.RESPONSE:
            done = True
    return out


def safe_float(el, field):
    try:
        return el.getElementAsFloat(field)
    except Exception:
        return ""

def fmt_date(el):
    try:
        d = el.getElementAsDatetime("date")
        return f"{d.year}-{d.month:02d}-{d.day:02d}"
    except Exception:
        return ""


# ── Build rows ─────────────────────────────────────────────────────────────────
def build_rows(hist):
    rows = []
    for sec, daily in hist:
        generic = sec.replace(" Comdty", "").strip()
        for el in daily:
            d = fmt_date(el)
            if not d:
                continue
            rows.append({
                "date":       d,
                "generic":    generic,
                "ticker":     sec,
                "volume":     safe_float(el, "PX_VOLUME"),
                "open_int":   safe_float(el, "OPEN_INT"),
                "px_last":    safe_float(el, "PX_LAST"),
                "px_high":    safe_float(el, "PX_HIGH"),
                "px_low":     safe_float(el, "PX_LOW"),
                "px_open":    safe_float(el, "PX_OPEN"),
                "efp_volume": safe_float(el, "EXCHANGE_FOR_PHYSICAL_VOLUME"),
                "efs_volume": safe_float(el, "EXCHANGE_FOR_SWAP_VOLUME"),
            })
    rows.sort(key=lambda r: (r["date"], r["generic"]))
    return rows


def write_csv(rows, output_path):
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  Output: {len(rows)} rows written to {path}")


def merge_csv(new_rows, output_path):
    """Upsert new_rows into the existing CSV by (date, generic).
    Keeps all existing rows for dates NOT covered by new_rows; replaces rows
    for dates that are covered (idempotent trailing-window refresh).
    Writes the merged result back sorted by (date, generic).
    """
    path = Path(output_path)
    existing = []
    if path.is_file():
        with open(path, newline="", encoding="utf-8") as f:
            existing = list(csv.DictReader(f))

    new_keys = {(r["date"], r["generic"]) for r in new_rows}
    kept = [r for r in existing if (r["date"], r["generic"]) not in new_keys]
    merged = kept + new_rows
    merged.sort(key=lambda r: (r["date"], r["generic"]))

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writeheader()
        writer.writerows(merged)
    print(f"  Merged: {len(new_rows)} new/updated rows, {len(kept)} kept, "
          f"{len(merged)} total → {path}")


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="Pull daily CT futures-volume history from Bloomberg")
    p.add_argument("--start", default="20050101", help="Start date YYYYMMDD (default 20050101)")
    p.add_argument("--end",   default=datetime.date.today().strftime("%Y%m%d"),
                   help="End date YYYYMMDD (default today)")
    p.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output CSV path")
    p.add_argument("--merge", action="store_true",
                   help="Upsert pulled rows into existing CSV (safe for trailing-window refresh); "
                        "without --merge the output file is fully replaced.")
    args = p.parse_args()

    print(f"\n{'='*60}")
    print(f"  CT Futures Volume History — {datetime.datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"  Range {args.start} → {args.end}  |  {len(SECURITIES)} generics"
          + ("  [--merge]" if args.merge else "  [full replace]"))
    print(f"{'='*60}")

    session = start_session()
    try:
        hist = send_hist_request(session, SECURITIES, HIST_FIELDS, args.start, args.end)
        rows = build_rows(hist)
        if args.merge:
            merge_csv(rows, args.output)
        else:
            write_csv(rows, args.output)
        if rows:
            dates = sorted({r["date"] for r in rows})
            print(f"\n  Coverage: {dates[0]} → {dates[-1]}  ({len(dates)} trading days)")
            per = {}
            for r in rows:
                if r["volume"] not in ("", 0, 0.0):
                    per[r["generic"]] = per.get(r["generic"], 0) + 1
            print("  Nonzero-volume days per generic:")
            for g in sorted(per):
                print(f"    {g:<8} {per[g]:>6}")
        print(f"\n  Done.")
    finally:
        session.stop()


if __name__ == "__main__":
    main()
