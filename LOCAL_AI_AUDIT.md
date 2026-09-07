# Raspberry Pi 5 Local-AI Deployment — Architecture Audit

Repo: `KPIsAgent` (github.com/Kwendo-sean/KPIsAgent), branch `main`
Target: Raspberry Pi 5, 4 GB, Debian 13 trixie, aarch64, Python 3.13
Local LLM: existing `treplex-llm.service` → llama-server on `127.0.0.1:8081` (Qwen2.5-0.5B-Instruct Q4_K_M)

**Status: audit only. No code changed.**

---

## A. CURRENT ARCHITECTURE

| Layer | Detail |
|---|---|
| Framework | Django 4.2 + DRF 3.14 + django-cors-headers |
| Project | `hospital_kpi/` (settings, urls, wsgi, asgi) |
| App | `kpi/` — single app, no other apps |
| Entry | `manage.py runserver` (no gunicorn/uvicorn in requirements) |
| DB | SQLite (`db.sqlite3`) by default; MySQL only if `MYSQL_DATABASE` env set |
| Cache | Django `DatabaseCache` (`cache_table`) — no Redis |
| Queue | **django-q2**, ORM broker, `workers: 2`, `sync: False` — a *second* process (`manage.py qcluster`) |
| Templates | Plain `.html` at repo root; `TEMPLATES.DIRS = [BASE_DIR]` |
| Frontend | Vanilla JS + Chart.js. **No package.json, no build step.** |
| Auth | Django session auth + TOTP 2FA (pyotp/qrcode), password reset by email |
| Reports | xhtml2pdf + matplotlib (Agg, headless) server-side charts; openpyxl for Excel export |

Key modules in `kpi/`:

- `ai_agent.py` (71 KB) — all LLM calls + all KPI math. The file to change.
- `pdf_extractor.py` (30 KB) — deterministic PDF/CSV/XLS parsing, regex M-PESA parser.
- `views.py` (134 KB) — upload flow, dashboards, API endpoints.
- `pii_redactor.py` — redacts account/card/IBAN/phone/holder-name before any external call.
- `tasks.py` — django-q2 task wrappers.
- `chart_utils.py` — matplotlib PNG charts for PDF reports.
- `industry_config.py` — per-industry KPI config (untracked, new).

---

## B. CURRENT AI FLOW

### B.1 Providers

Six hardcoded provider functions in `kpi/ai_agent.py`, each with its own URL and SDK/REST call:

| Function | Line | Endpoint | Key env var |
|---|---|---|---|
| `_ask_ollama` | 71 | `http://localhost:11434/api/chat` | — (**dead code**, see below) |
| `_ask_groq` | 92 | `api.groq.com/openai/v1/chat/completions` | `GROQ_API_KEY` |
| `_ask_fireworks` | 133 | Fireworks OpenAI-compatible | `FIREWORKS_API_KEY` |
| `_ask_claude` | 179 | Anthropic SDK | `ANTHROPIC_API_KEY` |
| `_ask_gemini` | 201 | Google generativelanguage REST | `GEMINI_API_KEY` |
| `_ask_llm7` / `_ask_llm7_text` | 275 / 299 | LLM7.io OpenAI-compatible | `LLM7_API_KEY`, `_2`, `_3` |
| `_ask_openrouter` | 308 | OpenRouter OpenAI-compatible | `OPENROUTER_API_KEY` |

### B.2 Dispatch — there IS a chokepoint, but not a clean abstraction

```
_PROVIDER_FUNCS  (line 342)  = { llm7-<model> ... }   # Gemini list is empty
_PROVIDER_ORDER  (line 346)  = list of those keys

_ask(prompt, max_tokens, primary, json_mode)   line 366
    → round-robin over _PROVIDER_ORDER, first non-None wins
    → used for STRUCTURED JSON work (extraction, CSV mapping)

_ask_text(prompt, max_tokens)                  line 349
    → OpenRouter → LLM7 text key → falls through to _ask()
    → used for NARRATIVE work (insights, Q&A, reports)

_claude_client_for_vision()                    line 390
    → Anthropic SDK only, for OCR of scanned PDFs
```

**Assessment:** there is no provider *class* abstraction and no `base_url` configuration — every provider is a bespoke function. **But** every text-generating call in the app funnels through exactly two functions (`_ask`, `_ask_text`) plus one vision path (`_claude_client_for_vision`). That is enough. We add one `_ask_local()` and gate those three chokepoints. No provider rewrite needed.

