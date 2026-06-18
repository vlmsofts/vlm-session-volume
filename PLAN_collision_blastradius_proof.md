# Plan: Collision / Blast-Radius / No-Consumer-Change PROOF — pre-code gate

**Owner directive:** no code decisions until we are 100% certain on (1) collision, (2) blast radius,
and (3) that **no other consumer of the data sees any change in their collection.** This document is
the evidence, plus the decisions that must be locked first. **No code until every box below is
signed off.**

---

## PART 1 — Evidence (repo-wide sweep, not assertion)

### 1.1 Who scans the data directories? (every glob/scandir/listdir/walk in the repo)
| Location | Target | Could it see our files? |
|---|---|---|
| `config/settings.py:42` | `Desktop/COT/dm_export_*.csv` | No — different folder (COT). |
| `gex_chart_data.py:248` | `DATA_BASE/*/gex_output.json` | No — exact filename `gex_output.json`. |
| `pipeline/synopsis_generator.py:124` | `DATA_BASE/*/gex_output.json` | No — exact filename. |
| `pipeline/flow_aggregator.py:106` | `processed/.../*.json` | No — `processed/` tree, not `data/<date>/`. |
| `pipeline/snapshot_scraper.py:515` | `*.png` | No. |
| `eod_options_brief/run.py:80`, `friday_cot_brief/run.py:245` | `*.png` | No. |
| `options_tape.py:269` | `os.scandir(DATA_BASE)` → **cleanup** | See 1.2 — only deletes the tape file. |
| `overnight_volume.py:41`, `session_volume.py:206` | our own date-folder glob | Ours. |

**No consumer globs `data/<date>/` for anything but `gex_output.json`, and nothing globs
`data/history/` except our own scripts.** Our outputs (`session_volume.txt/.json`,
`session_volume_by_contract.csv`, new columns in `session_volume_history.csv`) are therefore
invisible to every other process.

### 1.2 The 10-day cleanup cannot touch our files (read the code)
`options_tape.py:264-284` deletes **one specific path per old date folder**:
`os.remove(_options_tape_path(entry.name, commodity))` = `data/<date>/ct_options_tape.csv`.
It does **not** `rmtree` the folder and does **not** wildcard-delete. Our per-date files in those
same folders are untouched. ✓

### 1.3 Who reads the options tape? (our read can't disturb anyone)
Only `options_tape.py` (the writer) and our two scripts reference `*_options_tape.csv`. No dashboard,
GEX, synopsis, or brief reads the raw options tape. Our access is **read-only, single sequential
pass, no lock touched** → it cannot change what any other consumer collects from the tape. ✓

### 1.4 settings.py additions are invisible to everyone else
- The new names (`SESSION_VOL_DAY_SPLIT`, `SESSION_VOL_DAY_END`, `SESSION_VOL_LOOKBACKS`,
  `CT_HOLIDAYS`) are read **only** by `session_volume.py` and `tests/test_session_volume.py`.
- **No** `from config.settings import *`, **no** `vars(settings)`/`dir(settings)`, **no**
  serialization of the settings namespace anywhere in the repo — so no other module enumerates or
  re-emits settings. Adding module-level constants only binds new names at import; existing
  consumers are byte-for-byte unaffected. ✓
- There is **no** `test_settings.py` / settings-schema snapshot test that adding names could break.

### 1.5 History file ownership
`session_volume_history.csv` and the proposed `session_volume_by_contract.csv` are read by
**this feature only**. Adding columns to the former is forward/backward compatible: the reader uses
`csv.DictReader` + `.get()`, and the idempotent rewrite uses `DictWriter(extrasaction='ignore')`
(drops unknown old keys, blank-fills new keys). One caveat to enforce in code (not yet done): the
universe reader must treat both `''` **and** a missing column as "no data."

### 1.6 The empirical proof still owed (the only thing left for 100%)
Static analysis says zero impact. To make it **empirical**, before merging any change:
- Run the **full test suite** (`pytest tests/ -q`, ~472 tests) and confirm green — proves the
  settings.py additions already made, and any new columns, break nothing.
- Run `session_volume.py --no-write` and a **dry-run diff**: confirm it writes/changes **no existing
  file** and that re-running a past date reproduces identical existing columns (only new columns/files
  appear). 
> **These two runs are the gate. Until both are green, no code is merged.**

---

## PART 2 — Decisions that MUST be locked before any code (no guessing)

### D1. Data model — confirm what we are actually measuring
The source is `ct_options_tape.csv` = **options volume** (`call_vol`/`put_vol` per strike), where the
`contract` column is the **underlying futures month** the option sits on. The plan is: measure
**options volume, bucketed by futures-month complex**. **Confirm this is the intent** (vs. literally
tracking futures-contract volume, which lives only in the once-daily `rtd_snap.json` and would be a
different build).

### D2. Serial option months (CTU/CTX/CTF) — drop or fold?
You noted these are **serial-month option contracts, not valid futures contracts**, and we only
track the futures months (Z/H/K/N). Two ways to honor that — must pick one:
- **(a) Drop** serial-month flow entirely (purest "futures months only"). Cleanest with your framing.
- **(b) Fold** each serial into its parent futures complex (CTU/CTX→Dec, CTF→Mar) because those
  options exercise into that future. Keeps the Dec/Mar complex's *options* flow complete.
Note: **moot in current data** — serials carry ~zero traded volume in the live sessions, so either
choice yields identical numbers today. But the definition must be fixed before it ever matters.

### D3. Generic depth
Confirmed: **1st and 2nd generic of each month are required.** 3rd+ (e.g. DEC3) — capture if already
present is "no harm," but **not a prerequisite**; mark `in_universe=0` for position ≥ 3 so totals
stay on the 1st/2nd-only definition.

### D4. Repo placement (still open — deferred earlier, decide with the facts above)
Given Part 1 proves the additive design changes nothing for any other consumer, in-repo is now a
low-risk option. Decide: keep in `Options_flow_analyzer` (reuses settings/tape paths) vs. extract to
a standalone repo (max separation). Not blocking the data-model work; decide before the API phase.

---

## PART 3 — Sign-off checklist (all must be ✓ before code)
- [ ] D1 data-model confirmed (options-volume-by-futures-month).
- [ ] D2 serial treatment chosen (drop vs fold).
- [ ] D3 generic depth confirmed (1st/2nd required; 3rd optional, in_universe=0).
- [ ] D4 repo placement decided.
- [ ] Full `pytest` suite green after settings additions (empirical no-collision proof).
- [ ] `--no-write` dry-run diff shows zero change to any existing file/column.

Only when every box is ✓ does the analyzer get a build prompt. Static analysis (Part 1) already
shows **no other consumer's collection changes**; Part 3 makes it empirical.
