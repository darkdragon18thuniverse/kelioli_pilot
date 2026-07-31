# Handoff Context — Kelioli Pilot (compliance rescan investigation)

Date: 2026-07-31. Carry this into the next session. Read `PROJECT_CONTEXT.md` first for stack/history.

---

## 1. Environment facts (verified this session)

| | |
|---|---|
| Backend repo (local) | `/Users/vinamramattoo/projects/curigon/kelioli_pilot` |
| Frontend repo (local) | `/Users/vinamramattoo/projects/curigon/kelioli_pilot_ui` |
| **Prod host path** | `/home/darkdragon18thuniverse/kelioli_pilot` (NOT `/home/ubuntu/...` — `makeserver.sh` seds `ubuntu`→`$USER`) |
| Prod DB | `src/app/production.db` (relative `DATABASE_PATH`, SQLite WAL) |
| Prod runtime | systemd `kelioli` → gunicorn `-w 4 -k uvicorn.workers.UvicornWorker`, nginx reverse proxy, no caching |
| `sqlite3` CLI | **not installed on prod** — use `python3 -c "import sqlite3; ..."` |
| Logs | `sudo journalctl -u kelioli` |

**Local `production.db` is stale (last write 2026-07-28). Always query prod for data questions.**

---

## 2. What was diagnosed and RESOLVED

**Symptom:** superadmin rescans (LLM-only and full) appeared to keep returning old cost data (~6.8L) after the compliance rule was changed.

