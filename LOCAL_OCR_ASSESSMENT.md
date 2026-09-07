# Local OCR Compatibility Assessment — Raspberry Pi 5

Target: Raspberry Pi 5, 4 GB RAM, Debian 13 trixie, aarch64, Python 3.13
Constraint: fully local. No Baidu Cloud OCR, no external OCR/AI provider, ever.

**Status: assessment only. Nothing installed.**

All wheel/platform findings below were verified live against the PyPI JSON API
on 2026-09-07, not recalled. Performance figures are estimates and are marked
as such — they have not been measured on your Pi.

---

## RECOMMENDATION: C — a lightweight fully-local OCR approach

**Use RapidOCR (PP-OCR models on ONNX Runtime). Do not install PaddlePaddle.**

This is not a rejection of PaddleOCR's models — it is the opposite. RapidOCR
runs *the same Baidu PP-OCR model family*, exported to ONNX, on a runtime that
actually has aarch64 + Python 3.13 wheels. You keep PaddleOCR-class accuracy and
drop the part that does not fit this Pi: the PaddlePaddle framework itself.

Tesseract is the credible fallback if you want maximum installation simplicity
and are willing to accept lower accuracy on dense numeric tables.

---

## 1–4. PaddlePaddle / PaddleOCR on aarch64 + Python 3.13

This is where PaddleOCR fails, and it fails on a detail that would not be
obvious until install day.

**PaddlePaddle dropped Linux aarch64 wheels in its current release.**

| Version | cp313 + linux aarch64 wheel? | Wheel size |
|---|---|---|
| 3.3.1 (latest) | **No — no aarch64 wheels at all** | — |
| 3.3.0 | **No — no aarch64 wheels at all** | — |
| 3.2.2 | Yes | 88 MB |
| 3.2.1 | Yes | 88 MB |
| 3.2.0 | Yes | 88 MB |
| 3.1.1 | Yes | 87 MB |
| 3.1.0 | Yes | 94 MB |
| 3.0.0 | Yes | 92 MB |
| 2.6.2 | aarch64 yes, but **cp38–cp312 only — no 3.13** | — |

Answers to your questions 1–4:

1. **Usable aarch64 support on the current stable release: no.** 3.3.0 and 3.3.1
   publish zero Linux aarch64 wheels. Only a pinned `paddlepaddle==3.2.2` (or
   older 3.x) works.
2. **Python 3.13 supported: yes, but only in the same narrow window** — 3.0.0
   through 3.2.2. The last version with aarch64 *and* cp313 is 3.2.2.
3. **Prebuilt aarch64 wheels exist: only for pinned older versions.**
4. **Source compilation risk: this is the dangerous part.** `paddlepaddle` publishes
   **no sdist**. So a plain `pip install paddlepaddle` on your Pi does not fall
   back to a source build — it fails outright with "no matching distribution".
   You would not get a slow compile; you would get a hard failure. The fix is
   `paddlepaddle==3.2.2`, but that leaves you pinned to a version the project has
   already stopped shipping for your architecture. Any future `pip install -U`
   silently breaks the deployment.

On top of the runtime, `paddleocr` 3.7.0 depends on `paddlex[ocr-core]` — a full
ML pipeline framework, not a thin OCR wrapper. That is a large additional
dependency surface for one feature.

**Verdict on PaddleOCR: viable only in a pinned, already-deprecated
configuration. That is the wrong footing for a live banking demo.**

---

## RapidOCR — the same models, a runtime that fits

Verified facts:

- `rapidocr` 3.9.2 is a **pure-Python `py3-none-any` wheel** (27.3 MB compressed,
  32.4 MB installed) with `requires_python = >=3.8,<4` — Python 3.13 is fine.
- Its inference backend, `onnxruntime` 1.29.0, publishes
  **`cp313` + `manylinux_2_28_aarch64`** wheels. Debian 13 (glibc 2.41) satisfies
  manylinux_2_28 comfortably.
- Dependencies are light and all aarch64-available: pyclipper, opencv, numpy,
  shapely, PyYAML, Pillow, tqdm, omegaconf, requests, colorlog.