`_ask_ollama` is defined but **never registered** in `_PROVIDER_FUNCS` — it is unreachable dead code. It is nevertheless the exact shape we need (localhost HTTP, short timeout, no key), so it is a template to copy. **We are not installing Ollama.**

Key loading: `_env_key(name)` (line 56) reads Django settings first, then `os.environ`, and calls `load_dotenv(BASE_DIR/.env, override=False)` on every call. So a config value can be supplied via `.env` *or* `settings.py` and both work.

### B.3 What actually calls the LLM

| Task | Method | Path | LLM genuinely required? |
|---|---|---|---|
| OCR of scanned PDFs | `ocr_pdf_pages` (494) | Claude Vision only | **No local equivalent** — Qwen-0.5B is text-only |
| Per-chunk transaction extraction | `_extract_batch` (579) | `_ask(json_mode=True)`, 8192 max_tokens | **Should not be** — see D.1 |
| Statement summary fields | `extract_financial_data` (821) | `_ask(json_mode=True)`, 512 tokens | Marginal — regex parser already covers M-PESA |
| CSV/XLSX column mapping | `extract_csv_structure_with_ai` (708) | `_ask` | Marginal — header heuristics exist |
| Narrative insights | `generate_insights` (1128) | `_ask_text`, 1024 tokens | **Yes** — good local-LLM fit |
| Q&A on KPIs | `answer_question` (1178) | `_ask_text`, 1024 | **Yes** |
| System/copilot Q&A | `answer_system_question` (1190) | `_ask_text`, 1024, 8 KB context | Yes, but context must shrink |
| Report summary | `generate_report_summary` (1204) | `_ask_text`, 1024 | Yes |
| Detailed CFO report | `generate_detailed_report` (1219) | `_ask_text`, **4096** | Yes, but too long for 0.5B/1024ctx |
| Alerts | `generate_alerts` (1146) | **pure Python** | No |
| KPIs | `calculate_kpis` (917) | **pure Python** | No |
| Health score | `compute_health_score` (1035) | **pure Python** | No |
| Recurring payments | `detect_recurring_payments` (1072) | **pure Python** | No |

Every narrative method already has a deterministic offline fallback: `_generate_local_insights`, `_answer_locally`, `_answer_from_system_context`, `_build_local_report_summary` (lines 1243–1361). These are valuable — the Pi can degrade to them gracefully.

**The deterministic KPI architecture is intact and must be preserved.** `calculate_kpis` is plain Python over a transaction list, and `extract_financial_data` deliberately overrides the model's closing balance with `opening + deposits − withdrawals` (line 878, with a comment saying the AI value is "frequently wrong"). Good. Do not touch.

---

## C. BANK STATEMENT PROCESSING FLOW

```
POST upload (views.py ~1261)
├── PDF branch (~1438)
│   ├── PyMuPDF/PyPDF2 text extraction (password-aware)
│   ├── render_pdf_pages_to_images()  → always renders page PNGs
│   ├── if needs_ocr(text) → ocr_pdf_pages()          ← CLAUDE VISION
│   │   else: parse_mpesa_transactions(); if empty → ocr_pdf_pages()  ← CLAUDE VISION
│   ├── save extracted_text
│   └── async_task("kpi.tasks.process_statement_task")   (sync fallback if no qcluster)
│         └── process_bank_statement_with_ai (views ~1810)
│               └── agent.extract_financial_data()
│                     ├── extract_transactions_in_batches()
│                     │     ├── _normalize_text (M-PESA receipt-line regex)
│                     │     ├── _chunk_text(6000 chars)
│                     │     ├── ThreadPoolExecutor(max_workers=6)  ← 6 PARALLEL LLM CALLS
│                     │     ├── _extract_batch × N   ← LLM, json_mode, 8192 tokens
│                     │     └── dedupe (date, desc[:80], amount/10, type)
│                     ├── if zero transactions → build_fallback_financial_data_from_text()  ← REGEX
│                     ├── summary_prompt → _ask()   ← LLM
│                     ├── _validate_completeness()  (logs >20% gaps)
│                     └── totals + closing recomputed deterministically
│               └── _process_with_financial_data() → calculate_kpis → save KPIs/alerts/health
└── CSV / XLSX / XLS branch (~1261)
    ├── csv reader / openpyxl / xlrd → rows
    ├── extract_csv_structure_with_ai(first 10 rows)   ← LLM column mapping
    ├── deterministic row parsing (_parse_date, _parse_decimal, _resolve_amounts)
    └── async_task("kpi.tasks.process_csv_statement_task", fin_data)
```

