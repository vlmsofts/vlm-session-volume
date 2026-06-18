# Futures Volume — LOCKED SCOPE + price-tape change (investigate-only)

**Status: planning. NO code until the Part 2 investigation clears collision/blast-radius to 100%.**

---

## PART 1 — LOCKED SCOPE (owner-confirmed)

**Product:** futures-contract volume, session-window comparison, for CT. (Replaces the options-volume
view — see "Disposition" below.)

**Three windows, per session, per futures contract:**
- **Night:** 21:00 (prev) → 07:00 ET
- **Day:** 07:00 → 14:20 ET
- **Full:** 21:00 → 14:20 ET  (= night + day)

**Four comparisons, all tracked over time vs trailing 5 / 10 / 20 / 30 / 60 sessions:**
1. Night vs its own history.
2. Day vs its own history.
3. **Night vs Day against each other, over time** — store per session: `night_share = night/full`
   and `night_day_ratio = night/day`; compare each to its trailing history (flag when the overnight
   tilt is unusually high/low).
4. Full daily vs its own history.

**Universe / rules (unchanged):** futures months only — **DEC/MAR/MAY/JUL × 1st & 2nd generic**
(8 slots); October & August excluded; serials are an options concept and don't exist on the futures
tape, so D2 ("drop serials") is automatic. Symbology on every row: `generic_code` (CTDEC1) +
`ice_code` (CTZ6) + `month_code` + `month_name` + 4-digit `delivery_year`. Permanent, append-only
history. Loud-fail with the offending file path.

**History reach:**
- Night / Day / Night-vs-Day split: **forward-only**, starts today (accepted).
- Full daily total per contract: available historically — `rtd_snap.json` (EOD) and VLM API
  `oi_data.csv` `PX_VOLUME` per Bloomberg generic, **back to 2008** → December-over-years on the full
  metric works immediately.

**Disposition of the existing options `session_volume.py`:** stand down its two scheduled jobs
(stop the notifications — it reports options volume, which is not this product). Keep the code as the
reusable engine (windows / RVOL / history / holiday / loud-fail machinery) to repurpose for futures.
Confirm with owner before any deletion.

---

## PART 2 — The ONE change required, and its investigation (do this BEFORE code)

To get night/day futures volume we must record the volume number the RTD feed already provides each
poll. `price_tape.py` currently writes `last,bid,offer,mid,settle,market_state` and **discards
volume**. The change: **append a `volume` column (and consider `oi`) to the price tape.**

This is the only collision-sensitive step (it edits an existing, tested producer). Investigate and
report — **no code yet:**

### 2.1 Map EVERY reader of the price-tape family
Not just the daily tape. The change flows into three files:
- `data/<date>/ct_price_tape.csv` (daily)
- `data/history/ct_price_tape_history.csv` (rolling, forever)
- `data/weekly-backup/.../ct_price_tape_<weekday>.csv` (weekly copies)
For each, list every consumer and confirm it reads **by column name** (or pandas) and does **not**
assert a fixed column count or read positionally. (Known so far: `gex_calculator.py` reads the daily
tape via `pd.read_csv` by name → an appended trailing column is invisible to it. CONFIRM there are no
others, especially any reader of `ct_price_tape_history.csv`.)

### 2.2 Confirm the producer's tests stay green
`test_price_tape.py` compares the header to the script's own `_FIELDS` list (auto-updates if we add a
field) and checks row counts, not column counts. Confirm by running the **full** `pytest` suite after
the (proposed) change and showing it green.

### 2.3 Decide capture cadence (design choice — flag, don't code)
The price tape writes only on a price move (≥0.01 tick), so volume would be sampled at those ticks.
For accurate window boundaries we want the volume reading nearest 07:00 and 14:20. Options to assess:
(a) accept tick-sampled volume; (b) also write a row when volume changes; (c) force a row at/just
before each boundary. Recommend one with pros/cons.

### 2.4 Blast-radius confirmations
- Does writing a new column change file sizes / backup logic / the 10-day cleanup in any way? (Cleanup
  deletes the whole tape file regardless of columns — no effect, but confirm.)
- Does `ct_price_tape_history.csv` have any downstream consumer that would see the new column? (This is
  the most likely overlooked reader — check it explicitly.)
- Will any GEX intraday-validation result change? (It reads the tape by name; appended column should
  be inert — prove gex output byte-identical before/after.)

### 2.5 Report format
Return: (A) the full reader map for all three price-tape files with each reader's access style; (B) a
yes/no on whether any reader is affected; (C) full pytest result; (D) gex output unchanged proof; (E)
a cadence recommendation. **Then stop for approval.**

---

## PART 3 — Sign-off gate (all ✓ before code)
- [ ] Part 2 reader map shows no consumer of any price-tape file is affected by an appended column.
- [ ] Full `pytest` green with the column added.
- [ ] GEX output proven unchanged.
- [ ] Capture cadence chosen.
- [ ] Options `session_volume.py` jobs stood down (or decision confirmed).
- [ ] Symbology + 8-slot universe + Oct/Aug exclusion carried into the futures version.
