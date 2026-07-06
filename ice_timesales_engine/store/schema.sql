-- ice_timesales_engine schema.
-- Portable SQL subset: runs on Postgres (Supabase, production) and SQLite
-- (local dev/test). Dialect-sensitive work (minute truncation, upsert batching)
-- is done in Python, not SQL. Postgres-only niceties (BIGSERIAL, partitioning)
-- are deliberately avoided so one DDL file serves both engines. Add monthly
-- range-partitioning on ticks.session_date as a Postgres migration when volume
-- warrants it. NOTE: db.init_schema splits this file on semicolons -- never
-- put a semicolon inside a comment.

-- COLD: every tick, permanent.
CREATE TABLE IF NOT EXISTS ticks (
  commodity      TEXT NOT NULL,
  session_date   TEXT NOT NULL,          -- 'YYYY-MM-DD'
  ice_code       TEXT NOT NULL,          -- 'CTZ6'
  generic_code   TEXT,                   -- 'CTDEC1' | NULL (out-of-universe)
  exchange_time  TEXT NOT NULL,          -- ISO naive ET (lexicographic = chronological)
  price          DOUBLE PRECISION,
  size           DOUBLE PRECISION NOT NULL,
  primary_type   TEXT NOT NULL,
  conditions_raw TEXT,
  seq_num        BIGINT NOT NULL,
  window_preset  TEXT,                   -- night | day | other
  ingested_at    TEXT,
  PRIMARY KEY (commodity, session_date, ice_code, seq_num)
);
CREATE INDEX IF NOT EXISTS ix_ticks_cmd_time   ON ticks (commodity, exchange_time);
CREATE INDEX IF NOT EXISTS ix_ticks_cmd_date_c ON ticks (commodity, session_date, ice_code);

-- HOT: pre-aggregated 1-minute buckets per contract per primary_type.
CREATE TABLE IF NOT EXISTS minute_agg (
  commodity    TEXT NOT NULL,
  session_date TEXT NOT NULL,
  ice_code     TEXT NOT NULL,
  generic_code TEXT,
  minute_ts    TEXT NOT NULL,            -- ISO naive ET truncated to minute
  primary_type TEXT NOT NULL,
  sum_size     DOUBLE PRECISION NOT NULL,
  trade_count  INTEGER NOT NULL,
  PRIMARY KEY (commodity, session_date, ice_code, minute_ts, primary_type)
);
CREATE INDEX IF NOT EXISTS ix_minute_cmd_time ON minute_agg (commodity, minute_ts);
CREATE INDEX IF NOT EXISTS ix_minute_cmd_date ON minute_agg (commodity, session_date, ice_code);

CREATE TABLE IF NOT EXISTS ingest_log (
  commodity     TEXT NOT NULL,
  session_date  TEXT NOT NULL,
  ice_code      TEXT NOT NULL,
  file_name     TEXT NOT NULL,
  file_sha256   TEXT,
  rows_read     INTEGER,
  rows_inserted INTEGER,
  status        TEXT,                    -- ok | skipped | empty | error
  ingested_at   TEXT,
  PRIMARY KEY (commodity, session_date, ice_code, file_name)
);

CREATE TABLE IF NOT EXISTS reconcile_flags (
  commodity     TEXT NOT NULL,
  session_date  TEXT NOT NULL,
  ice_code      TEXT NOT NULL,
  tape_total    DOUBLE PRECISION,
  settle_volume DOUBLE PRECISION,
  delta         DOUBLE PRECISION,
  delta_pct     DOUBLE PRECISION,
  label         TEXT,                    -- expected_gap | suspect_capture | no_settle
  generated_at  TEXT,
  PRIMARY KEY (commodity, session_date, ice_code)
);

-- ARCHIVE: permanent 5-minute buckets, source-labeled. This is the durable
-- multi-source store (Lou ruling 2026-07-05: 5-min is the minimum grain he
-- will ever query). 'ice' rows are rolled up from minute_agg/ticks and are
-- the tape truth going forward. 'bloomberg' rows are the one-shot historical
-- seed (intraday tick pull, ~6.4 months, condition-codes mapped to the same
-- primary_type ladder -- reconciles to ICE at ~0.2 pct). Sources NEVER mix in
-- a query unless explicitly asked -- the PK keeps them separate rows.
-- Window boundaries (21:00 / 07:00 / 14:20 ET) all fall on 5-minute marks,
-- so window_preset is uniform within any bucket.
CREATE TABLE IF NOT EXISTS bar5m (
  source        TEXT NOT NULL,           -- 'ice' | 'bloomberg'
  commodity     TEXT NOT NULL,
  session_date  TEXT NOT NULL,           -- 'YYYY-MM-DD'
  ice_code      TEXT NOT NULL,           -- 'CTZ6'
  generic_code  TEXT,                    -- 'CTDEC1' | NULL
  bucket_ts     TEXT NOT NULL,           -- ISO naive ET floored to 5 min
  window_preset TEXT NOT NULL,           -- night | day | other
  primary_type  TEXT NOT NULL,
  sum_size      DOUBLE PRECISION NOT NULL,
  trade_count   INTEGER NOT NULL,
  PRIMARY KEY (source, commodity, session_date, ice_code, bucket_ts, primary_type)
);
CREATE INDEX IF NOT EXISTS ix_bar5m_cmd_date ON bar5m (commodity, session_date, source);
CREATE INDEX IF NOT EXISTS ix_bar5m_cmd_ts   ON bar5m (commodity, bucket_ts);

CREATE TABLE IF NOT EXISTS block_supplement (
  commodity    TEXT NOT NULL,
  session_date TEXT NOT NULL,
  ice_code     TEXT NOT NULL,
  block_volume DOUBLE PRECISION,
  source       TEXT NOT NULL,            -- 'spreads' | 'tape'
  on_tape      INTEGER,                  -- boolean (0/1 for SQLite compat)
  generated_at TEXT,
  PRIMARY KEY (commodity, session_date, ice_code, source)
);