Deterministic assets already in `pdf_extractor.py` that the Pi can lean on:

- `_parse_mpesa_detail_rows` (413) — full receipt-line parser, the strongest path
- `_parse_mpesa_summary` (321) — PAID IN / PAID OUT summary table
- `_parse_transaction_rows` (561) — generic date+amount heuristic
- `build_fallback_financial_data_from_text` (258) — orchestrates the above
- `_find_header` / `_normalize_header` (612/619) — CSV header matching helpers, currently unused by the AI mapping path

PII redaction (`_redact_pii`) is applied to chunks and summary text before dispatch. On the Pi this becomes belt-and-braces rather than the primary control, since nothing leaves the box.

---

## D. RASPBERRY PI COMPATIBILITY ISSUES

### D.1 BLOCKER — transaction extraction is LLM-based and will not survive on Qwen2.5-0.5B/1024 ctx

`_extract_batch` sends a ~1,500-token instruction prompt plus a 6,000-character chunk (~1,800 tokens) and asks for up to 8,192 output tokens of strict JSON.

- Required context ≈ **3,300+ tokens in, thousands out**. Server context is **1024**. The request cannot fit — it will be truncated or rejected.
- Even if context were raised (we were told not to), at 29 tok/s generating a few thousand JSON tokens per chunk × N chunks × 6 parallel = many minutes per statement on a shared 4-thread CPU.
- A 0.5B model is not reliable at strict-JSON structured extraction from noisy statement text. Silent wrong numbers on a banking demo is the worst possible failure.

**Resolution:** in local mode, transaction extraction must run on the existing deterministic parsers (`_parse_mpesa_detail_rows` → `build_fallback_financial_data_from_text`), not the LLM. This is not a redesign — it is the code path the app already falls back to today, promoted to primary. It also matches the stated principle: the LLM interprets, it does not compute.

### D.2 BLOCKER — OCR has no local equivalent

`ocr_pdf_pages` is Anthropic-vision-only. Qwen2.5-0.5B-Instruct is text-only. In local mode, OCR must be **disabled with an explicit error**, never a silent call to Anthropic. Demo must therefore use **digital (text-layer) PDFs, CSV, or XLSX** — not scans. (Tesseract would be the local answer later; it is additional infrastructure and out of scope now.)

### D.3 BLOCKER — Django 4.2.0 does not support Python 3.13

`requirements.txt` pins `Django==4.2.0`. Python 3.13 support was only added in the 4.2.16 patch release. 4.2.0 on 3.13 will fail on removed stdlib modules. Two options:

1. Bump to `Django>=4.2.16,<5.0` — same 4.2 API, no code change expected. **Recommended.**
2. Install Python 3.11/3.12 on the Pi alongside 3.13 — extra infrastructure, avoid.

### D.4 Parallelism vs. a single llama-server

`ThreadPoolExecutor(max_workers=min(total, 6))` fires six concurrent LLM calls. Against one llama-server with 4 threads and 1024 context this causes queueing, timeouts, and thrash. In local mode concurrency must be **1**. (Largely moot if D.1 is resolved, since extraction stops calling the LLM — but the guard belongs in the code regardless.)

### D.5 Prompt/response sizes exceed the 1024 context

- `generate_detailed_report`: 4096 max_tokens, plus a full JSON analytics context.
- `answer_system_question`: 8,000 characters of JSON context alone.
- `generate_insights`: full `json.dumps(kpis, indent=2)` — can easily exceed 1024 tokens by itself.

All narrative prompts need a compact local variant: trimmed context, `max_tokens` ≈ 320–512, and the existing deterministic fallbacks used when the model returns nothing useful.

### D.6 RAM budget (4 GB total)

| Consumer | Approx. |
|---|---|
| llama-server + Qwen2.5-0.5B Q4_K_M + KV | ~0.6–0.9 GB (already resident) |
| Django runserver | ~150–250 MB, more with matplotlib/PyMuPDF loaded |
| django-q2 qcluster (2 workers = 2 more Python processes) | ~150–250 MB each |
| OS + desktop/AP services | ~0.5–1.0 GB |