> Note: the older `rapidocr-onnxruntime` package caps at `<3.13` and is **not**
> usable here. The current `rapidocr` package is the one that works.

---

## 5. Disk and RAM

| Component | Disk | Notes |
|---|---|---|
| rapidocr wheel (models included) | ~32 MB | |
| onnxruntime aarch64 | ~15–20 MB | |
| opencv (use `opencv-python-headless`) | ~40–60 MB | headless avoids GUI libs |
| numpy, shapely, pyclipper, misc | ~40 MB | |
| **Total** | **~150 MB** | vs. ~1 GB+ for PaddlePaddle + PaddleX |

Estimated RAM during inference: **~300–600 MB peak** for the mobile/small models
at typical page resolution — an estimate, not a measurement. Against your 47 GB
free disk, disk is a non-issue either way; RAM is the real budget.

---

## 6. Expected CPU performance (estimate)

Cortex-A76 @ 2.4 GHz, 4 cores, ONNX Runtime CPU, one A4 page at 200–300 DPI:
roughly **3–8 seconds per page** with the small det+rec models, assuming OCR gets
2–3 threads while llama-server holds its 4.

For a demo statement of 2–5 pages that is ~15–40 seconds. Acceptable if the UI
shows progress; unpleasant as a silent wait. **Unverified — measure before
relying on it.**

---

## 7. Coexistence on 4 GB

Rough budget with everything resident:

| Process | Estimate |
|---|---|
| llama-server + Qwen2.5-0.5B Q4_K_M | ~0.6–0.9 GB |
| Django KPI app | ~0.2–0.3 GB |
| Flask AIoT dashboard | ~0.1–0.2 GB |
| OS | ~0.5–0.8 GB |
| **Subtotal** | **~1.4–2.2 GB** |
| OCR peak (transient) | +0.3–0.6 GB |

It fits, but only if OCR is transient (see 11) and CPU contention is managed.
The bigger risk is not RAM but **CPU**: OCR and llama-server competing for the
same 4 cores. Since the pipeline is sequential — OCR finishes, *then* the model
writes narrative — they should rarely overlap. Do not run them concurrently.

PaddlePaddle, by contrast, would put a second full ML framework into that budget.

---

## 8. Smallest appropriate configuration for English bank statements

The RapidOCR wheel **bundles** these models, which is exactly what you want:

| Model | Size | Role |
|---|---|---|
| `PP-OCRv6_det_small.onnx` | 9.9 MB | text detection |
| `PP-OCRv6_rec_small.onnx` | 21.2 MB | text recognition |
| `ch_ppocr_mobile_v2.0_cls_mobile.onnx` | 0.6 MB | orientation classification |

The `_small` PP-OCRv6 pair is the right choice: smallest footprint, and the
PP-OCR recognition models handle Latin script and digits well. Angle
classification can be disabled for scanner-flat statements to save a pass.

---

## 9 & 10. Zero-internet operation

**Verified and this is the decisive advantage:** the three default models ship
*inside* the wheel at `rapidocr/models/`. With the default configuration there is
no first-run download at all.

The caveat, also verified: `rapidocr/default_models.yaml` contains ~300 remote
`modelscope.cn` URLs for the *other*, non-bundled model variants. Selecting a
non-default model would trigger a download. So:

1. Use the bundled defaults, and pass explicit local `model_path` values rather
   than model *names*, so no resolution against that YAML can occur.
2. Belt and braces: the app already runs with `LOCAL_AI_MODE=true`; add the same
   discipline here and verify with the blackhole-route test already in the audit
   plan. If OCR works with all outbound traffic blocked, it is offline. That test
   is the only proof worth accepting.

---

## 11. Load-on-demand and release

Yes, and it should be mandatory on 4 GB:

- Construct the OCR engine lazily, inside the function that needs it — never at
  Django import time, or every worker pays the RAM permanently.
- Drop the reference and `gc.collect()` after the statement is processed;
  ONNX Runtime releases its arenas when the session is freed.
- Because uploads already run through the django-q worker, the natural design is
  to build and destroy the engine inside the task. A single worker
  (`Q_WORKERS=1`, already configured) guarantees only one OCR session can exist.

