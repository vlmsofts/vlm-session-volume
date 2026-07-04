"""
loader.py -- idempotent upsert of NormTicks + ingest_log bookkeeping.

Idempotency key: (commodity, session_date, ice_code, seq_num) -- the table's
PRIMARY KEY -- with INSERT ... ON CONFLICT DO NOTHING, so re-ingesting a day
inserts 0 new rows. Whole-file skip via sha256 in ingest_log.
"""

from dataclasses import dataclass
from typing import Iterable, Optional

from store.db import Db, now_iso

_INSERT_TICK = """
INSERT INTO ticks (commodity, session_date, ice_code, generic_code,
                   exchange_time, price, size, primary_type, conditions_raw,
                   seq_num, window_preset, ingested_at)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (commodity, session_date, ice_code, seq_num) DO NOTHING
"""


@dataclass
class IngestMeta:
    commodity: str
    session_date: str
    ice_code: str
    file_name: str
    file_sha256: str
    rows_read: int
    rows_inserted: int
    status: str          # ok | skipped | empty | error


def upsert_ticks(db: Db, rows: Iterable) -> int:
    """Insert NormTicks; conflicts are silently skipped. Returns inserted count
    (delta of table count -- executemany rowcount is unreliable for
    ON CONFLICT DO NOTHING across drivers)."""
    rows = list(rows)
    if not rows:
        return 0
    key = (rows[0].commodity, rows[0].session_date)
    before = db.q('SELECT COUNT(*) FROM ticks WHERE commodity=%s AND session_date=%s', key)[0][0]
    db.execmany(_INSERT_TICK, [
        (t.commodity, t.session_date, t.ice_code, t.generic_code,
         t.exchange_time, t.price, t.size, t.primary_type, t.conditions_raw,
         t.seq_num, t.window_preset, now_iso())
        for t in rows
    ])
    after = db.q('SELECT COUNT(*) FROM ticks WHERE commodity=%s AND session_date=%s', key)[0][0]
    db.commit()
    return after - before


def record_ingest(db: Db, meta: IngestMeta) -> None:
    db.exec("""
        INSERT INTO ingest_log (commodity, session_date, ice_code, file_name,
                                file_sha256, rows_read, rows_inserted, status, ingested_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (commodity, session_date, ice_code, file_name) DO UPDATE SET
          file_sha256=excluded.file_sha256, rows_read=excluded.rows_read,
          rows_inserted=excluded.rows_inserted, status=excluded.status,
          ingested_at=excluded.ingested_at
    """, (meta.commodity, meta.session_date, meta.ice_code, meta.file_name,
          meta.file_sha256, meta.rows_read, meta.rows_inserted, meta.status,
          now_iso()))
    db.commit()


def already_ingested(db: Db, commodity: str, session_date: str,
                     ice_code: str, sha256: str) -> bool:
    """True when this exact file content was already ingested ok."""
    rows = db.q("""
        SELECT 1 FROM ingest_log
        WHERE commodity=%s AND session_date=%s AND ice_code=%s
          AND file_sha256=%s AND status='ok'
    """, (commodity, session_date, ice_code, sha256))
    return bool(rows)
