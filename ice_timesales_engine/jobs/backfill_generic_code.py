r"""
backfill_generic_code.py -- relabel stored generic_code after the FND roll fix.

WHY
---
Until 2026-09-02 contract_resolver rolled a generic at the 1st of the delivery
month instead of FIRST NOTICE DAY, so for the handful of sessions between FND
and delivery the expiring contract kept slot 1 while the incoming contract sat
in slot 2. ice_to_generic stamps generic_code INTO the archive (ingest/
normalize.py, ingest/rollup.py), so those rows are stored wrong. The resolver
is fixed; this job re-derives the label on the rows already written.

It NEVER touches ice_code, volume, timestamps or any other column. The only
column it writes is generic_code, and only where the corrected resolver
disagrees with what is stored. Rows whose contract has rolled past FND
correctly clear to NULL: the trade is still there, still keyed by ice_code,
it simply no longer occupies a generic slot that day.

THE SIDECAR IS IN SCOPE TOO
---------------------------
data/history/futures_session_volume_history_by_contract_ICE.csv does not copy
the DB column -- ingest/rollup.py::emit_contract_rows RE-DERIVES generic_code,
delivery_year and position from the resolver when it writes a row. So every
row written under an old resolver is stale in three columns, and fixing only
the database would leave this file quietly disagreeing with it.

Measured 2026-09-02: 402 of 2,118 rows (19%) are stale, and most of it is NOT
from the FND roll fix:
  * 359  blank -> populated. Fallout from the KC/CC/SB null-generic bug that
         normalize.py records fixing on 2026-07-31; the DB was backfilled then,
         this sidecar never was.
  *  31  wrong slot -> right slot. The FND roll fix.
  *  12  other field-level drift.
All three are the same defect class -- a re-derived label left behind by a
resolver change -- so one repair covers them.

SCOPE (measured against the live store 2026-09-02, all four commodities)
-----------------------------------------------------------------------
  bar5m        8,064 of   674,969 rows
  minute_agg   8,076 of 1,283,792 rows
  ticks       31,189 of 9,096,706 rows
  total       47,329 of 11,055,467 rows  (0.43%)

The live store is Postgres/Supabase (DATABASE_URL), NOT the local SQLite --
and it carries KC, CC and SB as well as CT. An earlier estimate of 4,200 rows
came from the stale CT-only SQLite seed and was wrong on both counts.

SAFETY
------
  * DRY RUN BY DEFAULT. --apply is required to write anything.
  * One transaction per table; any error rolls that table back whole.
  * Idempotent: it computes the correct label from the resolver, so a second
    run finds nothing to do. Re-runnable after a partial failure.
  * Refuses to run if ANY row cannot be dated by the expiry authority --
    a contract with no sourced FND must be vendored first, never guessed.
  * Writes a full before/after CSV receipt so every change is auditable and
    reversible.
  * Verifies after committing: re-reads the tables and asserts zero remaining
    disagreements and that row counts are unchanged.

USAGE (PowerShell)
------------------
  python -m jobs.backfill_generic_code                  # dry run, prints plan
  python -m jobs.backfill_generic_code --apply          # writes
  python -m jobs.backfill_generic_code --table bar5m    # one table only
"""

import argparse
import csv
import datetime as dt
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config
from ingest.rollup import BYCONTRACT_COLS
from store.db import connect
from contract_resolver import ice_to_generic
from commodity_meta import COMMODITY_MONTHS

TABLES = ('bar5m', 'minute_agg', 'ticks')


def _correct_generic(commodity, session_date, ice_code):
    """The generic slot this (commodity, date, contract) belongs in, per the
    corrected resolver. Raises whatever the resolver raises -- an undatable
    contract must stop the job, never be silently written as NULL."""
    info = ice_to_generic(ice_code, session_date, prefix=commodity,
                          active_months=COMMODITY_MONTHS.get(commodity))
    return info.generic_code if info else None


def plan(db, table):
    """[(commodity, session_date, ice_code, stored, correct, n_rows)] for every
    distinct group whose stored label disagrees with the corrected resolver.

    Grouping by the four key columns keeps this to a few thousand rows of
    Python regardless of table size -- the UPDATE then addresses whole groups,
    not individual rows.
    """
    rows = db.q(f'select commodity, session_date, ice_code, generic_code, '
                f'count(*) from {table} group by 1,2,3,4')
    changes, undatable = [], []
    for commodity, session_date, ice_code, stored, n in rows:
        ds = str(session_date)[:10]
        try:
            correct = _correct_generic(commodity, ds, ice_code)
        except Exception as exc:                     # noqa: BLE001
            undatable.append((commodity, ds, ice_code, str(exc)[:90]))
            continue
        if (stored or None) != (correct or None):
            changes.append((commodity, ds, ice_code, stored, correct, n))
    return changes, undatable


def receipt_path(table):
    stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    d = os.path.join(config.DATA_DIR, 'backfill_receipts')
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f'generic_code_{table}_{stamp}.csv')