Reduce `Q_CLUSTER["workers"]` to 1 on the Pi, or run `sync: True` and skip qcluster entirely for the demo (simpler, one process, but the upload HTTP request then blocks — acceptable once extraction is deterministic and fast). `render_pdf_pages_to_images` at 150 DPI holds every page as base64 PNG in memory — should be skipped entirely in local mode since OCR is disabled (D.2). That alone is a large RAM and latency win.

### D.7 aarch64 / cp313 wheel availability

| Package | Risk |
|---|---|
| `PyMuPDF>=1.23.0` | manylinux aarch64 wheels exist; ensure a recent version for cp313 or it tries a source build |
| `matplotlib>=3.7.0` | aarch64 cp313 wheels available on recent versions; old pins may build from source (slow, needs headers) |
| `xhtml2pdf` / `reportlab` | reportlab needs recent version for cp313 wheels |
| `PyPDF2==3.0.1` | pure Python, fine (deprecated upstream, but leave it — not our scope) |
| `pyotp`, `qrcode`, `openpyxl`, `xlrd`, `requests`, `markdown`, `python-dotenv` | pure Python, fine |
| `anthropic>=0.40.0` | pure Python. **Keep installed** so cloud mode still works elsewhere; it simply must never be *called* in local mode |
| `django-q2>=1.6.0` | pure Python, fine |

Also: Debian trixie enforces **PEP 668** (externally-managed environment). System-wide `pip install` will refuse. Must use a venv — which the README already prescribes.

### D.8 Offline UI degradation (demo-quality, not a blocker)

Templates load from the internet:

- `https://cdnjs.cloudflare.com/.../font-awesome/6.4.0/css/all.min.css`
- `https://fonts.googleapis.com/css2` (Inter, JetBrains Mono)
- `https://cdn.jsdelivr.net/npm/marked/marked.min.js`
- `https://cdnjs.cloudflare.com/.../three.js/r134/three.min.js`
- Chart.js (also CDN)

On the isolated `TREPLEX-AIOT` network with no internet, icons vanish, fonts fall back, and **markdown report rendering (`marked`) and charts (Chart.js) break outright**. For a banking event demo this is visible. Fix by vendoring those few files into `static/` — mechanical, no logic change. Flagging it now; it is separate from the local-AI work.

### D.9 Networking / binding

- No port or bind configuration anywhere; `runserver` defaults to `127.0.0.1:8000`. Needs `0.0.0.0:8090`.
- `ALLOWED_HOSTS` defaults to `*` via env, so it works, but should be pinned to `10.42.0.1,127.0.0.1,localhost`.
- `CSRF_TRUSTED_ORIGINS` is **not set**. Django 4.2 requires it for non-localhost origins — POST uploads and all AJAX from `http://10.42.0.1:8090` will fail CSRF until `http://10.42.0.1:8090` is added. **This will bite on first test if missed.**
- `CORS_ALLOWED_ORIGINS` lists only localhost:8000 — add the Pi origin if any cross-origin call is used.
- `DEBUG` defaults True. Keep True for the demo only if you accept the tracebacks; `runserver` with `DEBUG=False` needs `--insecure` or `collectstatic`. Simplest for a demo: `DEBUG=True`, isolated network, no internet. Note it as a deliberate demo-only choice.

### D.10 Repository / secrets hygiene

- Working tree has **uncommitted changes** (`base_dashboard.html`, `ai_agent.py`, `models.py`, `urls.py`, `views.py`, `login.html`, `requirements.txt`) and **untracked new files** (`industry_config.py`, migrations `0004`/`0005`, `kpi/tests/`, `landing.html`, `register.html`, `accounts.html`, `pytest.ini`). A `git clone` on the Pi will **not** get any of this. Commit and push before deploying.
- `settings.py` at repo root is deleted (`D settings.py`) — the live settings are `hospital_kpi/settings.py`. Fine, just don't be confused by it.
- `.env` is gitignored (correct) and holds **live Groq, Anthropic, Gemini, and LLM7 keys**. **Do not copy this `.env` to the Pi.** Write a fresh Pi-only `.env` with no cloud keys at all — that is a second, independent guarantee that nothing can leave the device.
- `db.sqlite3` is **tracked in git** and contains prior data. Use a fresh DB on the Pi (`migrate` from empty) rather than shipping real records to a demo device.
- Root-level scratch files (`check_gemini.py`, `list_models*.py`, `available_models.txt`, `gemini_results.txt`, `design_import.html` at 4 MB) are clutter; harmless, but no need to deploy them.

