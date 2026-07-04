# ERRORS.md — ice_timesales_engine

> When an approach takes more than 2 attempts: log What didn't work / What
> worked instead / Note for next time. Check before suggesting approaches to
> similar problems.

---

## 2026-07-04 — `from config import ...` inside our own config.py

**Didn't work:** re-exporting the VLM repo's config via
`sys.path.insert(0, VLM_REPO); from config import X` inside this repo's own
`config.py`. Python resolves `config` to `sys.modules['config']` — the
half-initialized module itself — → ImportError/garbage.

**Worked instead:** load the VLM config explicitly by file path with
`importlib.util.spec_from_file_location('vlm_session_volume_config', path)`,
then assign the needed names. Also `sys.path.append` (not insert-0) so THIS
repo's modules win name clashes.

**Note:** the reused `contract_resolver` itself does `from config import
CT_ACTIVE_MONTH_CODES, ...` — when imported from this engine, that resolves to
OUR config, so our config must re-export those three names. Any new name the
resolver starts importing must be re-exported here too.

## 2026-07-04 — semicolon inside a schema.sql comment

**Didn't work:** `db.init_schema()` splits schema.sql on `;`. A `;` inside a
`--` comment produced a bogus statement (`near "add": syntax error`) — 9 test
errors from one comment character.

**Worked instead:** removed the semicolon from the comment and added a NOTE in
the DDL header: never put `;` inside a comment in that file.

**Note:** if the schema ever needs literals containing `;`, replace the naive
split with a proper statement splitter first.
