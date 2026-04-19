# KPIConsole — Hospital Financial Intelligence

A Django web application for hospital finance managers to import bank statements (PDF/CSV), automatically extract transactions using Claude AI, compute KPIs, and visualise financial trends over time.

---

## Features

- **Statement Import** — Upload PDF or CSV bank statements (max 25 MB). Password-protected PDFs are supported.
- **AI Extraction** — Claude Haiku extracts transactions, categories, and balances from M-PESA and generic bank statement formats.
- **KPI Engine** — Computes Revenue, Cost, Profitability, Cash Flow, Efficiency, and Financial Health metrics with thresholds and anomaly detection.
- **Analytics** — Multi-period trend charts, OLS revenue projections, benchmark comparisons, and radar performance profiles.
- **AI Assistant** — Ask plain-English questions about your statements. Answers are powered by Claude.
- **PDF Reports** — Generate downloadable PDF summaries with AI-compiled narrative.
- **Audit Trail** — Every upload, delete, reprocess, and AI query is logged.
- **Dark / Light Mode** — Manual toggle; respects OS preference by default.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Django 4.2, Python 3.11+ |
| AI | Anthropic Claude API (claude-haiku-4-5) |
| PDF extraction | PyMuPDF (fitz), PyPDF2 fallback |
| Database | SQLite (dev) / MySQL (production) |
| Frontend | Vanilla JS, Chart.js, Font Awesome |
| Fonts | Inter, JetBrains Mono |

---

## Setup (Development)

### 1. Prerequisites
- Python 3.11+
- An Anthropic API key

### 2. Clone and install

```bash
git clone <repo-url>
cd KPIsAgent
python -m venv venv
# Windows:
venv\Scripts\activate
# Mac/Linux:
source venv/bin/activate

pip install -r requirements.txt
```

### 3. Environment variables

Create a `.env` file in the project root:

```env
SECRET_KEY=your-long-random-secret-key
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
ANTHROPIC_API_KEY=sk-ant-...

# Optional: MySQL (leave blank to use SQLite)
MYSQL_DATABASE=
MYSQL_USER=
MYSQL_PASSWORD=
MYSQL_HOST=
MYSQL_PORT=

# Optional: Email (for password reset)
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
EMAIL_USE_TLS=True
DEFAULT_FROM_EMAIL=noreply@yourhospital.com
```

### 4. Database setup

```bash
python manage.py migrate
python manage.py createsuperuser
```

### 5. Run

```bash
python manage.py runserver
```

Visit `http://127.0.0.1:8000/` and sign in.

> **Note:** The superuser account has `is_staff=True`, which grants dashboard access. Regular users also need a `UserProfile` record created via `/admin/`.

---

## Creating a User

Via the Django admin at `/admin/`:

1. Create a **User** (username, email, password).
2. Under **Kpi > User profiles**, create a `UserProfile` linking to that user with a role (Director, CFO, Manager, Analyst).

---

## Project Structure

```
KPIsAgent/
├── hospital_kpi/          # Django project config
│   ├── settings.py
│   └── urls.py
├── kpi/                   # Main app
│   ├── models.py          # BankStatement, FinancialTransaction, KPIMetric, AuditLog
│   ├── views.py           # All views and business logic
│   ├── ai_agent.py        # Claude API extraction + KPI calculation
│   ├── pdf_extractor.py   # PDF/CSV text extraction + M-PESA parser
│   ├── pii_redactor.py    # PII scrubbing before sending text to Claude
│   ├── admin.py
│   ├── urls.py
│   └── migrations/
├── *.html                 # Templates (in project root)
├── media/                 # Uploaded statements (gitignored)
└── manage.py
```

---

## Data Flow

```
Upload PDF/CSV
     │
     ▼
pdf_extractor.py  ──►  raw text / CSV rows
     │
     ▼
pii_redactor.py   ──►  PII removed from text
     │
     ▼
ai_agent.py       ──►  Claude Haiku extracts transactions (15k char batches)
     │
     ▼
views.py          ──►  saves FinancialTransaction + KPIMetric rows
     │
     ▼
Templates         ──►  charts, tables, AI assistant
```

---

## Key URLs

| URL | Description |
|---|---|
| `/` | Sign in |
| `/dashboard/` | Overview charts and summary cards |
| `/statements/` | Statement library with date/status filter |
| `/statement/<id>/` | Detail: KPIs, transactions, AI analyses |
| `/comparison/` | Multi-period trend analytics |
| `/assistant/` | AI question-answer interface |
| `/download-report/` | Generate PDF report |
| `/health/` | Health check endpoint |
| `/admin/` | Django admin |
| `/password-reset/` | Self-service password reset |

---

## Environment Variables Reference

| Variable | Required | Description |
|---|---|---|
| `SECRET_KEY` | Yes | Django secret key (50+ chars) |
| `DEBUG` | No | Enable debug mode (default: False) |
| `ALLOWED_HOSTS` | Yes | Comma-separated allowed hostnames |
| `ANTHROPIC_API_KEY` | Yes | Claude API key |
| `MYSQL_DATABASE` | No | Use MySQL if set; falls back to SQLite |
| `EMAIL_HOST` | No | SMTP server for password reset emails |

---

## Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+U` / `Cmd+U` | Open upload modal |
| `Escape` | Close any open modal |
| `Enter` (AI chat) | Send question |
| `Shift+Enter` (AI chat) | New line |

---

## Known Limitations

- **SQLite** — Not suitable for concurrent production use; configure MySQL via env vars.
- **Synchronous processing** — Statement upload blocks the HTTP request during AI extraction (30–90 s for large PDFs). A Celery/Django-Q task queue would fix this.
- **No caching** — KPI views recompute on every request. Add Redis for production.
- **Single currency** — Hardcoded to KES. Change `CURRENCY_SYMBOL` in `views.py`.
- **Email** — Password reset emails require a configured `EMAIL_HOST` in `.env`; without it they go to the console.