---

## E. EXACT FILES THAT NEED CHANGES

Minimum set. Nothing else.

| # | File | Change | Size |
|---|---|---|---|
| 1 | `kpi/ai_agent.py` | Add `LOCAL_AI_*` config reads; add `_ask_local()`; gate `_ask`, `_ask_text`, `_claude_client_for_vision`; force serial extraction; compact local prompts | ~120 lines added, ~15 modified |
| 2 | `hospital_kpi/settings.py` | `LOCAL_AI_MODE` / `LOCAL_AI_BASE_URL` / `LOCAL_AI_MODEL` / `LOCAL_AI_TIMEOUT` / `LOCAL_AI_MAX_TOKENS`; `CSRF_TRUSTED_ORIGINS`; Pi-aware `Q_CLUSTER["workers"]` | ~15 lines |
| 3 | `kpi/views.py` | In local mode: skip `render_pdf_pages_to_images` + OCR; route extraction to deterministic parsers | ~20 lines, guarded |
| 4 | `requirements.txt` | `Django>=4.2.16,<5.0` (Python 3.13). No new packages. | 1 line |
| 5 | `.env.example` (new) | Document the new vars; no secrets | new file |
| 6 | `kpi/tests/test_local_ai.py` (new) | Assert no external call is possible when `LOCAL_AI_MODE=true` | new file |

Explicitly **not** changed: `calculate_kpis`, `compute_health_score`, `detect_recurring_payments`, `generate_alerts`, `pdf_extractor.py`, `pii_redactor.py`, `models.py`, migrations, templates (CDN vendoring is a separate optional task), all existing cloud provider functions.

---

## F. MINIMUM LOCAL-AI INTEGRATION PLAN

### F.1 Configuration

Project convention is flat `UPPER_SNAKE` env vars read through `_env_key()` (settings-first, `.env` fallback). Follow it:

```env
LOCAL_AI_MODE=true
LOCAL_AI_BASE_URL=http://127.0.0.1:8081/v1
LOCAL_AI_MODEL=qwen2.5-0.5b-instruct
LOCAL_AI_TIMEOUT=120
LOCAL_AI_MAX_TOKENS=384
```

Surface them in `settings.py` alongside the existing `ANTHROPIC_API_KEY` block so `_env_key` picks them up from either source. `LOCAL_AI_MODE` defaults to **false**, so every existing cloud deployment is unaffected.

### F.2 The local provider

One function modelled on the dead `_ask_ollama`, but hitting the OpenAI-compatible endpoint:

```
_ask_local(prompt, max_tokens) -> str | None
    POST {LOCAL_AI_BASE_URL}/chat/completions
    Authorization: Bearer local          # dummy; llama-server ignores it
    {"model": LOCAL_AI_MODEL,
     "messages":[{"role":"user","content":prompt}],
     "max_tokens": min(max_tokens, LOCAL_AI_MAX_TOKENS),
     "temperature": 0, "stream": false}
    timeout = LOCAL_AI_TIMEOUT
```

No new dependency — `requests` is already in `requirements.txt` and already used by every REST provider here.

### F.3 The hard gate (the security-critical part)

At the top of **both** `_ask()` and `_ask_text()`:

```
if LOCAL_AI_MODE:
    result = _ask_local(prompt, max_tokens)
    if result is None:
        raise LocalAIUnavailable("local model unavailable — refusing external fallback")
    return result
# ... existing cloud round-robin below, untouched
```

Raising rather than returning `None` is deliberate: returning `None` would let `_ask` continue into the cloud loop, and would let callers silently fall through to their offline fallbacks, hiding a broken model. An explicit exception satisfies the "must produce an explicit error, never a silent external call" requirement.

Third gate, in `_claude_client_for_vision()`:

```
if LOCAL_AI_MODE:
    return None    # OCR unavailable locally — callers already handle None
```

This is the belt. The braces is F.6.

### F.4 Route extraction back to deterministic code (the D.1 fix)

In `extract_financial_data()`, when `LOCAL_AI_MODE`:

1. Skip `extract_transactions_in_batches()` entirely.
2. Call `BankStatementPDFExtractor.parse_mpesa_transactions()`, then `build_fallback_financial_data_from_text()` — the code the app already trusts as its fallback today.
3. Skip the LLM `summary_prompt`; opening balance from the regex summary parser, closing balance from the existing deterministic `opening + deposits − withdrawals`.
4. `calculate_kpis()` runs unchanged on the resulting transaction list.

