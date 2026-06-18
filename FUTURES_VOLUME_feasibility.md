# Feasibility: pivot to FUTURES-contract volume (D1 = futures, D2 = drop serials)

**No code. Feasibility + decisions only.**

## What changed
- D1: track **futures-contract volume**, not options volume.
- D2: **drop serial option months** (CTU/CTX/CTF) — they aren't futures contracts.

The current `session_volume.py` measures **options** volume (the `ct_options_tape.csv` call_vol/put_vol).
That is the wrong source for D1. Futures volume needs a different source.

## Key finding — the data is already in the feed, just discarded
- The RTD futures read exposes per-contract `volume` (and `oi`, `block_vol`, `efs_vol`) on **every poll**
  (confirmed in `rtd_snap.json` futures keys).
- But `price_tape.py` writes only `last,bid,offer,mid,settle,market_state` (`_FIELDS`) — it **drops volume**.
- The price tape's `contract` column is the **futures month** (CTZ6, CTN6…). No serial option series — so
  D2 ("drop serials") is automatic on this source.

## The additive-column path (owner's model — validated against the code)
Append a `volume` (and optionally `oi`) column to the price tape. It is invisible to other consumers:
- `gex_calculator` reads the tape via `pd.read_csv` and accesses columns **by name** → an extra trailing
  column is ignored.
- `test_price_tape` compares the header to the script's own `_FIELDS` (auto-updates) and checks row counts,
  not column counts.
- No positional / usecols / column-count reader of the price tape exists.
Conditions for safety (all met here): append at the END, readers use name access, no column-count assertion.
**Not zero-touch though:** it edits an existing tested producer (`price_tape.py` + `test_price_tape.py`,
with `gex_calculator` downstream). Safest kind of change, but must be PROVEN: full `pytest` green + confirm
gex output byte-identical before/after.

## What's possible vs not
| View | Available? | Source |
|---|---|---|
| **Full-session / daily** futures volume per contract | YES, now + historically | `rtd_snap.json` (EOD) and VLM API `oi_data.csv` `PX_VOLUME` per generic, back to **2008** |
| **Overnight vs day split** for futures | NO today — **forward-only** after adding the price-tape `volume` column | new column on `price_tape.py` |
| December-over-years (full session) | YES immediately | VLM API generic series (CTDEC1…) |

## Caveats
- **Forward-only split.** Overnight/day futures split starts accruing only from deployment of the new column.
  Until then, only full-session/daily futures volume exists (historically deep via API).
- **Tick-sampled volume.** The price tape writes on a ≥0.01 price move, not a fixed clock. Volume would be
  read at the last tick ≤ each boundary (07:00 / 14:20). Decide: also write on volume change, or accept tick
  sampling.
- **Existing options-volume work.** `session_volume.py` (options) is live and verified. Decide its fate
  (below) — it is not the futures product.

## Decisions to lock before any code
1. **Confirm the futures path:** append `volume` to the price tape (forward) + use `rtd_snap`/VLM API for
   historical full-session. (Y/N)
2. **Forward-only overnight/day split acceptable?** History shows full-session futures volume only, until the
   new column accrues sessions. (Y/N)
3. **Capture cadence:** also write a tape row on volume change, or accept tick-sampled volume at price moves?
4. **Fate of the existing options `session_volume.py`:** retire it, keep it in parallel (options view
   alongside futures), or repurpose? It measures options volume — a different product from D1.
5. **Empirical proof gate (unchanged):** full `pytest` green + `gex` output unchanged + dry-run diff showing
   no existing file/column altered, before merge.