def write_receipt(path, changes):
    with open(path, 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(['commodity', 'session_date', 'ice_code',
                    'stored_generic_code', 'corrected_generic_code', 'n_rows'])
        w.writerows(changes)


def apply_changes(db, table, changes):
    """Apply every change inside ONE transaction. The WHERE clause pins the
    stored value too, so a concurrent writer that already fixed a row cannot
    be clobbered, and a re-run is a no-op rather than a double-write."""
    updated = 0
    for commodity, ds, ice_code, stored, correct, _n in changes:
        # Placeholders are %s, ALWAYS. store/db.py::_sql translates %s DOWN to
        # ? for SQLite; Postgres gets the SQL verbatim and psycopg does not
        # recognise ? at all. Writing ? here works on a SQLite copy and dies on
        # the live Postgres with "0 placeholders but 5 parameters were passed"
        # -- which is exactly how the first cut of this job passed its sandbox
        # test and would have failed against production.
        if stored is None:
            where_stored = 'generic_code is null'
            params = (correct, commodity, ds, ice_code)
        else:
            where_stored = 'generic_code = %s'
            params = (correct, commodity, ds, ice_code, stored)
        sql = (f'update {table} set generic_code = %s '
               f'where commodity = %s and session_date = %s and ice_code = %s '
               f'and {where_stored}')
        updated += db.exec(sql, params)
    db.commit()
    return updated


def plan_sidecar():
    """[(row_index, row, corrected)] for sidecar rows whose re-derived columns
    disagree with the corrected resolver, plus any undatable rows.

    Only generic_code / delivery_year / position are re-derived by rollup, so
    only those three may be rewritten here. Volume columns are measurements and
    are never touched."""
    path = config.ROLLUP_BYCONTRACT_CSV
    if not os.path.isfile(path):
        return [], [], []
    with open(path, newline='', encoding='utf-8') as fh:
        rows = list(csv.DictReader(fh))
    changes, undatable = [], []
    for idx, row in enumerate(rows):
        commodity = (row.get('commodity') or '').upper()
        ds = (row.get('date') or '')[:10]
        ice_code = row.get('ice_code') or ''
        if not commodity or not ds or not ice_code:
            continue
        try:
            info = ice_to_generic(ice_code, ds, prefix=commodity,
                                  active_months=COMMODITY_MONTHS.get(commodity))
        except Exception as exc:                     # noqa: BLE001
            undatable.append((commodity, ds, ice_code, str(exc)[:90]))
            continue
        corrected = {
            'generic_code': info.generic_code if info else '',
            'delivery_year': str(info.delivery_year) if info else '',
            'position': str(info.position) if info else '',
        }
        current = {k: (row.get(k) or '') for k in corrected}
        if current != corrected:
            changes.append((idx, dict(row), corrected))
    return rows, changes, undatable


def apply_sidecar(rows, changes):
    """Rewrite the sidecar atomically via a temp file + os.replace, exactly as
    rollup._upsert_csv does, so a crash mid-write cannot leave a partial file
    where a shared artifact used to be."""
    for idx, _row, corrected in changes:
        rows[idx].update(corrected)
    path = config.ROLLUP_BYCONTRACT_CSV
    tmp = path + '.tmp'
    with open(tmp, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=BYCONTRACT_COLS, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)
    return len(changes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true',
                    help='actually write; without it this is a dry run')
    ap.add_argument('--table', choices=TABLES,
                    help='limit to one table (default: all three)')
    args = ap.parse_args()

    tables = (args.table,) if args.table else TABLES
    where = 'POSTGRES (live)' if config.DATABASE_URL.startswith('postgres') else 'SQLITE (local)'
    print(f'target: {where}')
    print(f'mode  : {"APPLY -- will write" if args.apply else "DRY RUN -- no writes"}')
    print()

    # Read-only connection for planning; a writable one only if applying.
    reader = connect(read_only=True)
    plans, total = {}, 0
    for table in tables:
        changes, undatable = plan(reader, table)
        if undatable:
            print(f'REFUSING: {len(undatable)} group(s) in {table} cannot be dated '
                  f'by the expiry authority. Vendor their FND first -- this job '
                  f'will not guess a label.')
            for row in undatable[:10]:
                print('   ', row)
            reader.close()
            return 2
        n = sum(c[5] for c in changes)
        plans[table] = changes
        total += n
        print(f'{table:<12} {len(changes):>5} groups, {n:>7} rows to relabel')
    reader.close()

    # The sidecar CSV re-derives its own labels, so it is stale in the same way
    # and is repaired in the same pass (only when running the full set).
    side_rows, side_changes, side_undatable = ([], [], [])
    if not args.table:
        side_rows, side_changes, side_undatable = plan_sidecar()
        if side_undatable:
            print(f'REFUSING: {len(side_undatable)} sidecar row(s) cannot be dated.')
            for row in side_undatable[:10]:
                print('   ', row)
            return 2
        print(f'{"sidecar csv":<12} {len(side_changes):>5} rows to relabel')

    if not total and not side_changes:
        print('\nnothing to do -- every stored generic_code already agrees '
              'with the corrected resolver.')
        return 0

    print(f'\ntotal db rows to relabel: {total}'
          f'{f", sidecar rows: {len(side_changes)}" if side_changes else ""}')

    if not args.apply:
        print('\ndry run only. Re-run with --apply to write.')
        for table, changes in plans.items():
            for c in sorted(changes, key=lambda x: -x[5])[:5]:
                print(f'  {table}: {c[0]} {c[1]} {c[2]} {c[3]} -> {c[4]} ({c[5]} rows)')
        return 0

    if side_changes:
        receipt = receipt_path('sidecar_bycontract')
        with open(receipt, 'w', newline='', encoding='utf-8') as fh:
            w = csv.writer(fh)
            w.writerow(['date', 'commodity', 'ice_code', 'stored_generic_code',
                        'corrected_generic_code', 'stored_position',
                        'corrected_position'])
            for _idx, row, corrected in side_changes:
                w.writerow([row.get('date'), row.get('commodity'), row.get('ice_code'),
                            row.get('generic_code'), corrected['generic_code'],
                            row.get('position'), corrected['position']])
        n = apply_sidecar(side_rows, side_changes)
        print(f'\nsidecar: receipt -> {receipt}')
        print(f'sidecar: updated {n} rows in {config.ROLLUP_BYCONTRACT_CSV}')

    db = connect()
    try:
        for table, changes in plans.items():
            if not changes:
                continue
            path = receipt_path(table)
            write_receipt(path, changes)
            print(f'\n{table}: receipt -> {path}')
            before = db.q(f'select count(*) from {table}')[0][0]
            updated = apply_changes(db, table, changes)
            after = db.q(f'select count(*) from {table}')[0][0]
            if before != after:
                raise RuntimeError(
                    f'{table} row count changed {before} -> {after}; this job '
                    f'must only ever UPDATE a column, never insert or delete')
            print(f'{table}: updated {updated} rows (count unchanged at {after})')
    finally:
        db.close()

    # VERIFY THE EFFECT, not the status: re-read and confirm zero disagreements.
    print('\nverifying...')
    reader = connect(read_only=True)
    ok = True
    for table in tables:
        changes, undatable = plan(reader, table)
        remaining = sum(c[5] for c in changes)
        print(f'  {table:<12} remaining disagreements: {remaining}')
        if remaining or undatable:
            ok = False
    # The check above is SELF-REFERENTIAL: it re-runs the same resolver that
    # produced the changes, so it only proves "the DB matches what this code
    # believes". If expiry_source returned garbage for a whole commodity it
    # would still pass. So also verify the RESULT against a property the
    # resolver never consulted.
    ok = _verify_slot_ordering(reader) and ok

    if not args.table:
        _rows, remaining_side, _u = plan_sidecar()
        print(f'  sidecar csv  remaining disagreements: {len(remaining_side)}')
        if remaining_side:
            ok = False
    reader.close()
    print('\nVERIFIED CLEAN' if ok else '\nVERIFICATION FAILED -- investigate before trusting')
    return 0 if ok else 1


def _verify_slot_ordering(db):
    """Independent check: where both slots of a month word are present on a
    date, slot 1 must be the EARLIER-expiring contract.

    This asks the resolver nothing. It reads the labels actually stored and
    tests them against the definition of a generic slot, so a systematically
    wrong FND surfaces as an ordering violation instead of passing a check
    that agrees with itself by construction.

    ICE codes carry a single year digit ('CTN6'), so each is lifted to the
    nearest full year at or after the session year before comparing. That is
    enough to order two contracts sharing a month word, which is all a
    slot-1-vs-slot-2 comparison requires.
    """
    month_of = {'F': 1, 'G': 2, 'H': 3, 'J': 4, 'K': 5, 'M': 6,
                'N': 7, 'Q': 8, 'U': 9, 'V': 10, 'X': 11, 'Z': 12}
    rows = db.q('select commodity, session_date, generic_code, ice_code '
                'from bar5m where generic_code is not null group by 1,2,3,4')
    by_key = {}
    for commodity, session_date, generic, ice in rows:
        ds = str(session_date)[:10]
        if not generic or not generic[-1].isdigit():
            continue
        by_key.setdefault((commodity, ds, generic[:-1]), {})[int(generic[-1])] = ice

    bad = 0
    for (commodity, ds, word), slots in by_key.items():
        if 1 not in slots or 2 not in slots:
            continue
        a, b = slots[1], slots[2]
        try:
            ya, yb = int(a[-1]), int(b[-1])
            ma, mb = month_of[a[-2]], month_of[b[-2]]
        except (ValueError, KeyError, IndexError):
            continue
        year = int(ds[:4])
        fa = year + ((ya - year) % 10)
        fb = year + ((yb - year) % 10)
        if (fa, ma) >= (fb, mb):
            bad += 1
            if bad <= 5:
                print(f'  ORDERING VIOLATION {commodity} {ds} {word}: '
                      f'slot1={a} not earlier than slot2={b}')
    print(f'  slot ordering (resolver-independent): '
          f'{"OK" if not bad else str(bad) + " violations"}')
    return bad == 0


if __name__ == '__main__':
    sys.exit(main())