Net effect on the Pi: **all financial numbers come from Python, never from the model.** The model's only job becomes narrative — which is exactly the stated target architecture, and is the right job for a 0.5B model.

For CSV/XLSX, `extract_csv_structure_with_ai` gets a deterministic pre-pass using the existing unused `_find_header`/`_normalize_header` helpers against common header names (date/description/amount/debit/credit/balance), calling the model only if that fails — and in local mode, failing with a clear error rather than escalating.

### F.5 Compact prompts for local mode

For the five narrative methods, when `LOCAL_AI_MODE`:

- Trim the injected JSON context to the ~10 headline KPIs rather than the full dump.
- `max_tokens` 320–512, not 1024/4096.
- `generate_detailed_report`: build the section skeleton from `_build_local_report_summary` (deterministic) and ask the model only for a short executive paragraph per section. A 0.5B model cannot write a coherent five-section CFO report in one pass at 1024 context; composing from deterministic scaffolding is both faster and more accurate.
- Serial only — `max_workers=1` wherever a ThreadPoolExecutor drives LLM calls.

### F.6 Defence in depth

- Pi `.env` contains **no** `ANTHROPIC_API_KEY`, `GROQ_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, or `LLM7_*`. Every cloud function already returns `None` immediately when its key is absent — so even if a gate were bypassed, there is nothing to authenticate with.
- Optional and cheap: on Pi, add a startup assertion that logs loudly if `LOCAL_AI_MODE=true` **and** any cloud key is present.
- Logging already avoids statement bodies at INFO (only char counts and transaction counts). One thing to check during implementation: `logger.info("summary response: %s", repr(summary_text[:300]))` at line 855 echoes model output containing balances — trim or drop to DEBUG for the Pi.

### F.7 Ordering

1. Commit + push the working tree (D.10).
2. `requirements.txt` Django bump.
3. `settings.py` config block.
4. `ai_agent.py`: `_ask_local` + three gates.
5. `ai_agent.py`/`views.py`: deterministic extraction routing + OCR skip.
6. Compact local prompts.
7. Test on Windows first against a stubbed endpoint, then on the Pi.
8. Manual `runserver 0.0.0.0:8090` on the Pi. Test fully.
9. **Only then** discuss `bank-kpi-agent.service`.

---

## G. DEPENDENCIES THAT WOULD NEED INSTALLATION

**New Python packages required: none.** `requests` is already a dependency and covers the OpenAI-compatible call.

On the Pi:

```
python3 -m venv venv          # PEP 668 — venv is mandatory on trixie
pip install -r requirements.txt
```

with `Django>=4.2.16,<5.0`. Prefer wheels; if `matplotlib` or `PyMuPDF` attempt a source build, bump those pins to a version with cp313 aarch64 wheels rather than installing build toolchains.

Not installed, per constraints: Docker, Ollama, Kubernetes, Jupyter, TensorFlow, PyTorch, CUDA, any second LLM runtime, Node.js (not needed — no build step), any additional database (SQLite is built into Python).

`anthropic` stays in requirements (pure Python, no cost) so the same tree still works in cloud deployments; it is simply never invoked when `LOCAL_AI_MODE=true`.

---

## H. TEST PLAN

**H.1 Pre-flight, on the Pi**

```
curl -s http://127.0.0.1:8081/health
curl -s http://127.0.0.1:8081/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"qwen2.5-0.5b-instruct","messages":[{"role":"user","content":"Reply with OK"}],"max_tokens":8}'
systemctl status treplex-llm.service
```
Confirms the endpoint and the exact model name to put in `LOCAL_AI_MODEL`.

**H.2 Unit (runs on Windows too)**

- Existing `kpi/tests/test_ai_agent.py` must stay green — it covers the deterministic KPI math and must be unaffected. Run it first as a baseline, before any edit.
- New `test_local_ai.py`:
  - `LOCAL_AI_MODE=true` + `_ask_local` returning `None` → `_ask` and `_ask_text` **raise**, and no cloud function is entered (assert via monkeypatched spies on `_ask_groq`/`_ask_claude`/`_ask_gemini`/`_ask_openrouter`/`_ask_llm7`).
  - `LOCAL_AI_MODE=true` → `_claude_client_for_vision()` returns `None`.
  - `LOCAL_AI_MODE=false` → cloud round-robin behaves exactly as before (regression guard).
  - `_ask_local` builds the correct URL/payload against a mocked `requests.post`.

**H.3 Offline-guarantee test (the one that matters)**

On the Pi, block outbound traffic and process a statement end-to-end:
```
sudo ip route add blackhole 0.0.0.0/1
sudo ip route add blackhole 128.0.0.0/1
# upload + analyse a synthetic statement — must fully succeed
sudo ip route del blackhole 0.0.0.0/1 && sudo ip route del blackhole 128.0.0.0/1
```
Complementary check: `sudo ss -tnp | grep python` during processing must show **no** connection other than to `127.0.0.1:8081`.

**H.4 Functional**

Synthetic M-PESA-format PDF and a CSV. Verify: upload → transactions parsed → KPI values match a hand-computed spreadsheet → insights text generated locally → Q&A responds → PDF report renders. Compare KPI numbers against the same file processed in cloud mode on the laptop; the deterministic figures must be **identical**, since the same Python computes them.

**H.5 Failure-mode**

`sudo systemctl stop treplex-llm` → upload a statement. Expected: KPIs still compute (deterministic path unaffected), narrative sections show an explicit "local model unavailable" error, and `ss`/logs show zero external connections. Restart the service and confirm recovery.

**H.6 Resource**

`free -h`, `vmstat 2`, and `htop` during a full statement analysis. Watch for swap. Confirm Django RSS stays modest and llama-server is not evicted.

**H.7 Network**

From a laptop on `TREPLEX-AIOT`: `http://10.42.0.1:8090` loads, login works, upload POST succeeds (CSRF!), `http://10.42.0.1:8080` still serves the AIoT dashboard, and `http://10.42.0.1:8081` is **refused** (llama-server stays bound to loopback).