---

## 12. Comparison for this specific use case

| | RapidOCR (PP-OCR/ONNX) | PaddleOCR (PaddlePaddle) | Tesseract |
|---|---|---|---|
| Accuracy on statement tables | **High** — PP-OCR models | **High** — same models | Moderate; weaker on dense numeric columns |
| aarch64 + Py3.13 | **Yes, current release** | Only pinned ≤3.2.2, dropped since | **Yes** (apt) |
| Install risk | Low — pure-Python wheel + ORT | **High** — no sdist, hard failure unpinned | **Lowest** — `apt install tesseract-ocr` |
| Disk | ~150 MB | ~1 GB+ | ~50 MB |
| Peak RAM | ~300–600 MB | ~600 MB–1 GB+ | ~100–200 MB |
| Speed/page (est.) | 3–8 s | 4–10 s | **1–3 s** |
| Models offline | **Bundled in wheel** | Downloads on first use — must pre-seed | Bundled in apt package |
| Future-proof | Good | Poor for aarch64 | Good |

Against your stated priorities:

1. **Accuracy** → RapidOCR = PaddleOCR > Tesseract
2. **Fully local** → all three, once configured
3. **Demo reliability** → RapidOCR > Tesseract > PaddleOCR (pinned-deprecated is a liability)
4. **4 GB RAM** → Tesseract > RapidOCR > PaddleOCR
5. **Install simplicity** → Tesseract > RapidOCR > PaddleOCR
6. **Speed** → Tesseract > RapidOCR ≈ PaddleOCR

RapidOCR wins on the two you ranked first and third; Tesseract wins the ones you
ranked lower. Since accuracy on *financial figures* is the thing that decides
whether the demo is trustworthy, RapidOCR is the right trade.

---

## Minimum integration architecture (if approved)

Your target pipeline, with OCR strictly as a text source:

```
digital PDF ──► PyMuPDF/PyPDF2 text ──┐
                                       ├─► deterministic transaction parser
scanned PDF ──► local OCR ──► text ───┘        (pdf_extractor.py, unchanged)
                                                        │
                                                        ▼
                                          deterministic KPI calculation
                                             (calculate_kpis, unchanged)
                                                        │
                                                        ▼
                                        local Qwen — narrative interpretation only
```

OCR produces **text**, nothing else. It never sees a KPI and never produces a
number. It is a substitute for `ocr_pdf_pages()`, sitting in exactly the slot
Claude Vision occupies today.

Proposed minimal changes — 3 files, no redesign:

| File | Change |
|---|---|
| `kpi/local_ocr.py` (new) | `ocr_pages_locally(images) -> str \| None`; lazy engine build, explicit local model paths, release + `gc.collect()` after use |
| `kpi/ai_agent.py` | In `ocr_pdf_pages()`, when local mode: delegate to `local_ocr` instead of returning None |
| `kpi/views.py` | Re-enable page rendering in local mode *only* when local OCR is available; keep the current explicit error when it is not |
| `requirements-pi.txt` (new) | `rapidocr`, `onnxruntime`, `opencv-python-headless` — kept out of the main requirements so cloud deployments stay lean |

Config: `LOCAL_OCR_ENABLED` (default false) and `LOCAL_OCR_MODEL_DIR`, following
the `LOCAL_AI_*` convention already established. Default false means today's
behaviour — explicit "OCR unavailable" — is unchanged until you switch it on.

Tests to add: OCR never invoked when `LOCAL_OCR_ENABLED=false`; no network call
during OCR; extraction still deterministic on OCR output; engine released after use.

---

## Honest caveats

- Every performance and RAM figure here is an **estimate**. The only numbers
  worth trusting come from your Pi.
- OCR accuracy on *your* statement layout is unverified. OCR output feeds the
  same regex parsers, so a layout the parser already struggles with will not be
  rescued by OCR — it may be made worse by OCR noise.
- Recommended proving order, before any integration work: install into a venv on
  the Pi, OCR one representative scanned statement, run the blackhole test, and
  time it. If accuracy on your layout is poor, the answer is Tesseract or better
  source documents, not more OCR machinery.