**Root cause: Gemini free-tier daily quota exhaustion.** Every reprocess after 11:39:14 threw `429 RESOURCE_EXHAUSTED` (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`, `gemini-3.6-flash`, limit 20/day) at `calls_controller.py:648`, i.e. **before** any DB write. The row was never updated, so the UI kept showing the 11:39:14 result.

**Fix applied by user:** switched the org to `gemini-3.5-flash-lite`. Confirmed working.

### Live quota data (AI Studio, project `personal-mac` / `gen-lang-client-0886388872`, **Free tier**)

| Model | RPM | TPM (input) | RPD |
|---|---|---|---|
| Gemini 3.6 Flash | 4 / **5** | 35.55K / 250K | **22 / 20** ⚠ over |
| Gemini 3.5 Flash Lite | 3 / **15** | 9.67K / 250K | 8 / **500** |

API key `…yWcQ` ("Kelioli API KEY") is on the free tier; billing not set up. Google's docs no longer publish a per-model tier table — AI Studio (`https://aistudio.google.com/rate-limit`) is authoritative.

### ⚠ Corrections to the earlier scan report
Two hypotheses from the first pass were **disproved** — do not chase them:
- ❌ "AI Format strips the numbers from the rule" — **not** what happened. Prod rule id 16 has the figures intact.
- ❌ "duplicate `call_evaluations` rows from 4 gunicorn workers" — prod query returned **zero** duplicates.
- ❌ `compliance_parameters.id=1` referenced in the first report is the **local stale DB**. Prod uses ids 15/16 and 5/6.

---

## 3. REMAINING CRITICAL ISSUES

### C1 — Duplicate active compliance rules (data + missing guard)
Prod dept 1 has **two `is_active=1` rows per concept**:

| ids | name | difference |
|---|---|---|
| **15**, 16 | Cost Communication | id 15 says *"the approved price range"* with **no figures**; id 16 has `min 3.8L (3,80,000) to max 8.9L (8,90,000)` but **no `Fails if:` section** |
| **5**, 6 | Cost / EMI Handling | identical `Expected:` line; id 6's failure list is a strict superset of id 5's |

Log confirms all are sent: *"across 8 rules"*. Consequences:
- id 15 tells the model to enforce an undefined range → it supplies its own number (**this is the real source of the bad figure**).
- Cost topics occupy 4 of 8 scoring slots → double-weighted in `compliance_score_percentage`.

Nothing prevents two active rules with the same `parameter_name` in one department. `ComplianceParameter.create()` (`models/compliance.py:8`) has no uniqueness check; both dashboard edit paths branch correctly to update, so these were created manually.

**Open:** user said the range is `3.6L`, rule 16 says `3.8L` — confirm which is correct.

### C2 — `company_context` / `department_context` wiped in prod
```
org  1  'Best Laser Dental Clinic'  company_context    = ''
dept 1  'Mugalivakkam Clinic'       department_context = ''
dept 2  'Valasaravakkam Clinic'     department_context = ''
```
Had full clinic descriptions on 2026-07-28. `stt.py:604-605` now sends `Company Context: N/A` on every evaluation. Suspected cause: `Organization.update` / `Department.update` filter on `v is not None`, so an empty string from the edit form or the Ask-Curi context scratchpad overwrites saved content. **Needs confirmation, then a guard.**

### C3 — Retry loop retries a daily quota error
`stt.py:203 retry_with_backoff` treats any 429 as retryable and escalates to 5 attempts. A `...PerDayPerProject...` quota cannot recover before midnight PT. Each click therefore burns 5 more requests against the cap and pins one of 4 gunicorn workers for ~4 minutes. Must distinguish per-minute (retry) from per-day (fail fast).

### C4 — Raw 500 on LLM failure
`reprocess_single_call` has no try/except around `LLMService.evaluate` → the Google exception surfaces as an unhandled 500 with a full stack trace. Frontend shows the raw JSON after a 4-minute hang. Should be a 503 with a clear "LLM quota exhausted" message. (Prior results are correctly left intact.)

### C5 — Wasted STT on failed `full` reprocess
`mode="full"` transcribes first (48s / 19 Sarvam chunks, billable), then dies at the LLM step and discards the transcript. Persist the transcript before the LLM call, or fail fast.

### C6 — Rate limiter is per-process, and mis-tuned
`GEMINI_MIN_INTERVAL` defaults to `2.0`s (`stt.py:75`) = 30 req/min, vs caps of 5 RPM (flash) / 15 RPM (flash-lite). Worse, `_last_gemini_request_time` (`stt.py:80`) is a **per-process** global — with `-w 4` the effective rate is 4× configured. Same applies to `_last_stt_request_time` / `STT_MIN_INTERVAL`.

### C7 — Background workers ×4
`main.py:54 @app.on_event("startup")` fires in **every** gunicorn worker → 4 call-queue pollers, 4 billing-snapshot workers, 4 DB-backup workers on one SQLite file. The queue claim (`call_queue_worker.py:29`) is atomic so double-claim is guarded, but the backup/snapshot workers are not idempotent-by-design across processes.

---

## 4. LOWER-PRIORITY DEFECTS (confirmed, not yet fixed)

| # | Issue |
|---|---|
| L1 | `SuperadminCallControlModal.jsx:16` sends `mode:'stt'`; `calls.py:95` accepts `Literal["full","transcription","llm"]` → **"STT Only" button always 422s**. Batch modal is correct. |
| L2 | **Token accounting dead.** `stt.py:651` returns only `procedure_enquired`/`evaluations`; callers read `prompt_tokens`/`completion_tokens`/`model_used` (`calls_controller.py:194-196`, `680-682`) → always 0. DB confirms `upstream_tokens_* = 0` everywhere. Gemini stream loop (`stt.py:417-421`) discards `usage_metadata`. |
| L3 | `_run_evaluation_pipeline` uses `CallEvaluation.create_batch` (append) while reprocess uses `replace_evaluations` → queue-path re-runs would duplicate eval rows. |
| L4 | Frontend sends `rule_name` to `/compliance/format-rule`; `FormatRuleRequestSchema` (`api/v1/compliance.py:25`) has no such field → silently dropped. |
| L5 | `compliance_parameters` has no `updated_at` — can't correlate a rule change with a call's score. |
| L6 | `reprocess_single_call` skips `_enforce_org_active` — rescans bypass the suspension / `limit_exceeded` gate. |
| L7 | `SuperadminCallsModule.handleActionSuccess` (line 30) reloads the list but not an open `selectedCallDetail` → stale audit report on screen after a rescan. |
| L8 | Live `SARVAM_API_KEY`, `OPENROUTER_API_KEY`, `GEMINI_API_KEY`, `JWT_SECRET_KEY` in `kelioli_pilot/.env` — untracked but **not in `.gitignore`**. One `git add .` from being committed. |
| L9 | `parameters.csv` at backend root is dead (no code reads it) and still holds the superseded `₹3.8L–6.8L` spec. Delete or move to `docs/`. |

---

## 5. OPEN DECISIONS (needed before coding)

1. **Where does the price range live?** Inside `rule_description` text (current), in `department_context`, or a new structured field on `compliance_parameters`? Affects whether C1's guard is enough.
2. **Duplicate-rule policy:** hard 409 on same `parameter_name` + `department_id` + `is_active=1`, or soft warning in the UI?
3. **Correct range:** 3.6L or 3.8L to 8.9L?
4. **Quota strategy:** enable billing (Tier 1) vs. stay free-tier on flash-lite and enforce real client-side rate limiting (C6).
5. **Scope for the next session:** C1–C5 only, or include C6/C7 (worker/concurrency) and the L-list?

---

## 6. Verification commands (prod, read-only)

```bash
cd /home/darkdragon18thuniverse/kelioli_pilot

# duplicate active rules
python3 -c "
import sqlite3;c=sqlite3.connect('src/app/production.db')
print('--- ACTIVE RULES BY DEPT ---')
for r in c.execute('SELECT department_id,id,parameter_name,severity_level,created_at FROM compliance_parameters WHERE is_active=1 ORDER BY department_id,parameter_name,id'):print(r)
print('--- SAME-NAME ACTIVE DUPLICATES ---')
for r in c.execute('SELECT department_id,parameter_name,COUNT(*) n,GROUP_CONCAT(id) ids FROM compliance_parameters WHERE is_active=1 GROUP BY 1,2 HAVING n>1'):print(r)
print('--- CONTEXTS ---')
for r in c.execute('SELECT id,name,length(coalesce(company_context,\"\")) FROM organizations'):print(r)
for r in c.execute('SELECT id,name,length(coalesce(department_context,\"\")) FROM departments'):print(r)
print('--- RECENT CALLS ---')
for r in c.execute('SELECT id,updated_at,compliance_score_percentage,runtime_llm_model,processing_status,upstream_tokens_prompt FROM calls ORDER BY updated_at DESC LIMIT 10'):print(r)
"

# quota / pipeline errors
sudo journalctl -u kelioli --since today | grep -iE '429|RESOURCE_EXHAUSTED|UNHANDLED|Evaluating call_id'
```

Local test suite: `cd kelioli_pilot && pytest` (was 127/127; 3 STT-chunking tests fail only when sandbox `ALL_PROXY`/`HTTP_PROXY` are set).
Frontend: `cd kelioli_pilot_ui && npx vite build`.
