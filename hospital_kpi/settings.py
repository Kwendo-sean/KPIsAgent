"""
Django settings for the hospital_kpi project.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.environ.get("SECRET_KEY", "hospital-kpi-secret-key-change-in-production")
DEBUG = os.environ.get("DEBUG", "True").lower() == "true"
ALLOWED_HOSTS = [host.strip() for host in os.environ.get("ALLOWED_HOSTS", "*").split(",") if host.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "django_q",
    "kpi",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "hospital_kpi.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "hospital_kpi.wsgi.application"

if os.environ.get("MYSQL_DATABASE"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": os.environ.get("MYSQL_DATABASE"),
            "USER": os.environ.get("MYSQL_USER", "root"),
            "PASSWORD": os.environ.get("MYSQL_PASSWORD", ""),
            "HOST": os.environ.get("MYSQL_HOST", "127.0.0.1"),
            "PORT": os.environ.get("MYSQL_PORT", "3306"),
            "OPTIONS": {"charset": "utf8mb4"},
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").exists() else []

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
}

CORS_ALLOWED_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
] + [o.strip() for o in os.environ.get("EXTRA_CORS_ORIGINS", "").split(",") if o.strip()]

# Django 4.2 requires an explicit trusted origin for any non-localhost host, or
# every POST (upload, AJAX) fails CSRF validation. The Pi serves the app at
# http://10.42.0.1:8090, so that origin must be listed there via CSRF_TRUSTED_ORIGINS.
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.environ.get(
        "CSRF_TRUSTED_ORIGINS",
        "http://localhost:8000,http://127.0.0.1:8000",
    ).split(",") if o.strip()
]

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
# Legacy — kept so .env files with the old key still load without crashing
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

# ── Local AI mode (edge deployment: llama.cpp on loopback) ────────────────────
# When true, ALL inference goes to a local llama-server and no external AI
# provider may be contacted under any circumstance. Defaults to false so
# existing cloud deployments are completely unaffected.
LOCAL_AI_MODE = os.environ.get("LOCAL_AI_MODE", "false").strip().lower() in ("1", "true", "yes", "on")
LOCAL_AI_BASE_URL = os.environ.get("LOCAL_AI_BASE_URL", "http://127.0.0.1:8081/v1").strip()
LOCAL_AI_MODEL = os.environ.get("LOCAL_AI_MODEL", "gemma-3-1b-it").strip()
LOCAL_AI_TIMEOUT = int(os.environ.get("LOCAL_AI_TIMEOUT", "120"))
# Ceiling on generated tokens — the local server's context is 1024.
LOCAL_AI_MAX_TOKENS = int(os.environ.get("LOCAL_AI_MAX_TOKENS", "256"))

# ── Local OCR (RapidOCR + ONNX Runtime CPU, bundled PP-OCR models) ────────────
# Only consulted when LOCAL_AI_MODE is also true. Default false, so neither
# cloud deployments nor a local-AI deployment without the OCR stack are affected.
LOCAL_OCR_ENABLED = os.environ.get("LOCAL_OCR_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")
# Blank means "use the models bundled inside the installed rapidocr package".
# Models are never downloaded at runtime; a missing file is an explicit error.
LOCAL_OCR_MODEL_DIR = os.environ.get("LOCAL_OCR_MODEL_DIR", "").strip()
# Leave cores free for llama-server (which uses 4 threads).
LOCAL_OCR_THREADS = int(os.environ.get("LOCAL_OCR_THREADS", "2"))
LOCAL_OCR_MAX_PAGES = int(os.environ.get("LOCAL_OCR_MAX_PAGES", "20"))

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"

# ── django-q2 Task Queue (DB-backed, no Redis required) ───────────────────────
Q_CLUSTER = {
    "name": "hospital_kpi",
    "secret_key": "hospital-kpi-queue-v1",  # explicit key — never derived from SECRET_KEY
    # One worker on constrained edge hardware (4 GB Pi shared with llama-server).
    "workers": int(os.environ.get("Q_WORKERS", "1" if LOCAL_AI_MODE else "2")),
    "timeout": 86400,   # 24 hours — tasks always finish regardless of file size
    "retry": 90000,     # must be > timeout to prevent re-trigger before completion
    "queue_limit": 50,
    "bulk": 10,
    "orm": "default",  # Use Django ORM as broker
    "sync": False,     # Set True only in tests to run tasks synchronously
}

# ── Logging ───────────────────────────────────────────────────────────────────
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{asctime} [{levelname}] {name}: {message}",
            "style": "{",
            "datefmt": "%H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "loggers": {
        # Our app — show INFO and above (includes batch progress, AI provider used, errors)
        "kpi": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        # django-q worker — show INFO so you see task start/done/fail
        "django_q": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        # Django itself — warnings and above only (avoids SQL noise)
        "django": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}

# ── Database Cache (avoids Redis while improving dashboard performance) ────────
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "cache_table",
        "TIMEOUT": 300,  # 5 minutes
        "OPTIONS": {"MAX_ENTRIES": 1000},
    }
}

# ── Email (password reset + alert notifications) ──────────────────────────────
EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend",  # prints to console if not configured
)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", 587))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "True").lower() == "true"
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "noreply@hospital.com")

# ── Multi-currency: base currency used in UI ──────────────────────────────────
BASE_CURRENCY = os.environ.get("BASE_CURRENCY", "KES")
SUPPORTED_CURRENCIES = ["KES", "USD", "EUR", "GBP", "UGX", "TZS", "ETB", "NGN", "ZAR", "GHS"]

# ── PWA settings ──────────────────────────────────────────────────────────────
PWA_APP_NAME = "KPIConsole"
PWA_APP_DESCRIPTION = "Hospital Financial Intelligence"
PWA_APP_THEME_COLOR = "#1A4D3E"
PWA_APP_BACKGROUND_COLOR = "#F7F8F5"