---

## I. RISKS / BLOCKERS

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| 1 | LLM-based extraction cannot run on 0.5B/1024ctx (D.1) | **Blocker** | Route extraction to existing deterministic parsers (F.4). Also the architecturally correct answer. |
| 2 | Deterministic parser quality on the demo file | **High** | Test with the *actual* demo statements early. If `_parse_mpesa_detail_rows` misses rows on your format, that parser — plain regex, no LLM — is where to invest. Do this before anything else; it decides the demo. |
| 3 | OCR unavailable locally (D.2) | **High** | Demo with digital PDFs / CSV / XLSX only. Explicit error on scans. Tesseract is a later, separate decision. |
| 4 | `Django==4.2.0` on Python 3.13 (D.3) | **Blocker** | Bump to `>=4.2.16,<5.0`. |
| 5 | `CSRF_TRUSTED_ORIGINS` unset → uploads fail from `10.42.0.1:8090` (D.9) | **High** | Add the origin. Cheap fix, easy to overlook, fails loudly at the worst moment. |
| 6 | Silent cloud fallback | **Critical if it happens** | Three gates + no keys in the Pi `.env` + the H.3 blackhole test. |
| 7 | 0.5B narrative quality on financial text | Medium | Set expectations: the model *explains* figures it is handed. Keep prompts short and grounded. Deterministic fallbacks already exist if output is unusable. |
| 8 | Broken UI offline — Chart.js, marked, Font Awesome, Google Fonts (D.8) | Medium | Vendor into `static/`. Mechanical; schedule before the event. |
| 9 | 4 GB RAM contention (D.6) | Medium | qcluster to 1 worker or `sync: True`; skip page-image rendering in local mode. |
| 10 | aarch64/cp313 source builds (D.7) | Medium | Install early, not on event day; bump pins if pip starts compiling. |
| 11 | Uncommitted work not in the clone (D.10) | Medium | Commit and push first — otherwise the Pi gets a materially different app. |
| 12 | Live API keys / real data copied to the Pi (D.10) | High | Fresh Pi `.env` with zero cloud keys; fresh SQLite DB; synthetic statements only. |
| 13 | Latency perceived as a hang | Low | Extraction becomes near-instant once deterministic; only narrative waits on the model. Consider a progress indicator if a section takes >20 s. |

**Open question for you:** the `_parse_mpesa_detail_rows` regex path is now the backbone of local mode. Can you share (or point me at) a representative synthetic statement in the exact format you will demo? Its accuracy on your real layout is the single largest determinant of whether this demo lands, and it is worth verifying before I write any code.
