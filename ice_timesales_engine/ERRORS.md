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

## 2026-07-14 — stale server on :5062 kept serving old code

**Didn't work:** editing api/routes_query.py then testing against
http://127.0.0.1:5062 — the OLD error text came back. A previous engine
instance was still LISTENING on 5062, and Start-Process launched a second
python that silently lost the port race. First kill attempt ALSO failed:
there were multiple listener PIDs (Flask parent+child) — killing one left
the port busy.

**Worked instead:** enumerate ALL PIDs via Get-NetTCPConnection -LocalPort
5062 -State Listen, kill every one, loop until the port reads free, THEN
start — and prove the new code is live by hitting a code path whose response
text changed.

**Note:** Start_Session_Volume.bat intentionally reuses an already-running
server (that's its design). After ANY code change, kill the listeners first
or the "restart" is a no-op.

## 2026-08-20 — TEMPLATE edits invisible: Jinja cache, not a port race

**Didn't work:** editing `ui/templates/dashboard.html` (75 lines, verified on
disk via git diff) and asking Lou to re-export the PNG — he got a
byte-identical image twice. Unlike 2026-07-14 this was NOT a port race and NOT
a duplicate listener: the right process was serving on 5062 and answering
`/health` with `{"ok":true}` the whole time.

**Cause:** `api/app.py` runs `create_app().run(..., debug=False)` and never set
`TEMPLATES_AUTO_RELOAD`. Jinja compiles a template on first render and caches
it for the life of the process, so a long-lived server keeps serving the
dashboard.html it read at STARTUP. Disk edits are invisible until restart.
Health checks and 200s tell you nothing — the old build answers them perfectly.

**Worked instead:** diff the SERVED html, not the file —
`curl -s http://127.0.0.1:5062/ | grep -c _niceAxis` returned **0** while the
same grep on disk returned 12. That one command proves stale-vs-live in
seconds. Then kill the PID, restart, and re-grep for the specific NEW symbol
(not a 200) until it appears.

**Fixed at the root:** `app.config['TEMPLATES_AUTO_RELOAD'] = True` in
`create_app()`. Template edits now show on a browser refresh, no restart.
Confirmed by the effect: edited a comment, and the old text vanished from the
served HTML with the process untouched.

**Note for next time:** for a UI-only change, the browser is a SECOND cache in
front of this one. After the server is confirmed live, still hard-refresh
(Ctrl+Shift+R) — the PNG is drawn by in-page JS, so a cached page keeps drawing
the old canvas even against a correct server. Two caches, two fixes.
