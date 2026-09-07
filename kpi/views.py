import base64
import json
import logging
import os
import secrets
import string

from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.conf import settings as django_settings
from django.db.models import Sum, Avg, Q
from django.http import JsonResponse, HttpResponseForbidden, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.template.loader import get_template, render_to_string
from django.views.decorators.http import require_POST, require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django_ratelimit.decorators import ratelimit
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status as drf_status
import datetime
from django.utils import timezone
import markdown
import csv
from io import StringIO, BytesIO
from decimal import Decimal, InvalidOperation
from xhtml2pdf import pisa

from .models import (
    BankStatement, KPIMetric, AIAnalysis, FinancialTransaction,
    UserProfile, AuditLog, BudgetTarget, StatementTag, StatementTagging,
    TwoFactorProfile, SubAccount,
)
from .pdf_extractor import BankStatementPDFExtractor, PDFPasswordRequired, PDFWrongPassword
from .ai_agent import HospitalKPIAgent
from .industry_config import (
    INDUSTRY_CHOICES, INDUSTRY_SECTORS, get_industry_label, get_ai_context,
)

logger = logging.getLogger("kpi.views")

# Maximum upload size: 25 MB
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024
# Maximum question length sent to AI
_MAX_QUESTION_LEN = 2000


def _get_client_ip(request):
    x_forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded:
        return x_forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _audit(request, action: str, detail: str = "", user=None):
    try:
        resolved_user = user or (request.user if request.user.is_authenticated else None)
        AuditLog.objects.create(
            user=resolved_user,
            action=action,
            detail=detail[:500],
            ip_address=_get_client_ip(request),
        )
    except Exception:
        pass

CURRENCY_SYMBOL = "KES"


def _active_account(request):
    """Return (sub_account_or_None, industry_key) for the current session."""
    sub_id = request.session.get("active_sub_account_id")
    if sub_id:
        try:
            sub = SubAccount.objects.get(pk=sub_id, owner=request.user, is_active=True)
            return sub, sub.industry
        except SubAccount.DoesNotExist:
            request.session.pop("active_sub_account_id", None)
    industry = "HOSPITAL"
    try:
        industry = request.user.userprofile.industry or "HOSPITAL"
    except Exception:
        pass
    return None, industry


def _account_qs(request):
    """Return a BankStatement queryset filtered to the active account."""
    sub, _ = _active_account(request)
    if sub:
        return BankStatement.objects.filter(uploaded_by=request.user, sub_account=sub)
    return BankStatement.objects.filter(uploaded_by=request.user, sub_account__isnull=True)


def _base_context(request):
    """Return sidebar counts and account context used on every page."""
    qs = _account_qs(request)
    total = qs.count()
    processed = qs.filter(is_processed=True).count()
    sub, industry = _active_account(request)
    sub_accounts = list(SubAccount.objects.filter(owner=request.user, is_active=True))
    org_name = ""
    try:
        org_name = sub.name if sub else (request.user.userprofile.organization_name or request.user.get_full_name() or request.user.username)
    except Exception:
        org_name = request.user.get_full_name() or request.user.username
    return {
        "total_statements": total,
        "processed_statements": processed,
        "currency_symbol": CURRENCY_SYMBOL,
        "active_sub_account": sub,
        "active_industry": industry,
        "active_industry_label": get_industry_label(industry),
        "active_org_name": org_name,
        "sub_accounts": sub_accounts,
    }


# ──────────────────────────────────────────────
# Landing page
# ──────────────────────────────────────────────

def landing_view(request):
    """Public landing page — redirect to dashboard if already logged in."""
    if request.user.is_authenticated:
        return redirect("dashboard")
    return render(request, "landing.html", {
        "industry_sectors": INDUSTRY_SECTORS,
    })


# ──────────────────────────────────────────────
# Auth
# ──────────────────────────────────────────────

@ratelimit(key='ip', rate='10/m', method='POST', block=False)
def login_view(request):
    """Manager login page. Redirects to 2FA verification if the user has it enabled."""
    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":
        if getattr(request, 'limited', False):
            return render(request, "login.html", {"error": "Too many login attempts. Please try again later."})
        username = request.POST.get("username")
        password = request.POST.get("password")
        user = authenticate(request, username=username, password=password)

        if user is not None:
            # Check if 2FA is enabled for this user
            try:
                tf = TwoFactorProfile.objects.get(user=user, is_enabled=True)
                if tf.totp_secret:
                    # Stash the user id and send to verification step (don't log in yet)
                    request.session["2fa_pending_user"] = user.pk
                    request.session["2fa_backend"] = user.backend if hasattr(user, "backend") else "django.contrib.auth.backends.ModelBackend"
                    return redirect("two_factor_verify")
            except TwoFactorProfile.DoesNotExist:
                pass

            login(request, user)
            _audit(request, "LOGIN", f"User {user.username} signed in", user=user)
            return redirect("dashboard")
        else:
            return render(request, "login.html", {"error": "Invalid credentials."})

    return render(request, "login.html")


@ratelimit(key='ip', rate='10/m', method='POST', block=False)
def two_factor_verify_view(request):
    """Second step of login: validate TOTP code or backup code."""
    if request.method == "POST" and getattr(request, 'limited', False):
        return render(request, "two_factor_verify.html", {"error": "Too many attempts. Please try again later."})
    import pyotp

    user_id = request.session.get("2fa_pending_user")
    if not user_id:
        return redirect("login")

    from django.contrib.auth import get_user_model
    User = get_user_model()
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        del request.session["2fa_pending_user"]
        return redirect("login")

    try:
        tf = TwoFactorProfile.objects.get(user=user, is_enabled=True)
    except TwoFactorProfile.DoesNotExist:
        # 2FA was disabled between login steps — just let them in
        del request.session["2fa_pending_user"]
        login(request, user, backend=request.session.pop("2fa_backend", "django.contrib.auth.backends.ModelBackend"))
        return redirect("dashboard")

    error = None
    if request.method == "POST":
        code = request.POST.get("code", "").strip().replace(" ", "")

        # Check TOTP
        totp = pyotp.TOTP(tf.totp_secret)
        if totp.verify(code, valid_window=1):
            del request.session["2fa_pending_user"]
            backend = request.session.pop("2fa_backend", "django.contrib.auth.backends.ModelBackend")
            login(request, user, backend=backend)
            _audit(request, "LOGIN", f"User {user.username} signed in (2FA)", user=user)
            return redirect("dashboard")

        # Check backup codes
        backup_codes = list(tf.backup_codes or [])
        if code.upper() in [c.upper() for c in backup_codes]:
            backup_codes = [c for c in backup_codes if c.upper() != code.upper()]
            tf.backup_codes = backup_codes
            tf.save(update_fields=["backup_codes"])
            del request.session["2fa_pending_user"]
            backend = request.session.pop("2fa_backend", "django.contrib.auth.backends.ModelBackend")
            login(request, user, backend=backend)
            _audit(request, "LOGIN", f"User {user.username} signed in (2FA backup code used)", user=user)
            return redirect("dashboard")

        error = "Invalid code. Please try again."

    return render(request, "two_factor_verify.html", {"error": error, "username": user.username})


def logout_view(request):
    """Logout user."""
    logout(request)
    return redirect("login")


@ratelimit(key='ip', rate='5/m', method='POST', block=False)
def register_view(request):
    """Multi-step signup: create user + industry-aware profile."""
    if request.user.is_authenticated:
        return redirect("dashboard")

    errors = {}
    form_data = {}

    if request.method == "POST":
        if getattr(request, 'limited', False):
            errors["rate_limit"] = "Too many registration attempts. Please try again later."
        # Step fields
        first_name     = request.POST.get("first_name", "").strip()
        last_name      = request.POST.get("last_name", "").strip()
        username       = request.POST.get("username", "").strip()
        email          = request.POST.get("email", "").strip()
        password       = request.POST.get("password", "")
        password2      = request.POST.get("password2", "")
        org_name       = request.POST.get("organization_name", "").strip()
        industry       = request.POST.get("industry", "HOSPITAL")
        role           = request.POST.get("role", "MANAGER")
        phone          = request.POST.get("phone", "").strip()

        form_data = {
            "first_name": first_name, "last_name": last_name,
            "username": username, "email": email,
            "organization_name": org_name, "industry": industry,
            "role": role, "phone": phone,
        }

        # Validation
        if not username:
            errors["username"] = "Username is required."
        elif User.objects.filter(username=username).exists():
            errors["username"] = "This username is already taken."
        if not email:
            errors["email"] = "Email is required."
        elif User.objects.filter(email=email).exists():
            errors["email"] = "An account with this email already exists."
        if not password:
            errors["password"] = "Password is required."
        elif len(password) < 8:
            errors["password"] = "Password must be at least 8 characters."
        elif password != password2:
            errors["password2"] = "Passwords do not match."
        if not org_name:
            errors["organization_name"] = "Organization name is required."

        if not errors:
            user = User.objects.create_user(
                username=username, email=email, password=password,
                first_name=first_name, last_name=last_name,
            )
            UserProfile.objects.create(
                user=user, role=role, industry=industry,
                organization_name=org_name, phone=phone,
            )
            login(request, user)
            _audit(request, "LOGIN", f"New account registered: {username}", user=user)
            return redirect("dashboard")

    return render(request, "register.html", {
        "errors": errors,
        "form_data": form_data,
        "industry_sectors": INDUSTRY_SECTORS,
    })


# ──────────────────────────────────────────────
# Dashboard
# ──────────────────────────────────────────────

@login_required(login_url="login")
def dashboard_view(request):
    """Main KPI dashboard."""
    import re as _re
    bank_statements = _account_qs(request).order_by("-upload_date")

    # ── Filter / scope params ─────────────────────────────────────
    filter_mode         = request.GET.get("filter_mode", "latest")
    filter_statement_id = request.GET.get("statement_id", "")
    filter_from         = request.GET.get("from_date", "")
    filter_to           = request.GET.get("to_date", "")
    summary_type        = request.GET.get("summary_type", "overview")

    processed_qs = bank_statements.filter(is_processed=True)

    if filter_mode == "statement" and filter_statement_id:
        try:
            sel_qs = processed_qs.filter(id=int(filter_statement_id))
        except (ValueError, TypeError):
            sel_qs = processed_qs.none()
    elif filter_mode == "range":
        sel_qs = processed_qs
        if filter_from:
            try:
                sel_qs = sel_qs.filter(statement_period_start__gte=datetime.date.fromisoformat(filter_from))
            except ValueError:
                pass
        if filter_to:
            try:
                sel_qs = sel_qs.filter(statement_period_end__lte=datetime.date.fromisoformat(filter_to))
            except ValueError:
                pass
    elif filter_mode == "all":
        sel_qs = processed_qs
    else:
        filter_mode = "latest"
        _first = processed_qs.first()
        sel_qs = processed_qs.filter(id=_first.id) if _first else processed_qs.none()

    selected_list   = list(sel_qs.order_by("statement_period_start", "upload_date"))
    latest_selected = selected_list[-1] if selected_list else None

    # Dropdown options for "Specific Statement" mode
    all_processed_asc = list(processed_qs.order_by("statement_period_start", "upload_date"))
    filter_statement_options = [
        {
            "id": s.id,
            "label": (
                f"{s.statement_period_start.strftime('%b %Y') if s.statement_period_start else s.upload_date.strftime('%b %Y')}"
                f" — {s.file_name}"
            ),
            "selected": str(s.id) == filter_statement_id,
        }
        for s in all_processed_asc
    ]

    kpis = []
    alerts = []
    overview_chart_labels = []
    overview_revenue_series = []
    overview_expense_series = []
    expense_labels = []
    expense_values = []
    summary_cards = []
    statement_rows = []
    detailed_summaries = []
    h_score, h_tier, h_tone = 0, "—", "neutral"
    summary_options = [
        {"key": "overview",             "label": "Overview",             "icon": "fa-chart-pie"},
        {"key": "revenue_sources",      "label": "Revenue Sources",      "icon": "fa-money-bill-trend-up"},
        {"key": "expense_breakdown",    "label": "Expense Breakdown",    "icon": "fa-receipt"},
        {"key": "transaction_patterns", "label": "Transaction Patterns", "icon": "fa-clock-rotate-left"},
        {"key": "top_insights",         "label": "Top Insights",         "icon": "fa-lightbulb"},
    ]

    if latest_selected:
        kpis   = KPIMetric.objects.filter(bank_statement=latest_selected)
        alerts = AIAnalysis.objects.filter(
            bank_statement__in=sel_qs,
            analysis_type__in=["ALERT", "RECOMMENDATION"],
        ).order_by("-created_at")[:5]

        # Chart uses selected statements only
        overview_chart_labels   = [
            s.statement_period_start.strftime("%b %Y") if s.statement_period_start else s.upload_date.strftime("%b %Y")
            for s in selected_list
        ]
        overview_revenue_series = [float(getattr(s, "total_deposits",    0) or 0) for s in selected_list]
        overview_expense_series = [float(getattr(s, "total_withdrawals", 0) or 0) for s in selected_list]

        # Expense mix across all selected statements
        expenses = FinancialTransaction.objects.filter(
            bank_statement__in=sel_qs, transaction_type="WITHDRAWAL"
        )
        expense_map: dict = {}
        for tx in expenses:
            cat = tx.category or (tx.description.split()[0] if tx.description else "Other")
            expense_map[cat] = expense_map.get(cat, 0) + float(tx.amount or 0)
        expense_labels = list(expense_map.keys())[:10]
        expense_values = [expense_map[k] for k in expense_labels]

        # Aggregate totals from selected statements
        total_deposits    = sum(float(getattr(s, "total_deposits",    0) or 0) for s in selected_list)
        total_withdrawals = sum(float(getattr(s, "total_withdrawals", 0) or 0) for s in selected_list)
        net_income        = total_deposits - total_withdrawals
        closing_balance   = float(getattr(latest_selected, "closing_balance", 0) or 0)

        # Deltas only meaningful in "latest" mode
        prev_statement = (
            processed_qs.exclude(id=latest_selected.id).first()
            if filter_mode == "latest" else None
        )

        def _delta(current, previous, higher_is_better=True):
            if not previous or previous == 0:
                return None
            pct  = (current - previous) / abs(previous) * 100
            up   = pct >= 0
            good = up if higher_is_better else not up
            return {
                "pct":   abs(round(pct, 1)),
                "up":    up,
                "tone":  "delta-good" if good else "delta-bad",
                "label": f"{'▲' if up else '▼'} {abs(pct):.1f}% vs prev",
            }

        prev_deposits    = float(getattr(prev_statement, "total_deposits",    0) or 0) if prev_statement else 0
        prev_withdrawals = float(getattr(prev_statement, "total_withdrawals", 0) or 0) if prev_statement else 0
        prev_balance     = float(getattr(prev_statement, "closing_balance",   0) or 0) if prev_statement else 0
        prev_net         = prev_deposits - prev_withdrawals

        _period_meta = {
            "latest":    "latest statement",
            "all":       "all statements",
            "range":     "selected range",
            "statement": "selected statement",
        }.get(filter_mode, "selected period")

        summary_cards = [
            {
                "label": "Closing Balance",
                "value": f"{CURRENCY_SYMBOL} {closing_balance:,.2f}",
                "meta":  "most recent in selection",
                "icon":  "fa-wallet",
                "tone":  "green",
                "delta": _delta(closing_balance, prev_balance),
            },
            {
                "label": "Total Revenue",
                "value": f"{CURRENCY_SYMBOL} {total_deposits:,.2f}",
                "meta":  f"deposits · {_period_meta}",
                "icon":  "fa-arrow-trend-up",
                "tone":  "blue",
                "delta": _delta(total_deposits, prev_deposits),
            },
            {
                "label": "Total Expenses",
                "value": f"{CURRENCY_SYMBOL} {total_withdrawals:,.2f}",
                "meta":  f"withdrawals · {_period_meta}",
                "icon":  "fa-arrow-trend-down",
                "tone":  "amber",
                "delta": _delta(total_withdrawals, prev_withdrawals, higher_is_better=False),
            },
            {
                "label": "Net Income",
                "value": f"{CURRENCY_SYMBOL} {net_income:,.2f}",
                "meta":  f"net earnings · {_period_meta}",
                "icon":  "fa-chart-line",
                "tone":  "rose" if net_income < 0 else "green",
                "delta": _delta(net_income, prev_net),
            },
        ]

        # ── Financial Health Score (most recent selected statement) ─
        health_kpi = KPIMetric.objects.filter(
            bank_statement=latest_selected, metric_name="Financial Health Score"
        ).first()
        if health_kpi:
            h_score = int(float(health_kpi.current_value))
            m = _re.search(r"Tier:\s*([^.]+)", health_kpi.description or "")
            h_tier  = m.group(1).strip() if m else (
                "Stable" if h_score >= 80 else "Watchlist" if h_score >= 60
                else "At Risk" if h_score >= 40 else "Distressed" if h_score >= 20 else "Critical"
            )
        else:
            kpi_dict   = {k.metric_name.replace(" ", "_"): {"value": float(k.current_value)} for k in kpis}
            health_tmp = HospitalKPIAgent().compute_health_score(kpi_dict)
            h_score, h_tier = health_tmp["score"], health_tmp["tier"]

        if   h_score >= 80: h_tone = "green"
        elif h_score >= 60: h_tone = "amber"
        elif h_score >= 40: h_tone = "orange"
        elif h_score >= 20: h_tone = "red"
        else:               h_tone = "critical"

        # All statements for the table (always full list)
        sel_ids = {s.id for s in selected_list}
        statement_rows = [
            {
                "id":              s.id,
                "file_name":       s.file_name,
                "period":          (
                    f"{getattr(s,'statement_period_start','') or ''} – {getattr(s,'statement_period_end','') or ''}"
                ).strip(" –"),
                "closing_balance": float(getattr(s, "closing_balance", 0) or 0),
                "status":          "Processed" if getattr(s, "is_processed", False) else "Pending",
                "in_selection":    s.id in sel_ids,
            }
            for s in list(bank_statements.order_by("statement_period_start"))
        ]

        detailed_summaries = _generate_detailed_summaries(latest_selected, sel_qs, summary_type)

    ctx = _base_context(request)
    ctx.update({
        "bank_statements":          bank_statements,
        "latest_statement":         latest_selected,
        "kpis":                     kpis,
        "alerts":                   alerts,
        "overview_chart_labels":    overview_chart_labels,
        "overview_revenue_series":  overview_revenue_series,
        "overview_expense_series":  overview_expense_series,
        "expense_labels":           expense_labels,
        "expense_values":           expense_values,
        "summary_cards":            summary_cards,
        "statement_rows":           statement_rows,
        "detailed_summaries":       detailed_summaries,
        "summary_type":             summary_type,
        "summary_options":          summary_options,
        "active_page":              "overview",
        "has_statements":           bank_statements.exists(),
        "health_score":             h_score if latest_selected else 0,
        "health_tier":              h_tier  if latest_selected else "—",
        "health_tone":              h_tone  if latest_selected else "neutral",
        # Filter state passed back to template
        "filter_mode":              filter_mode,
        "filter_from":              filter_from,
        "filter_to":                filter_to,
        "filter_statement_id":      filter_statement_id,
        "filter_statement_options": filter_statement_options,
        "selected_count":           len(selected_list),
    })
    return render(request, "dashboard.html", ctx)


def _generate_detailed_summaries(latest_statement, all_statements, summary_type):
    """Generate intelligent summaries based on transaction analysis."""
    summaries = []

    # Get all transactions for analysis
    all_transactions = FinancialTransaction.objects.filter(
        bank_statement__in=all_statements
    ).order_by("-transaction_date")

    if not all_transactions.exists():
        return [{"title": "No Data", "content": "Upload statements to see detailed insights.", "icon": "fa-info-circle", "tone": "info"}]

    # Revenue sources analysis
    if summary_type in ["overview", "revenue_sources"]:
        deposits = all_transactions.filter(transaction_type="DEPOSIT")
        if deposits.exists():
            # Group by description patterns
            revenue_sources = {}
            for tx in deposits:
                desc = tx.description.upper()
                # Categorize by common keywords
                if any(word in desc for word in ["PHARMACY", "DRUG", "MEDICINE"]):
                    key = "Pharmacy Sales"
                elif any(word in desc for word in ["CONSULTATION", "DOCTOR", "VISIT", "APPOINTMENT"]):
                    key = "Consultation Fees"
                elif any(word in desc for word in ["LAB", "TEST", "LABORATORY", "DIAGNOSTIC"]):
                    key = "Laboratory Services"
                elif any(word in desc for word in ["SURGERY", "OPERATION", "PROCEDURE"]):
                    key = "Surgical Procedures"
                elif any(word in desc for word in ["ADMISSION", "ROOM", "WARD", "BED"]):
                    key = "Room & Board"
                elif any(word in desc for word in ["INSURANCE", "CLAIM", "NHIF", "MATERNITY"]):
                    key = "Insurance Claims"
                elif any(word in desc for word in ["MATERNITY", "DELIVERY", "BIRTH"]):
                    key = "Maternity Services"
                elif any(word in desc for word in ["XRAY", "X-RAY", "SCAN", "ULTRASOUND", "IMAGING", "RADIOLOGY"]):
                    key = "Radiology & Imaging"
                elif any(word in desc for word in ["DENTAL", "TOOTH", "DENTIST"]):
                    key = "Dental Services"
                elif any(word in desc for word in ["OPTICAL", "EYE", "GLASSES", "VISION"]):
                    key = "Optical Services"
                elif any(word in desc for word in ["PHYSIOTHERAPY", "PHYSIO", "REHAB", "THERAPY"]):
                    key = "Physiotherapy"
                elif any(word in desc for word in ["EMERGENCY", "CASUALTY", "ACCIDENT", "URGENT"]):
                    key = "Emergency Services"
                else:
                    key = "Other Income"

                revenue_sources[key] = revenue_sources.get(key, 0) + float(tx.amount)

            if revenue_sources:
                sorted_sources = sorted(revenue_sources.items(), key=lambda x: x[1], reverse=True)
                top_source = sorted_sources[0]
                total_revenue = sum(revenue_sources.values())
                top_percentage = (top_source[1] / total_revenue * 100) if total_revenue > 0 else 0

                summaries.append({
                    "title": "Top Revenue Source",
                    "content": f"<strong>{top_source[0]}</strong> generates the most income at <strong>KES {top_source[1]:,.2f}</strong> ({top_percentage:.1f}% of total revenue).</p>",
                    "detail": f"Other significant sources: {', '.join([f'{k} (KES {v:,.0f})' for k, v in sorted_sources[1:4]])}" if len(sorted_sources) > 1 else "",
                    "icon": "fa-money-bill-trend-up",
                    "tone": "green"
                })

                # Add revenue breakdown
                breakdown = "<ul class='summary-list'>"
                for source, amount in sorted_sources[:5]:
                    pct = (amount / total_revenue * 100) if total_revenue > 0 else 0
                    breakdown += f"<li><span class='source-name'>{source}</span><span class='source-amount'>KES {amount:,.2f} ({pct:.1f}%)</span></li>"
                breakdown += "</ul>"
                summaries.append({
                    "title": "Revenue Breakdown",
                    "content": breakdown,
                    "icon": "fa-chart-pie",
                    "tone": "blue",
                    "is_html": True
                })

    # Expense breakdown analysis
    if summary_type in ["overview", "expense_breakdown"]:
        withdrawals = all_transactions.filter(transaction_type="WITHDRAWAL")
        if withdrawals.exists():
            expense_categories = {}
            for tx in withdrawals:
                desc = tx.description.upper()
                if any(word in desc for word in ["SALARY", "PAYROLL", "WAGE", "STAFF", "EMPLOYEE"]):
                    key = "Staff Salaries"
                elif any(word in desc for word in ["SUPPLIES", "MEDICAL", "EQUIPMENT", "INVENTORY", "STOCK"]):
                    key = "Medical Supplies"
                elif any(word in desc for word in ["UTILITY", "ELECTRICITY", "WATER", "POWER", "KPLC", " Nairobi Water"]):
                    key = "Utilities"
                elif any(word in desc for word in ["RENT", "LEASE", "PREMISES", "BUILDING"]):
                    key = "Rent & Premises"
                elif any(word in desc for word in ["MAINTENANCE", "REPAIR", "SERVICE", "FIX"]):
                    key = "Maintenance & Repairs"
                elif any(word in desc for word in ["TRANSPORT", "FUEL", "VEHICLE", "PETROL", "DIESEL"]):
                    key = "Transport & Fuel"
                elif any(word in desc for word in ["COMMUNICATION", "PHONE", "INTERNET", "DATA", "AIRTIME"]):
                    key = "Communications"
                elif any(word in desc for word in ["INSURANCE", "PREMIUM", "COVER", "POLICY"]):
                    key = "Insurance"
                elif any(word in desc for word in ["TAX", "KRA", "VAT", "PAYE", "NSSF", "NHIF", "LEVY"]):
                    key = "Taxes & Statutory"
                elif any(word in desc for word in ["MARKETING", "ADVERTISING", "PROMOTION", "ADVERT"]):
                    key = "Marketing"
                elif any(word in desc for word in ["BANK", "CHARGES", "FEES", "INTEREST", "LOAN"]):
                    key = "Bank Charges & Interest"
                elif any(word in desc for word in ["PROFESSIONAL", "LEGAL", "ACCOUNTANT", "AUDIT", "CONSULTANT"]):
                    key = "Professional Services"
                elif any(word in desc for word in ["CLEANING", "SANITATION", "WASTE"]):
                    key = "Cleaning & Sanitation"
                elif any(word in desc for word in ["SECURITY", "GUARD"]):
                    key = "Security Services"
                elif any(word in desc for word in ["CATERING", "FOOD", "MEALS", "CANTEEN"]):
                    key = "Catering & Food"
                else:
                    key = "Other Expenses"

                expense_categories[key] = expense_categories.get(key, 0) + float(tx.amount)

            if expense_categories:
                sorted_expenses = sorted(expense_categories.items(), key=lambda x: x[1], reverse=True)
                top_expense = sorted_expenses[0]
                total_expenses = sum(expense_categories.values())
                expense_percentage = (top_expense[1] / total_expenses * 100) if total_expenses > 0 else 0

                summaries.append({
                    "title": "Top Expense Category",
                    "content": f"<strong>{top_expense[0]}</strong> is your highest expense at <strong>KES {top_expense[1]:,.2f}</strong> ({expense_percentage:.1f}% of total expenses).",
                    "detail": f"This represents a significant portion of your operating costs.",
                    "icon": "fa-receipt",
                    "tone": "amber"
                })

                # Show expense breakdown
                breakdown = "<ul class='summary-list expense'>"
                for category, amount in sorted_expenses[:5]:
                    pct = (amount / total_expenses * 100) if total_expenses > 0 else 0
                    breakdown += f"<li><span class='source-name'>{category}</span><span class='source-amount'>KES {amount:,.2f} ({pct:.1f}%)</span></li>"
                breakdown += "</ul>"
                summaries.append({
                    "title": "Expense Distribution",
                    "content": breakdown,
                    "icon": "fa-chart-column",
                    "tone": "rose",
                    "is_html": True
                })

    # Transaction patterns analysis
    if summary_type in ["overview", "transaction_patterns"]:
        tx_count = all_transactions.count()
        if tx_count > 0:
            deposits = all_transactions.filter(transaction_type="DEPOSIT")
            withdrawals = all_transactions.filter(transaction_type="WITHDRAWAL")

            avg_deposit = deposits.aggregate(Avg("amount"))["amount__avg"] or 0
            avg_withdrawal = withdrawals.aggregate(Avg("amount"))["amount__avg"] or 0

            # Find largest transactions
            largest_deposit = deposits.order_by("-amount").first()
            largest_withdrawal = withdrawals.order_by("-amount").first()

            summaries.append({
                "title": "Transaction Overview",
                "content": f"You've processed <strong>{tx_count}</strong> transactions. Average deposit is <strong>KES {avg_deposit:,.2f}</strong> vs average expense of <strong>KES {avg_withdrawal:,.2f}</strong>.",
                "icon": "fa-list-check",
                "tone": "blue"
            })

            if largest_deposit:
                summaries.append({
                    "title": "Largest Income",
                    "content": f"Your largest deposit was <strong>KES {float(largest_deposit.amount):,.2f}</strong> on {largest_deposit.transaction_date.strftime('%d %b %Y')} for {largest_deposit.description[:50]}...",
                    "icon": "fa-arrow-trend-up",
                    "tone": "green"
                })

            if largest_withdrawal:
                summaries.append({
                    "title": "Largest Expense",
                    "content": f"Your largest expense was <strong>KES {float(largest_withdrawal.amount):,.2f}</strong> on {largest_withdrawal.transaction_date.strftime('%d %b %Y')} for {largest_withdrawal.description[:50]}...",
                    "icon": "fa-arrow-trend-down",
                    "tone": "rose"
                })

    # Top insights
    if summary_type in ["overview", "top_insights"]:
        # Calculate month-over-month trends
        latest = all_statements.first()
        previous = all_statements.exclude(id=latest.id).first() if latest else None

        latest_revenue = float(getattr(latest, "total_deposits", 0) or 0) if latest else 0.0
        latest_expense = float(getattr(latest, "total_withdrawals", 0) or 0) if latest else 0.0

        if latest and previous:
            latest_revenue = float(getattr(latest, "total_deposits", 0) or 0)
            latest_expense = float(getattr(latest, "total_withdrawals", 0) or 0)
            prev_revenue = float(getattr(previous, "total_deposits", 0) or 0)
            prev_expense = float(getattr(previous, "total_withdrawals", 0) or 0)

            if prev_revenue > 0:
                revenue_change = ((latest_revenue - prev_revenue) / prev_revenue) * 100
                trend = "increased" if revenue_change > 0 else "decreased"
                summaries.append({
                    "title": "Revenue Trend",
                    "content": f"Revenue has <strong>{trend} by {abs(revenue_change):.1f}%</strong> compared to your previous statement period.",
                    "icon": "fa-chart-line",
                    "tone": "green" if revenue_change > 0 else "amber"
                })

            # Calculate expense efficiency
            if latest_revenue > 0:
                expense_ratio = (latest_expense / latest_revenue) * 100
                if expense_ratio > 80:
                    insight = "Your expense ratio is high. Consider reviewing major cost centers."
                    tone = "amber"
                elif expense_ratio < 50:
                    insight = "Excellent expense management! Your costs are well under control."
                    tone = "green"
                else:
                    insight = f"Your expense ratio is at {expense_ratio:.1f}%, which is within a healthy range."
                    tone = "blue"

                summaries.append({
                    "title": "Expense Efficiency",
                    "content": insight,
                    "icon": "fa-scale-balanced",
                    "tone": tone
                })

        # Cash position insight
        closing = float(getattr(latest_statement, "closing_balance", 0) or 0)
        if closing > 0:
            monthly_expense = latest_expense if latest else float(getattr(latest_statement, "total_withdrawals", 0) or 0)
            if monthly_expense > 0:
                runway_months = closing / monthly_expense
                if runway_months < 1:
                    summaries.append({
                        "title": "Cash Position Alert",
                        "content": f"Your current balance covers less than 1 month of expenses. Consider accelerating collections.",
                        "icon": "fa-triangle-exclamation",
                        "tone": "rose"
                    })
                elif runway_months < 3:
                    summaries.append({
                        "title": "Cash Runway",
                        "content": f"You have approximately <strong>{runway_months:.1f} months</strong> of operating expenses in reserve. Monitor cash flow closely.",
                        "icon": "fa-clock",
                        "tone": "amber"
                    })
                else:
                    summaries.append({
                        "title": "Cash Position",
                        "content": f"Strong liquidity position with <strong>{runway_months:.1f} months</strong> of expenses covered by current balance.",
                        "icon": "fa-shield-check",
                        "tone": "green"
                    })

    return summaries


# ──────────────────────────────────────────────
# Statements
# ──────────────────────────────────────────────

@login_required(login_url="login")
def statements_view(request):
    """List all bank statements for the current user with optional date/status filters."""
    bank_statements = _account_qs(request).order_by("-upload_date")

    # Date & status filters
    date_from   = request.GET.get("date_from", "").strip()
    date_to     = request.GET.get("date_to", "").strip()
    status_filter = request.GET.get("status", "").strip()

    if date_from:
        try:
            from datetime import date as _date
            bank_statements = bank_statements.filter(upload_date__date__gte=date_from)
        except Exception:
            date_from = ""
    if date_to:
        try:
            bank_statements = bank_statements.filter(upload_date__date__lte=date_to)
        except Exception:
            date_to = ""
    if status_filter == "processed":
        bank_statements = bank_statements.filter(is_processed=True)
    elif status_filter == "pending":
        bank_statements = bank_statements.filter(is_processed=False)

    latest_statement = BankStatement.objects.filter(uploaded_by=request.user).order_by("-upload_date").first()

    all_statement_rows = [
        {
            "id": s.id,
            "file_name": s.file_name,
            "uploaded": s.upload_date.strftime("%d %b %Y, %H:%M") if s.upload_date else "",
            "period": (
                f"{getattr(s, 'statement_period_start', '') or ''} – {getattr(s, 'statement_period_end', '') or ''}"
            ).strip(" –"),
            "deposits": float(getattr(s, "total_deposits", 0) or 0),
            "closing_balance": float(getattr(s, "closing_balance", 0) or 0),
            "status": "Processed" if getattr(s, "is_processed", False) else "Pending",
            "error": getattr(s, "processing_error", "") or "",
        }
        for s in bank_statements
    ]

    paginator = Paginator(all_statement_rows, 15)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    tx_page_number = request.GET.get("tx_page", 1)
    latest_transactions_qs = []
    if latest_statement:
        latest_transactions_qs = FinancialTransaction.objects.filter(
            bank_statement=latest_statement
        ).order_by("-transaction_date")
    tx_paginator = Paginator(latest_transactions_qs, 10)
    tx_page_obj = tx_paginator.get_page(tx_page_number)

    ctx = _base_context(request)
    ctx.update({
        "bank_statements": bank_statements,
        "latest_statement": latest_statement,
        "statement_rows": page_obj,
        "page_obj": page_obj,
        "tx_page_obj": tx_page_obj,
        "latest_transactions": tx_page_obj,
        "date_from": date_from,
        "date_to": date_to,
        "status_filter": status_filter,
        "active_page": "statements",
    })
    return render(request, "statements.html", ctx)


@login_required(login_url="login")
def delete_bank_statement(request, statement_id):
    """Delete a bank statement — returns JSON so JS fetch works."""
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "Method not allowed."}, status=405)

    statement = BankStatement.objects.filter(id=statement_id, uploaded_by=request.user).first()
    if not statement:
        return JsonResponse({"success": False, "error": "Statement not found or permission denied."}, status=403)

    _audit(request, "DELETE", f"Deleted statement id={statement_id} ({statement.file_name})")
    statement.delete()
    return JsonResponse({"success": True, "message": "Statement deleted."})


@login_required(login_url="login")
def reprocess_statement(request, statement_id):
    """Re-run AI extraction on a statement that has zero/bad data."""
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "Method not allowed."}, status=405)

    statement = BankStatement.objects.filter(id=statement_id, uploaded_by=request.user).first()
    if not statement:
        return JsonResponse({"success": False, "error": "Statement not found."}, status=403)

    if not statement.extracted_text:
        return JsonResponse({"success": False, "error": "No extracted text to reprocess. Please re-upload the file."}, status=400)

    try:
        # Clear old derived data before reprocessing
        FinancialTransaction.objects.filter(bank_statement=statement).delete()
        KPIMetric.objects.filter(bank_statement=statement).delete()
        AIAnalysis.objects.filter(bank_statement=statement, analysis_type__in=["INSIGHT", "ALERT"]).delete()
        statement.is_processed = False
        statement.processing_error = ""
        statement.save()

        process_bank_statement_with_ai(statement)
        _audit(request, "REPROCESS", f"Reprocessed statement id={statement_id}")

        return JsonResponse({
            "success": True,
            "message": "Statement reprocessed successfully.",
            "total_deposits": float(statement.total_deposits or 0),
            "total_withdrawals": float(statement.total_withdrawals or 0),
        })
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)}, status=500)


# ──────────────────────────────────────────────
# Statement Detail
# ──────────────────────────────────────────────

@login_required(login_url="login")
def statement_detail(request, statement_id):
    """View details of a specific bank statement."""
    statement = get_object_or_404(BankStatement, id=statement_id, uploaded_by=request.user)

    kpis = KPIMetric.objects.filter(bank_statement=statement)
    analyses = AIAnalysis.objects.filter(bank_statement=statement).order_by("-created_at")

    # Search / filter
    tx_qs = FinancialTransaction.objects.filter(bank_statement=statement)
    search_q = request.GET.get("q", "").strip()
    tx_type_filter = request.GET.get("tx_type", "").strip().upper()
    if search_q:
        tx_qs = tx_qs.filter(Q(description__icontains=search_q) | Q(category__icontains=search_q))
    if tx_type_filter in ("DEPOSIT", "WITHDRAWAL", "TRANSFER"):
        tx_qs = tx_qs.filter(transaction_type=tx_type_filter)
    tx_qs = tx_qs.order_by("-transaction_date")

    # Pagination: 50 per page
    tx_paginator = Paginator(tx_qs, 50)
    tx_page = tx_paginator.get_page(request.GET.get("tx_page", 1))

    # Group KPIs by type
    kpi_groups = {}
    for kpi in kpis:
        kpi_groups.setdefault(kpi.metric_type, []).append(kpi)

    # Health score for this statement
    health_kpi = KPIMetric.objects.filter(
        bank_statement=statement, metric_name="Financial Health Score"
    ).first()
    if health_kpi:
        det_score = int(float(health_kpi.current_value))
        import re as _re2
        m2 = _re2.search(r"Tier:\s*([^.]+)", health_kpi.description or "")
        det_tier = m2.group(1).strip() if m2 else ""
    else:
        kpi_dict = {k.metric_name.replace(" ", "_"): {"value": float(k.current_value)} for k in kpis}
        from .ai_agent import HospitalKPIAgent as _HKA
        h = _HKA().compute_health_score(kpi_dict)
        det_score, det_tier = h["score"], h["tier"]

    if   det_score >= 80: det_tone = "green"
    elif det_score >= 60: det_tone = "amber"
    elif det_score >= 40: det_tone = "orange"
    elif det_score >= 20: det_tone = "red"
    else:                 det_tone = "critical"

    # Recurring payments insight
    recurring_insight = analyses.filter(
        title__startswith="Recurring Payments Detected"
    ).first()

    ctx = _base_context(request)
    ctx.update({
        "statement": statement,
        "kpis": kpis,
        "kpi_groups": kpi_groups,
        "transactions": tx_page,
        "tx_page_obj": tx_page,
        "analyses": analyses,
        "active_page": "statements",
        "search_q": search_q,
        "tx_type_filter": tx_type_filter,
        "tx_total": tx_qs.count(),
        "health_score": det_score,
        "health_tier":  det_tier,
        "health_tone":  det_tone,
        "recurring_insight": recurring_insight,
    })
    return render(request, "statement_detail.html", ctx)


# ──────────────────────────────────────────────
# KPI Comparison
# ──────────────────────────────────────────────

def _linear_regression_projection(data_list: list[float], steps: int = 2) -> list[float | None]:
    """Project future values using ordinary least-squares linear regression."""
    n = len(data_list)
    if n < 2:
        return [None] * steps
    xs = list(range(n))
    x_mean = sum(xs) / n
    y_mean = sum(data_list) / n
    numerator   = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, data_list))
    denominator = sum((x - x_mean) ** 2 for x in xs)
    slope = numerator / denominator if denominator else 0
    intercept = y_mean - slope * x_mean
    return [round(intercept + slope * (n + i), 2) for i in range(steps)]


@login_required(login_url="login")
def kpi_comparison(request):
    """Compare KPIs across multiple statements."""
    try:
        limit = max(2, min(24, int(request.GET.get("limit", 12))))
    except (ValueError, TypeError):
        limit = 12

    statements = BankStatement.objects.filter(
        uploaded_by=request.user, is_processed=True
    ).prefetch_related("kpi_metrics").order_by("-statement_period_end")[:limit]

    kpi_comparison_data = {}
    for statement in statements:
        for kpi in statement.kpi_metrics.all():
            if kpi.metric_name not in kpi_comparison_data:
                kpi_comparison_data[kpi.metric_name] = []
            kpi_comparison_data[kpi.metric_name].append({
                "period": (
                    statement.statement_period_start.strftime("%b %Y")
                    if statement.statement_period_start
                    else str(statement.upload_date.date())
                ),
                "value": float(kpi.current_value),
                "unit": kpi.unit,
            })

    # 1. Main Trends & Projections
    key_metrics = ["Total Revenue", "Total Expenses", "Net Income", "Profit Margin"]
    chart_labels = []
    
    # Sort statements chronologically for the chart
    sorted_view_statements = list(reversed(list(statements)))
    for s in sorted_view_statements:
        label = (s.statement_period_start.strftime("%b %Y") if s.statement_period_start else str(s.upload_date.date()))
        if label not in chart_labels:
            chart_labels.append(label)

    # 2. Add Projections (Linear trend for next 2 months)
    projection_labels = chart_labels.copy()
    if len(chart_labels) >= 2:
        last_label_date = sorted_view_statements[-1].statement_period_start or datetime.date.today()
        # Simple placeholder for next 2 months
        for i in range(1, 3):
            next_date = last_label_date + datetime.timedelta(days=32 * i)
            projection_labels.append(next_date.strftime("%b %Y (P)"))

    def get_projection(data_list):
        return _linear_regression_projection(data_list, steps=2)

    # Projections specifically for Revenue and Expenses
    revenue_data = [ {entry["period"]: entry["value"] for entry in kpi_comparison_data.get("Total Revenue", [])}.get(label, 0) for label in chart_labels ]
    expense_data = [ {entry["period"]: entry["value"] for entry in kpi_comparison_data.get("Total Expenses", [])}.get(label, 0) for label in chart_labels ]
    
    rev_proj = get_projection(revenue_data)
    exp_proj = get_projection(expense_data)

    # 3. Cumulative Profit (for Area Chart)
    cumulative_profit = []
    current_sum = 0
    profit_data = [ {entry["period"]: entry["value"] for entry in kpi_comparison_data.get("Net Income", [])}.get(label, 0) for label in chart_labels ]
    for val in profit_data:
        current_sum += val
        cumulative_profit.append(round(current_sum, 2))

    # 4. Chart datasets (one per key metric) + Efficiency Data
    chart_datasets = []
    for metric_name in key_metrics:
        period_map = {entry["period"]: entry["value"] for entry in kpi_comparison_data.get(metric_name, [])}
        chart_datasets.append({
            "label": metric_name,
            "data": [period_map.get(label, 0) for label in chart_labels],
        })

    efficiency_data = []
    for s in sorted_view_statements:
        rev_m = next((k for k in s.kpi_metrics.all() if k.metric_name == "Total Revenue"), None)
        tx_count = s.transactions.count()
        rev_val = float(rev_m.current_value) if rev_m else 0
        efficiency_data.append(round(rev_val / tx_count, 2) if tx_count else 0)

    # 5. Radar Data (Performance Profile)
    # Axes: [Revenue, Profitability, Efficiency, Liquidity, Cost Control]
    radar_data = {"labels": ["Revenue", "Profitability", "Efficiency", "Liquidity", "Cost Control"], "latest": [0,0,0,0,0], "average": [0,0,0,0,0]}
    if sorted_view_statements:
        latest_s = sorted_view_statements[-1]
        
        def get_v(s, name):
            m = KPIMetric.objects.filter(bank_statement=s, metric_name=name).first()
            return float(m.current_value) if m else 0
        
        # Helper to compute averages across all statements
        all_s = BankStatement.objects.filter(uploaded_by=request.user, is_processed=True)
        def get_avg(name):
            val = KPIMetric.objects.filter(bank_statement__in=all_s, metric_name=name).aggregate(Avg('current_value'))['current_value__avg']
            return float(val or 0)

        # 1. Revenue (relative to avg)
        avg_rev = get_avg("Total Revenue") or 1
        radar_data["latest"][0] = min(100, (get_v(latest_s, "Total Revenue") / avg_rev) * 50)
        radar_data["average"][0] = 50
        
        # 2. Profitability (Profit Margin %)
        radar_data["latest"][1] = max(0, min(100, get_v(latest_s, "Profit Margin")))
        radar_data["average"][1] = max(0, min(100, get_avg("Profit Margin")))
        
        # 3. Efficiency (Revenue per Transaction)
        avg_eff = (sum(efficiency_data) / len(efficiency_data) if efficiency_data else 0) or 1
        radar_data["latest"][2] = min(100, (efficiency_data[-1] / avg_eff) * 50) if efficiency_data else 0
        radar_data["average"][2] = 50
        
        # 4. Liquidity (Liquidity Days normalized to 90)
        radar_data["latest"][3] = min(100, (get_v(latest_s, "Liquidity Days") / 90) * 100)
        radar_data["average"][3] = min(100, (get_avg("Liquidity Days") / 90) * 100)
        
        # 5. Cost Control (100 - Expense Ratio)
        radar_data["latest"][4] = max(0, 100 - get_v(latest_s, "Expense to Revenue Ratio"))
        radar_data["average"][4] = max(0, 100 - get_avg("Expense to Revenue Ratio"))

    # Month-over-month change indicators for key metrics
    mom_changes = {}
    for metric_name in key_metrics:
        data = [period_map.get(label, 0) for label in chart_labels
                for period_map in [{entry["period"]: entry["value"] for entry in kpi_comparison_data.get(metric_name, [])}]]
        if len(data) >= 2 and data[-2] != 0:
            pct = round((data[-1] - data[-2]) / abs(data[-2]) * 100, 1)
            mom_changes[metric_name] = pct

    # Anomalies summary across all statements
    anomaly_total = 0
    for s in sorted_view_statements:
        m = KPIMetric.objects.filter(bank_statement=s, metric_name="Anomaly Count").first()
        if m:
            anomaly_total += int(float(m.current_value))

    ctx = _base_context(request)
    ctx.update({
        "statements": statements,
        "kpi_comparison_data": kpi_comparison_data,
        "chart_labels": chart_labels,
        "chart_datasets": chart_datasets,
        "projection_labels": projection_labels,
        "revenue_projection": revenue_data + rev_proj,
        "expense_projection": expense_data + exp_proj,
        "cumulative_profit": cumulative_profit,
        "efficiency_data": efficiency_data,
        "radar_data": radar_data,
        "mom_changes": mom_changes,
        "anomaly_total": anomaly_total,
        "current_limit": limit,
        "active_page": "analytics",
    })
    return render(request, "kpi_comparison.html", ctx)


# ──────────────────────────────────────────────
# Assistant
# ──────────────────────────────────────────────

@login_required(login_url="login")
def assistant_view(request):
    """Render the AI assistant page."""
    latest_statement = BankStatement.objects.filter(uploaded_by=request.user).order_by("-upload_date").first()
    latest_statement_id = latest_statement.id if latest_statement else ""

    # Load prior Q&A interactions across all statements for conversation history
    analyses = AIAnalysis.objects.filter(
        bank_statement__uploaded_by=request.user,
        analysis_type__in=["RESPONSE", "INSIGHT"],
    ).order_by("-created_at")[:30]

    ctx = _base_context(request)
    ctx.update({
        "latest_statement_id": latest_statement_id,
        "focus_statement": latest_statement,
        "analyses": analyses,
        "active_page": "assistant",
    })
    return render(request, "assistant.html", ctx)


# ──────────────────────────────────────────────
# Upload
# ──────────────────────────────────────────────

@login_required(login_url="login")
def upload_bank_statement(request):
    """Handle bank statement file upload (PDF or CSV)."""
    if request.method == "POST" and request.FILES.get("bank_statement"):
        uploaded_file = request.FILES["bank_statement"]
        file_name = uploaded_file.name.lower()
        pdf_password = request.POST.get("pdf_password", "").strip()

        # File size guard
        if uploaded_file.size > _MAX_UPLOAD_BYTES:
            return JsonResponse({"success": False, "error": f"File too large. Maximum allowed size is 25 MB."}, status=400)

        # File type guard (extension + magic bytes)
        is_pdf  = file_name.endswith(".pdf")
        is_csv  = file_name.endswith(".csv")
        is_xlsx = file_name.endswith(".xlsx")
        is_xls  = file_name.endswith(".xls")
        if not (is_pdf or is_csv or is_xlsx or is_xls):
            return JsonResponse({"success": False, "error": "Only PDF, CSV, and Excel (.xlsx/.xls) files are supported."}, status=400)
        header = uploaded_file.read(8)
        uploaded_file.seek(0)
        if is_pdf and not header.startswith(b"%PDF"):
            return JsonResponse({"success": False, "error": "The uploaded file does not appear to be a valid PDF."}, status=400)

        active_sub, _ = _active_account(request)
        bank_statement = BankStatement.objects.create(
            uploaded_by=request.user,
            file_name=uploaded_file.name,
            file=uploaded_file,
            sub_account=active_sub,
        )

        try:
            uploaded_file.seek(0)

            if is_csv or is_xlsx or is_xls:
                agent = HospitalKPIAgent()

                # ── Parse rows from CSV or Excel ───────────────────────────
                if is_csv:
                    extracted_text = uploaded_file.read().decode("utf-8", errors="replace")
                    bank_statement.extracted_text = extracted_text
                    bank_statement.save()
                    sample = extracted_text[:2048]
                    try:
                        dialect = csv.Sniffer().sniff(sample)
                        reader = csv.reader(StringIO(extracted_text), dialect)
                    except csv.Error:
                        reader = csv.reader(StringIO(extracted_text))
                    lines = [row for row in reader if any(c.strip() for c in row)]

                elif is_xlsx:
                    import openpyxl
                    wb = openpyxl.load_workbook(uploaded_file, data_only=True)
                    ws = wb.active
                    lines = []
                    for row in ws.iter_rows(values_only=True):
                        cells = [str(c) if c is not None else "" for c in row]
                        if any(c.strip() for c in cells):
                            lines.append(cells)
                    extracted_text = "\n".join([",".join(r) for r in lines])
                    bank_statement.extracted_text = extracted_text
                    bank_statement.save()

                else:  # .xls
                    import xlrd
                    content = uploaded_file.read()
                    wb = xlrd.open_workbook(file_contents=content)
                    ws = wb.sheet_by_index(0)
                    lines = []
                    for rx in range(ws.nrows):
                        cells = [str(ws.cell_value(rx, cx)) for cx in range(ws.ncols)]
                        if any(c.strip() for c in cells):
                            lines.append(cells)
                    extracted_text = "\n".join([",".join(r) for r in lines])
                    bank_statement.extracted_text = extracted_text
                    bank_statement.save()

                if not lines:
                    raise ValueError("The uploaded file is empty.")
                if len(lines) < 2:
                    raise ValueError("The uploaded file contains no data rows.")

                header_context = "\n".join([",".join(row) for row in lines[:10]])
                logger.info("CSV/Excel headers: %s", lines[0] if lines else [])
                logger.info("CSV/Excel sample row: %s", lines[1] if len(lines) > 1 else [])
                mapping = agent.extract_csv_structure_with_ai(header_context)
                logger.info("CSV/Excel mapping: %s", mapping)

                has_amount = any(mapping.get(k) is not None for k in ("amount_index", "credit_index", "debit_index"))
                if not mapping or mapping.get("date_index") is None or not has_amount:
                    raise ValueError(f"Could not detect column structure. Headers found: {lines[0] if lines else []}. Mapping: {mapping}")

                transactions = []
                # Find where the data actually starts (it's not always row 1)
                start_row = 1
                for i, row in enumerate(lines[:5]):
                    # If we see a date-like thing in the date_index column, this might be a data row
                    val = row[mapping["date_index"]] if mapping["date_index"] < len(row) else ""
                    if BankStatementPDFExtractor._parse_date(val):
                        start_row = i
                        break
                
                for row_idx, row in enumerate(lines[start_row:]):
                    if not row: continue

                    def get_val(idx):
                        if idx is not None and isinstance(idx, int) and idx < len(row):
                            return row[idx].strip()
                        return ""

                    raw_date = get_val(mapping.get("date_index"))
                    desc = get_val(mapping.get("description_index"))
                    
                    if not raw_date:
                        continue

                    # Safe native date parser inside the generator
                    tx_date = BankStatementPDFExtractor._parse_date(raw_date)
                    if not tx_date:
                        # print(f"DEBUG: Invalid date at row {row_idx + start_row}: {raw_date}")
                        continue

                    amt_val = None
                    tx_type = None

                    if mapping.get("credit_index") is not None or mapping.get("debit_index") is not None:
                        cred = BankStatementPDFExtractor._parse_decimal(get_val(mapping.get("credit_index")))
                        deb = BankStatementPDFExtractor._parse_decimal(get_val(mapping.get("debit_index")))
                        if cred and cred > 0:
                            amt_val = cred
                            tx_type = "DEPOSIT"
                        elif deb and deb > 0:
                            amt_val = deb
                            tx_type = "WITHDRAWAL"
                    else:
                        amt_raw = BankStatementPDFExtractor._parse_decimal(get_val(mapping.get("amount_index")))
                        if amt_raw is not None:
                            amt_val = abs(amt_raw)
                            tx_type_raw = str(get_val(mapping.get("transaction_type_index"))).upper()
                            if tx_type_raw in ["CR", "CREDIT", "DEP", "DEPOSIT"]:
                                tx_type = "DEPOSIT"
                            elif tx_type_raw in ["DR", "DEBIT", "WITH", "WITHDRAWAL"]:
                                tx_type = "WITHDRAWAL"
                            else:
                                tx_type = "DEPOSIT" if amt_raw > 0 else "WITHDRAWAL"

                    if amt_val is not None and tx_type:
                        transactions.append({
                            "date": tx_date.isoformat(),
                            "description": desc,
                            "amount": float(amt_val),
                            "type": tx_type
                        })

                if not transactions:
                    raise ValueError("AI structured the columns but no valid transaction lines were extracted from the data.")

                # Sort by date regardless of statement order (ascending = oldest first)
                def _parse_tx_date(d):
                    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y", "%b. %d, %Y", "%B. %d, %Y"):
                        try:
                            return datetime.datetime.strptime(str(d).strip(), fmt).date()
                        except ValueError:
                            continue
                    return datetime.date.min

                transactions.sort(key=lambda x: _parse_tx_date(x["date"]))

                fin_data = {
                    "statement_period_start": transactions[0]["date"],
                    "statement_period_end": transactions[-1]["date"],
                    "opening_balance": 0.0,
                    "closing_balance": 0.0,
                    "total_deposits": sum(float(t["amount"]) for t in transactions if t["type"] == "DEPOSIT"),
                    "total_withdrawals": sum(float(t["amount"]) for t in transactions if t["type"] == "WITHDRAWAL"),
                    "transactions": transactions
                }
                
                # Assume closing balance is end of deposits - withdrawals to be safe if unprovided
                fin_data["closing_balance"] = fin_data["total_deposits"] - fin_data["total_withdrawals"]
                
                # Dispatch CSV processing to background worker
                try:
                    from django_q.tasks import async_task
                    async_task("kpi.tasks.process_csv_statement_task", bank_statement.id, fin_data)
                except Exception:
                    _process_with_financial_data(bank_statement, fin_data)

            else:
                # PDF path — extract text, with password + OCR support
                uploaded_file.seek(0)
                try:
                    extracted_text = BankStatementPDFExtractor.extract_text_from_pdf(
                        uploaded_file, password=pdf_password
                    )
                except PDFPasswordRequired as e:
                    # Delete the incomplete record and ask the frontend for a password
                    bank_statement.delete()
                    return JsonResponse({
                        "success": False,
                        "password_required": True,
                        "error": str(e),
                    }, status=400)
                except PDFWrongPassword as e:
                    bank_statement.delete()
                    return JsonResponse({
                        "success": False,
                        "password_required": True,
                        "wrong_password": True,
                        "error": str(e),
                    }, status=400)

                agent_for_ocr = HospitalKPIAgent()

                # Always attempt OCR alongside text extraction.
                # For scanned PDFs, text is empty → OCR is essential.
                # For digital PDFs (e.g. M-PESA), OCR gives a cleaner structured
                # representation that supplements the regex parser.
                page_images = BankStatementPDFExtractor.render_pdf_pages_to_images(
                    uploaded_file, password=pdf_password
                )

                if BankStatementPDFExtractor.needs_ocr(extracted_text):
                    # Scanned PDF — OCR is the only source of text
                    if page_images:
                        ocr_text = agent_for_ocr.ocr_pdf_pages(page_images)
                        if ocr_text:
                            extracted_text = ocr_text
                else:
                    # Digital PDF — check if regex finds any transactions from native text;
                    # if not, fall back to OCR which may produce cleaner output
                    test_txns = BankStatementPDFExtractor.parse_mpesa_transactions(extracted_text)
                    if not test_txns and page_images:
                        ocr_text = agent_for_ocr.ocr_pdf_pages(page_images)
                        if ocr_text and len(ocr_text.strip()) > len(extracted_text.strip()):
                            extracted_text = ocr_text

                bank_statement.extracted_text = BankStatementPDFExtractor.clean_pdf_text(extracted_text)
                bank_statement.save()
                # Dispatch to background worker so HTTP response returns immediately
                try:
                    from django_q.tasks import async_task
                    async_task("kpi.tasks.process_statement_task", bank_statement.id)
                except Exception:
                    # Fall back to synchronous processing if worker isn't running
                    process_bank_statement_with_ai(bank_statement)

            _audit(request, "UPLOAD", f"Uploaded {uploaded_file.name} (id={bank_statement.id})")
            return JsonResponse({
                "success": True,
                "message": "Statement uploaded. Processing in the background — refresh shortly.",
                "statement_id": bank_statement.id,
                "redirect_url": f"/statement/{bank_statement.id}/",
            })

        except (PDFPasswordRequired, PDFWrongPassword) as e:
            # Catch any that bubble up from helpers
            bank_statement.delete()
            return JsonResponse({
                "success": False,
                "password_required": True,
                "wrong_password": isinstance(e, PDFWrongPassword),
                "error": str(e),
            }, status=400)
        except Exception as e:
            bank_statement.processing_error = str(e)
            bank_statement.is_processed = False
            bank_statement.save()
            return JsonResponse({"success": False, "error": str(e)}, status=400)

    return JsonResponse({"success": False, "error": "No file provided."}, status=400)


# ──────────────────────────────────────────────
# Processing helpers
# ──────────────────────────────────────────────

def _categorise_tx(description: str) -> str:
    """Return a broad expense category from a transaction description."""
    desc = description.upper()
    if any(w in desc for w in ["SALARY", "PAYROLL", "WAGE", "STAFF"]):
        return "Staff Salaries"
    if any(w in desc for w in ["SUPPLIES", "MEDICAL", "EQUIPMENT", "STOCK"]):
        return "Medical Supplies"
    if any(w in desc for w in ["UTILITY", "ELECTRICITY", "WATER", "KPLC", "POWER"]):
        return "Utilities"
    if any(w in desc for w in ["RENT", "LEASE", "PREMISES"]):
        return "Rent"
    if any(w in desc for w in ["MAINTENANCE", "REPAIR", "SERVICE"]):
        return "Maintenance"
    if any(w in desc for w in ["TRANSPORT", "FUEL", "PETROL", "VEHICLE"]):
        return "Transport"
    if any(w in desc for w in ["BANK", "CHARGES", "LOAN", "INTEREST", "FEES"]):
        return "Bank Charges"
    if any(w in desc for w in ["INSURANCE", "PREMIUM"]):
        return "Insurance"
    return "Other"


def _get_category_totals(statement) -> dict:
    totals = {}
    for tx in FinancialTransaction.objects.filter(
        bank_statement=statement, transaction_type="WITHDRAWAL"
    ):
        cat = tx.category or _categorise_tx(tx.description)
        totals[cat] = totals.get(cat, 0) + float(tx.amount)
    return totals


def _detect_category_drift(current_statement, user) -> list:
    """
    Compare current statement's category spend to the 3-statement rolling average.
    Returns a list of alert dicts for categories that drifted ≥ 30 %.
    """
    prev_statements = list(
        BankStatement.objects.filter(
            uploaded_by=user,
            is_processed=True,
        ).exclude(id=current_statement.id).order_by("-statement_period_end")[:3]
    )
    if not prev_statements:
        return []

    current_totals = _get_category_totals(current_statement)
    prev_buckets: dict[str, list] = {}
    for s in prev_statements:
        for cat, amt in _get_category_totals(s).items():
            prev_buckets.setdefault(cat, []).append(amt)

    prev_averages = {cat: sum(v) / len(v) for cat, v in prev_buckets.items()}

    alerts = []
    for cat, curr_amt in current_totals.items():
        prev_avg = prev_averages.get(cat, 0)
        if prev_avg < 1:
            continue
        pct = (curr_amt - prev_avg) / prev_avg * 100
        if abs(pct) < 30:
            continue
        direction = "increased" if pct > 0 else "decreased"
        severity = "CRITICAL" if abs(pct) >= 60 else "WARNING"
        alerts.append({
            "title": f"Spending Drift — {cat}",
            "content": (
                f"{cat} has {direction} by {abs(pct):.0f}% this period "
                f"(KES {curr_amt:,.0f} vs {len(prev_statements)}-period avg "
                f"KES {prev_avg:,.0f}). Review for budget adherence."
            ),
            "severity": severity,
        })
    return alerts


def _safe_float(val) -> float:
    """Convert a value to float, stripping currency symbols and commas."""
    if isinstance(val, (int, float)):
        return float(val)
    try:
        cleaned = str(val).replace(",", "").replace("KES", "").replace("Ksh", "").strip()
        return float(cleaned)
    except (ValueError, TypeError):
        return 0.0


def _derive_period_from_transactions(transactions: list) -> tuple:
    """
    Given a list of transaction dicts with a "date" key (YYYY-MM-DD strings or
    date objects), return (min_date, max_date) as date objects.  Returns
    (None, None) if no parseable dates are found.
    """
    parsed = []
    for tx in transactions:
        raw = tx.get("date") or tx.get("transaction_date")
        if not raw:
            continue
        if isinstance(raw, datetime.date):
            parsed.append(raw)
            continue
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y"):
            try:
                parsed.append(datetime.datetime.strptime(str(raw).strip(), fmt).date())
                break
            except ValueError:
                continue
    if not parsed:
        return None, None
    return min(parsed), max(parsed)


def _process_with_financial_data(bank_statement, financial_data):
    """Shared: store extracted data, calculate KPIs, generate insights."""
    try:
        # Derive period from actual transaction dates so reverse-chronological
        # statements and any format are handled correctly.
        transactions_raw = financial_data.get("transactions", [])
        derived_start, derived_end = _derive_period_from_transactions(transactions_raw)

        # Fall back to AI-reported period fields if no transactions present
        ai_start = financial_data.get("statement_period_start")
        ai_end   = financial_data.get("statement_period_end")

        def _to_date(val):
            if not val:
                return None
            if isinstance(val, datetime.date):
                return val
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
                try:
                    return datetime.datetime.strptime(str(val).strip(), fmt).date()
                except ValueError:
                    continue
            return None

        period_start = derived_start or _to_date(ai_start)
        period_end   = derived_end   or _to_date(ai_end)

        # Guarantee start <= end regardless of what was returned
        if period_start and period_end and period_start > period_end:
            period_start, period_end = period_end, period_start

        bank_statement.statement_period_start = period_start
        bank_statement.statement_period_end   = period_end
        
        # Opening balance - use AI value or default 0
        opening_bal = _safe_float(financial_data.get("opening_balance", 0))
        bank_statement.opening_balance = opening_bal

        # Closing balance - use AI value, but if 0 and we have transactions, calculate it
        closing_bal = _safe_float(financial_data.get("closing_balance", 0))
        bank_statement.closing_balance = closing_bal

        # Always recompute totals from the actual transaction list so that
        # inaccurate AI-reported totals don't cause zero-KPI dashboards.
        computed_deposits = sum(
            _safe_float(t.get("amount", 0))
            for t in transactions_raw
            if str(t.get("type", "")).upper() in ("DEPOSIT", "CREDIT", "CR")
        )
        computed_withdrawals = sum(
            _safe_float(t.get("amount", 0))
            for t in transactions_raw
            if str(t.get("type", "")).upper() in ("WITHDRAWAL", "DEBIT", "DR")
        )
        # Use computed values; fall back to AI totals only if no transactions parsed
        bank_statement.total_deposits    = computed_deposits    or financial_data.get("total_deposits", 0)
        bank_statement.total_withdrawals = computed_withdrawals or financial_data.get("total_withdrawals", 0)
        
        # Always recompute closing balance from actual transactions — never trust the AI
        # summary value, which frequently returns total_deposits instead of the true balance.
        if computed_deposits or computed_withdrawals:
            dep = Decimal(str(computed_deposits))
            wdr = Decimal(str(computed_withdrawals))
            opn = Decimal(str(bank_statement.opening_balance or 0))
            bank_statement.closing_balance = float(opn + dep - wdr)

        bank_statement.save()

        # Save transactions
        for tx in financial_data.get("transactions", []):
            FinancialTransaction.objects.create(
                bank_statement=bank_statement,
                transaction_date=tx["date"],
                description=tx["description"],
                amount=tx["amount"],
                transaction_type=tx["type"],
                category=tx.get("category") or None,
                running_balance=tx.get("running_balance") or None,
            )

        # Calculate period days for accurate daily KPIs
        period_days = 30
        if period_start and period_end:
            try:
                delta = (period_end - period_start).days
                if delta > 0:
                    period_days = delta
            except TypeError:
                pass

        agent = HospitalKPIAgent()
        kpis = agent.calculate_kpis(
            financial_data.get("transactions", []),
            float(bank_statement.opening_balance or 0),
            float(bank_statement.closing_balance or 0),
            period_days=period_days,
        )

        for kpi_name, kpi_data in kpis.items():
            KPIMetric.objects.create(
                metric_name=kpi_name.replace("_", " "),
                metric_type=kpi_data["type"],
                description=kpi_data["description"],
                current_value=kpi_data["value"],
                unit=kpi_data["unit"],
                bank_statement=bank_statement,
                status=kpi_data.get("status", "HEALTHY"),
                warning_threshold=kpi_data.get("warning_threshold"),
                critical_threshold=kpi_data.get("critical_threshold"),
            )

        insights = agent.generate_insights(
            financial_data.get("transactions", []),
            kpis,
            f"{bank_statement.statement_period_start} to {bank_statement.statement_period_end}",
        )
        AIAnalysis.objects.create(
            bank_statement=bank_statement,
            analysis_type="INSIGHT",
            title="Financial Performance Analysis",
            content=insights,
        )

        alerts = agent.generate_alerts(kpis)

        # ── Category drift alerts (cross-statement) ───────────────
        drift_alerts = _detect_category_drift(bank_statement, bank_statement.uploaded_by)
        alerts.extend(drift_alerts)

        for alert in alerts:
            AIAnalysis.objects.create(
                bank_statement=bank_statement,
                analysis_type="ALERT",
                title=alert["title"],
                content=alert["content"],
                severity=alert["severity"],
            )

        # ── Financial Health Score & Risk Tier ────────────────────
        health = agent.compute_health_score(kpis)
        KPIMetric.objects.create(
            metric_name="Financial Health Score",
            metric_type="FINANCIAL_HEALTH",
            description=(
                f"Composite 0–100 score. Tier: {health['tier']}. "
                f"Breakdown — margin: {health['breakdown']['profit_margin']}pts, "
                f"runway: {health['breakdown']['cash_runway']}pts, "
                f"expense: {health['breakdown']['expense_ratio']}pts, "
                f"consistency: {health['breakdown']['revenue_consistency']}pts, "
                f"anomaly penalty: {health['breakdown']['anomaly_penalty']}pts."
            ),
            current_value=health["score"],
            unit="/ 100",
            bank_statement=bank_statement,
            status="HEALTHY" if health["score"] >= 60 else ("WARNING" if health["score"] >= 40 else "CRITICAL"),
        )

        # ── Recurring payment detection ───────────────────────────
        tx_raw = financial_data.get("transactions", [])
        recurring = agent.detect_recurring_payments(tx_raw)
        if recurring:
            lines = "\n".join(
                f"• {r['description'][:40]} — KES {r['avg_amount']:,.0f}/mo "
                f"({r['occurrences']}x, every ~{r['avg_interval_days']} days)"
                for r in recurring[:10]
            )
            AIAnalysis.objects.create(
                bank_statement=bank_statement,
                analysis_type="INSIGHT",
                title=f"Recurring Payments Detected ({len(recurring)} found)",
                content=(
                    f"The following transactions appear to recur monthly. "
                    f"Total estimated recurring spend: "
                    f"KES {sum(r['avg_amount'] for r in recurring):,.0f}/mo.\n\n{lines}"
                ),
            )
            # Auto-tag recurring transactions in the DB
            for r in recurring:
                FinancialTransaction.objects.filter(
                    bank_statement=bank_statement,
                    transaction_type="WITHDRAWAL",
                    description__istartswith=r["description"][:20],
                    category__isnull=True,
                ).update(category="Recurring")

        bank_statement.is_processed = True
        bank_statement.save()

    except Exception as e:
        bank_statement.processing_error = str(e)
        bank_statement.is_processed = False
        bank_statement.save()
        raise


def process_bank_statement_with_ai(bank_statement):
    """Process PDF bank statement using AI extraction."""
    try:
        agent = HospitalKPIAgent()
        financial_data = agent.extract_financial_data(bank_statement.extracted_text)

        if not financial_data:
            raise ValueError("Could not extract financial data from statement.")

        _process_with_financial_data(bank_statement, financial_data)

    except Exception as e:
        bank_statement.processing_error = str(e)
        bank_statement.is_processed = False
        bank_statement.save()


# ──────────────────────────────────────────────
# API Endpoints
# ──────────────────────────────────────────────

class AskAIView(APIView):
    """Ask AI questions about financial data — system-wide context."""

    def post(self, request):
        if not request.user.is_authenticated:
            return Response({"error": "Not authenticated."}, status=401)

        question = request.data.get("question", "").strip()
        statement_id = request.data.get("statement_id")

        if not question:
            return Response({"error": "Question is required."}, status=400)
        if len(question) > _MAX_QUESTION_LEN:
            return Response({"error": f"Question too long. Maximum {_MAX_QUESTION_LEN} characters."}, status=400)

        try:
            # Build system-wide context
            all_statements = BankStatement.objects.filter(
                uploaded_by=request.user, is_processed=True
            ).prefetch_related("transactions").order_by("-upload_date")

            statements_context = []
            all_transactions = []
            total_revenue = 0.0
            total_expenses = 0.0

            for s in all_statements:
                deposits = float(getattr(s, "total_deposits", 0) or 0)
                withdrawals = float(getattr(s, "total_withdrawals", 0) or 0)
                total_revenue += deposits
                total_expenses += withdrawals
                statements_context.append({
                    "file_name": s.file_name,
                    "period": f"{s.statement_period_start} to {s.statement_period_end}",
                    "deposits": deposits,
                    "withdrawals": withdrawals,
                    "closing_balance": float(getattr(s, "closing_balance", 0) or 0),
                })
                for tx in s.transactions.all().order_by("-transaction_date")[:50]:
                    all_transactions.append({
                        "file_name": s.file_name,
                        "date": str(tx.transaction_date),
                        "description": tx.description,
                        "amount": float(tx.amount),
                        "transaction_type": tx.transaction_type,
                        "category": tx.category or "",
                    })

            # Prior assistant interactions
            history = list(
                AIAnalysis.objects.filter(
                    bank_statement__uploaded_by=request.user,
                    analysis_type="RESPONSE",
                ).order_by("-created_at").values("title", "content")[:10]
            )

            net_profit = total_revenue - total_expenses
            profit_margin = (net_profit / total_revenue * 100) if total_revenue > 0 else 0.0

            system_context = {
                "statements": statements_context,
                "transactions": all_transactions,
                "aggregate_metrics": {
                    "total_revenue": total_revenue,
                    "total_expenses": total_expenses,
                    "net_profit": net_profit,
                    "profit_margin": round(profit_margin, 2),
                    "total_cashflows": total_revenue + total_expenses,
                },
                "history": history,
            }

            agent = HospitalKPIAgent()
            answer = agent.answer_system_question(question, system_context)

            # Save interaction against the latest statement (or the specified one)
            target_statement = None
            if statement_id:
                target_statement = BankStatement.objects.filter(
                    id=statement_id, uploaded_by=request.user
                ).first()
            if not target_statement and all_statements.exists():
                target_statement = all_statements.first()

            if target_statement:
                AIAnalysis.objects.create(
                    bank_statement=target_statement,
                    analysis_type="RESPONSE",
                    title=f"Q: {question[:200]}",
                    content=answer,
                )

            _audit(request, "AI_QUERY", question[:200])
            return Response({"success": True, "answer": answer})

        except Exception as e:
            return Response({"error": str(e)}, status=500)


class KPIDataView(APIView):
    """Get KPI data as JSON for frontend visualization."""

    def get(self, request, statement_id):
        if not request.user.is_authenticated:
            return Response({"error": "Not authenticated."}, status=401)

        try:
            statement = BankStatement.objects.get(id=statement_id, uploaded_by=request.user)
            kpis = KPIMetric.objects.filter(bank_statement=statement)
            data = {
                "statement": {
                    "id": statement.id,
                    "period_start": str(statement.statement_period_start),
                    "period_end": str(statement.statement_period_end),
                },
                "kpis": [
                    {
                        "name": kpi.metric_name,
                        "type": kpi.metric_type,
                        "value": float(kpi.current_value),
                        "unit": kpi.unit,
                        "status": kpi.status,
                    }
                    for kpi in kpis
                ],
            }
            return Response(data)
        except BankStatement.DoesNotExist:
            return Response({"error": "Statement not found."}, status=404)


class ReportSummaryView(APIView):
    """Return a summary of reports/statements for the current user."""

    def get(self, request):
        if not request.user.is_authenticated:
            return Response({"error": "Not authenticated."}, status=401)

        statements = BankStatement.objects.filter(uploaded_by=request.user).order_by("-upload_date")

        # Build aggregate context for AI summary
        total_revenue = 0.0
        total_expenses = 0.0
        top_files = []
        for s in statements:
            deps = float(getattr(s, "total_deposits", 0) or 0)
            wdrs = float(getattr(s, "total_withdrawals", 0) or 0)
            total_revenue += deps
            total_expenses += wdrs
            top_files.append({
                "file_name": s.file_name,
                "closing_balance": float(getattr(s, "closing_balance", 0) or 0),
            })

        net_profit = total_revenue - total_expenses
        profit_margin = (net_profit / total_revenue * 100) if total_revenue > 0 else 0.0

        report_context = {
            "aggregate_metrics": {
                "total_revenue": total_revenue,
                "total_expenses": total_expenses,
                "net_profit": net_profit,
                "profit_margin": round(profit_margin, 2),
            },
            "top_files": top_files,
        }

        agent = HospitalKPIAgent()
        summary = agent.generate_report_summary(report_context)

        data = [
            {
                "id": s.id,
                "file_name": s.file_name,
                "upload_date": s.upload_date,
                "closing_balance": float(getattr(s, "closing_balance", 0) or 0),
                "is_processed": getattr(s, "is_processed", False),
            }
            for s in statements
        ]
        return Response({"statements": data, "summary": summary}, status=drf_status.HTTP_200_OK)

@login_required
def download_report_view(request):
    timeframe = request.GET.get('timeframe', 'all')
    statements = BankStatement.objects.filter(uploaded_by=request.user, is_processed=True).order_by("-upload_date")
    
    if timeframe == 'last_30_days':
        thirty_days_ago = timezone.now() - datetime.timedelta(days=30)
        statements = statements.filter(upload_date__gte=thirty_days_ago)
        timeframe_label = "Last 30 Days"
    elif timeframe == 'ytd':
        today = timezone.now()
        start_of_year = datetime.datetime(today.year, 1, 1, tzinfo=timezone.utc)
        statements = statements.filter(upload_date__gte=start_of_year)
        timeframe_label = "Year to Date"
    else:
        timeframe_label = "All Time"

    if not statements.exists():
        return HttpResponse("No processed statements found for the selected timeframe.", status=400)

    # Collect data logic matching _build_report_context
    aggregate_metrics = {}
    total_rev = KPIMetric.objects.filter(bank_statement__in=statements, metric_name="Total Revenue").aggregate(Sum("current_value"))["current_value__sum"] or 0
    total_exp = KPIMetric.objects.filter(bank_statement__in=statements, metric_name="Total Expenses").aggregate(Sum("current_value"))["current_value__sum"] or 0
    aggregate_metrics["total_revenue"] = total_rev
    aggregate_metrics["total_expenses"] = total_exp
    aggregate_metrics["net_profit"] = total_rev - total_exp
    aggregate_metrics["profit_margin"] = ((total_rev - total_exp) / total_rev * 100) if total_rev else 0

    top_statements = []
    for st in statements[:5]:
        top_statements.append({"file_name": st.file_name, "closing_balance": st.closing_balance})

    context_payload = {
        "aggregate_metrics": aggregate_metrics,
        "top_files": top_statements,
        "total_statements_processed": statements.count()
    }

    agent = HospitalKPIAgent()
    md_report = agent.generate_detailed_report(context_payload)

    # Convert Markdown to HTML
    html_content = markdown.markdown(md_report, extensions=["tables"])

    # Render Django Template
    template = get_template("report_pdf.html")
    html_string = template.render({
        "report_html": html_content,
        "current_date": timezone.now().strftime("%B %d, %Y"),
        "timeframe_label": timeframe_label
    })

    # Generate PDF
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="KPI_Financial_Report_{timeframe_label.replace(" ", "_")}.pdf"'

    pisa_status = pisa.CreatePDF(html_string, dest=response)
    if pisa_status.err:
        return HttpResponse('We had some errors generating the PDF', status=500)
    _audit(request, "EXPORT", f"PDF report downloaded — {timeframe_label}")
    return response
# ──────────────────────────────────────────────

@login_required(login_url="login")
def export_transactions_csv(request, statement_id):
    """Export all transactions for a statement as a CSV file."""
    statement = get_object_or_404(BankStatement, id=statement_id, uploaded_by=request.user)
    transactions = FinancialTransaction.objects.filter(bank_statement=statement).order_by("transaction_date")

    response = HttpResponse(content_type="text/csv")
    safe_name = statement.file_name.replace('"', "'")
    response["Content-Disposition"] = f'attachment; filename="transactions_{statement_id}.csv"'

    writer = csv.writer(response)
    writer.writerow(["Date", "Description", "Type", "Category", "Amount (KES)", "Running Balance"])
    for tx in transactions:
        writer.writerow([
            tx.transaction_date,
            tx.description,
            tx.transaction_type,
            tx.category or "",
            f"{tx.amount:.2f}",
            f"{tx.running_balance:.2f}" if tx.running_balance is not None else "",
        ])

    _audit(request, "EXPORT", f"CSV export for statement id={statement_id}")
    return response


@login_required(login_url="login")
def export_kpis_csv(request, statement_id):
    """Export KPI metrics for a statement as a CSV file."""
    statement = get_object_or_404(BankStatement, id=statement_id, uploaded_by=request.user)
    kpis = KPIMetric.objects.filter(bank_statement=statement).order_by("metric_type", "metric_name")

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="kpis_{statement_id}.csv"'

    writer = csv.writer(response)
    writer.writerow(["Metric", "Type", "Value", "Unit", "Status", "Description"])
    for kpi in kpis:
        writer.writerow([
            kpi.metric_name,
            kpi.metric_type,
            f"{kpi.current_value:.2f}",
            kpi.unit,
            kpi.status,
            kpi.description or "",
        ])

    _audit(request, "EXPORT", f"KPI CSV export for statement id={statement_id}")
    return response


# ──────────────────────────────────────────────
# Health check
# ──────────────────────────────────────────────

def health_check(request):
    """Lightweight health check for uptime monitors and load balancers."""
    try:
        BankStatement.objects.exists()
        db_ok = True
    except Exception:
        db_ok = False
    payload = {"status": "ok" if db_ok else "degraded", "db": "ok" if db_ok else "error"}
    return JsonResponse(payload, status=200 if db_ok else 503)


# ──────────────────────────────────────────────
# Statement status polling (real-time processing)
# ──────────────────────────────────────────────

@login_required(login_url="login")
def statement_status(request, statement_id):
    """Return processing status for a statement (used by upload modal polling)."""
    statement = get_object_or_404(BankStatement, id=statement_id, uploaded_by=request.user)
    return JsonResponse({
        "id": statement.id,
        "is_processed": statement.is_processed,
        "processing_error": statement.processing_error or "",
        "total_deposits": float(statement.total_deposits or 0),
        "total_withdrawals": float(statement.total_withdrawals or 0),
        "closing_balance": float(statement.closing_balance or 0),
        "tx_count": statement.transactions.count(),
    })


# ──────────────────────────────────────────────
# XLSX Export
# ──────────────────────────────────────────────

@login_required(login_url="login")
def export_transactions_xlsx(request, statement_id):
    """Export all transactions for a statement as a formatted Excel file."""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter
    except ImportError:
        return HttpResponse("openpyxl not installed.", status=500)

    statement = get_object_or_404(BankStatement, id=statement_id, uploaded_by=request.user)
    transactions = FinancialTransaction.objects.filter(bank_statement=statement).order_by("transaction_date")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Transactions"

    # Header style
    header_fill = PatternFill("solid", fgColor="1A4D3E")
    header_font = Font(bold=True, color="FFFFFF", name="Calibri")
    header_align = Alignment(horizontal="center", vertical="center")
    thin = Side(style="thin", color="D0D0D0")
    cell_border = Border(left=thin, right=thin, bottom=thin)

    headers = ["Date", "Description", "Type", "Category", "Amount (KES)", "Running Balance"]
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = header_align

    # Data rows
    deposit_fill = PatternFill("solid", fgColor="E8F5EE")
    withdraw_fill = PatternFill("solid", fgColor="FFF3E0")
    money_fmt = '#,##0.00'

    for row_num, tx in enumerate(transactions, 2):
        fill = deposit_fill if tx.transaction_type == "DEPOSIT" else withdraw_fill
        row = [
            tx.transaction_date,
            tx.description,
            tx.get_transaction_type_display(),
            tx.category or "",
            float(tx.amount),
            float(tx.running_balance) if tx.running_balance is not None else "",
        ]
        for col, val in enumerate(row, 1):
            cell = ws.cell(row=row_num, column=col, value=val)
            cell.fill = fill
            cell.border = cell_border
            if col in (5, 6):
                cell.number_format = money_fmt

    # Column widths
    widths = [14, 50, 14, 22, 18, 18]
    for col, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(col)].width = w

    # Add summary sheet
    ws2 = wb.create_sheet("Summary")
    ws2["A1"] = "Statement"
    ws2["B1"] = statement.file_name
    ws2["A2"] = "Period"
    ws2["B2"] = f"{statement.statement_period_start} – {statement.statement_period_end}"
    ws2["A3"] = "Total Deposits (KES)"
    ws2["B3"] = float(statement.total_deposits or 0)
    ws2["B3"].number_format = money_fmt
    ws2["A4"] = "Total Withdrawals (KES)"
    ws2["B4"] = float(statement.total_withdrawals or 0)
    ws2["A5"] = "Closing Balance (KES)"
    ws2["B5"] = float(statement.closing_balance or 0)
    ws2["A6"] = "Transaction Count"
    ws2["B6"] = transactions.count()

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)

    response = HttpResponse(
        buf.read(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="transactions_{statement_id}.xlsx"'
    _audit(request, "EXPORT", f"XLSX export for statement id={statement_id}")
    return response


# ──────────────────────────────────────────────
# Manual transaction category correction
# ──────────────────────────────────────────────

@login_required(login_url="login")
@require_http_methods(["POST"])
def update_transaction_category(request, tx_id):
    """Update the category of a single transaction (inline edit)."""
    data = json.loads(request.body)
    new_category = str(data.get("category", "")).strip()[:100]

    tx = get_object_or_404(
        FinancialTransaction,
        id=tx_id,
        bank_statement__uploaded_by=request.user,
    )
    old_category = tx.category or ""
    tx.category = new_category
    tx.save(update_fields=["category"])
    _audit(request, "CATEGORY_EDIT",
           f"tx={tx_id}: '{old_category}' → '{new_category}'")
    return JsonResponse({"success": True, "category": new_category})


# ──────────────────────────────────────────────
# Statement Tagging
# ──────────────────────────────────────────────

@login_required(login_url="login")
def tags_view(request):
    """Manage statement tags."""
    tags = StatementTag.objects.filter(user=request.user)
    ctx = _base_context(request)
    ctx.update({"tags": tags, "active_page": "settings"})
    return render(request, "tags.html", ctx)


@login_required(login_url="login")
@require_http_methods(["POST"])
def create_tag(request):
    data = json.loads(request.body)
    name = str(data.get("name", "")).strip()[:50]
    color = str(data.get("color", "#2C7A5C")).strip()[:7]
    if not name:
        return JsonResponse({"error": "Name required."}, status=400)
    tag, created = StatementTag.objects.get_or_create(user=request.user, name=name, defaults={"color": color})
    if not created:
        return JsonResponse({"error": "Tag already exists."}, status=400)
    return JsonResponse({"id": tag.id, "name": tag.name, "color": tag.color})


@login_required(login_url="login")
@require_http_methods(["POST"])
def delete_tag(request, tag_id):
    tag = get_object_or_404(StatementTag, id=tag_id, user=request.user)
    tag.delete()
    return JsonResponse({"success": True})


@login_required(login_url="login")
@require_http_methods(["POST"])
def toggle_statement_tag(request, statement_id):
    """Add or remove a tag from a statement."""
    statement = get_object_or_404(BankStatement, id=statement_id, uploaded_by=request.user)
    data = json.loads(request.body)
    tag_id = data.get("tag_id")
    tag = get_object_or_404(StatementTag, id=tag_id, user=request.user)
    tagging, created = StatementTagging.objects.get_or_create(statement=statement, tag=tag)
    if not created:
        tagging.delete()
        return JsonResponse({"action": "removed", "tag": tag.name})
    return JsonResponse({"action": "added", "tag": tag.name})


# ──────────────────────────────────────────────
# Budget / Target Tracking
# ──────────────────────────────────────────────

@login_required(login_url="login")
def budget_targets_view(request):
    """View and set monthly budget targets."""
    today = datetime.date.today()
    targets = BudgetTarget.objects.filter(user=request.user).order_by(
        "-period_year", "-period_month"
    )

    # For current month comparisons
    current_month_targets = targets.filter(
        period_year=today.year, period_month=today.month
    )

    # Actuals from latest processed statements
    actuals = {}
    latest_stmt = BankStatement.objects.filter(
        uploaded_by=request.user, is_processed=True
    ).order_by("-upload_date").first()
    if latest_stmt:
        actuals["REVENUE"] = float(latest_stmt.total_deposits or 0)
        actuals["EXPENSES"] = float(latest_stmt.total_withdrawals or 0)
        rev = actuals["REVENUE"]
        exp = actuals["EXPENSES"]
        actuals["NET_INCOME"] = rev - exp
        actuals["PROFIT_MARGIN"] = round((rev - exp) / rev * 100, 2) if rev else 0

    # Build summary cards for current month
    current_month_cards = []
    for code, label in BudgetTarget.METRIC_CHOICES:
        target = current_month_targets.filter(metric=code).first()
        actual_val = actuals.get(code)
        if target:
            if code == "PROFIT_MARGIN":
                tdisp = f"{float(target.target_value):.1f}%"
                adisp = f"{actual_val:.1f}%" if actual_val is not None else ""
            else:
                tdisp = f"KES {float(target.target_value):,.0f}"
                adisp = f"KES {actual_val:,.0f}" if actual_val is not None else ""
            current_month_cards.append({
                "label": label, "has_target": True,
                "target_display": tdisp, "actual_display": adisp,
            })
        else:
            current_month_cards.append({"label": label, "has_target": False})

    ctx = _base_context(request)
    ctx.update({
        "targets": targets,
        "current_month_targets": current_month_targets,
        "current_month_cards": current_month_cards,
        "actuals": actuals,
        "today": today,
        "active_page": "budget",
        "metric_choices": BudgetTarget.METRIC_CHOICES,
    })
    return render(request, "budget_targets.html", ctx)


@login_required(login_url="login")
@require_http_methods(["POST"])
def save_budget_target(request):
    data = json.loads(request.body)
    metric = str(data.get("metric", "")).upper()
    valid_metrics = [c[0] for c in BudgetTarget.METRIC_CHOICES]
    if metric not in valid_metrics:
        return JsonResponse({"error": "Invalid metric."}, status=400)
    try:
        target_value = Decimal(str(data.get("target_value", 0)))
        month = int(data.get("month", datetime.date.today().month))
        year = int(data.get("year", datetime.date.today().year))
    except (ValueError, InvalidOperation):
        return JsonResponse({"error": "Invalid values."}, status=400)

    target, created = BudgetTarget.objects.update_or_create(
        user=request.user,
        metric=metric,
        period_month=month,
        period_year=year,
        defaults={
            "target_value": target_value,
            "notes": str(data.get("notes", ""))[:500],
            "currency": django_settings.BASE_CURRENCY,
        },
    )
    _audit(request, "BUDGET_SET", f"{metric} target={target_value} for {month}/{year}")
    return JsonResponse({
        "success": True,
        "id": target.id,
        "metric": metric,
        "target_value": float(target.target_value),
        "created": created,
    })


@login_required(login_url="login")
@require_http_methods(["POST"])
def delete_budget_target(request, target_id):
    target = get_object_or_404(BudgetTarget, id=target_id, user=request.user)
    target.delete()
    return JsonResponse({"success": True})


# ──────────────────────────────────────────────
# Audit Log UI
# ──────────────────────────────────────────────

@login_required(login_url="login")
def audit_log_view(request):
    """Display audit log with search and filters."""
    logs = AuditLog.objects.filter(user=request.user).order_by("-created_at")

    action_filter = request.GET.get("action", "").strip()
    if action_filter:
        logs = logs.filter(action=action_filter)

    search_q = request.GET.get("q", "").strip()
    if search_q:
        logs = logs.filter(detail__icontains=search_q)

    paginator = Paginator(logs, 25)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    ctx = _base_context(request)
    ctx.update({
        "page_obj": page_obj,
        "action_filter": action_filter,
        "search_q": search_q,
        "action_choices": AuditLog.ACTION_CHOICES,
        "active_page": "audit",
    })
    return render(request, "audit_log.html", ctx)


# ──────────────────────────────────────────────
# Gap Detection
# ──────────────────────────────────────────────

@login_required(login_url="login")
def gap_detection_api(request):
    """Return list of missing months in the uploaded statement range."""
    statements = BankStatement.objects.filter(
        uploaded_by=request.user, is_processed=True
    ).order_by("statement_period_start")

    covered = set()
    for s in statements:
        if s.statement_period_start and s.statement_period_end:
            d = s.statement_period_start.replace(day=1)
            end = s.statement_period_end.replace(day=1)
            while d <= end:
                covered.add((d.year, d.month))
                # advance month
                if d.month == 12:
                    d = d.replace(year=d.year + 1, month=1)
                else:
                    d = d.replace(month=d.month + 1)

    gaps = []
    if covered:
        min_ym = min(covered)
        max_ym = max(covered)
        y, m = min_ym
        while (y, m) <= max_ym:
            if (y, m) not in covered:
                gaps.append(f"{datetime.date(y, m, 1).strftime('%B %Y')}")
            m += 1
            if m > 12:
                m = 1
                y += 1

    return JsonResponse({"gaps": gaps, "total_covered": len(covered)})


# ──────────────────────────────────────────────
# Duplicate Detection
# ──────────────────────────────────────────────

@login_required(login_url="login")
def duplicate_detection_api(request):
    """Find transactions that appear identically in more than one statement."""
    from django.db.models import Count

    dupes = (
        FinancialTransaction.objects.filter(
            bank_statement__uploaded_by=request.user
        )
        .values("transaction_date", "description", "amount", "transaction_type")
        .annotate(cnt=Count("id"))
        .filter(cnt__gt=1)
        .order_by("-cnt")[:50]
    )

    results = [
        {
            "date": str(d["transaction_date"]),
            "description": d["description"],
            "amount": float(d["amount"]),
            "type": d["transaction_type"],
            "occurrences": d["cnt"],
        }
        for d in dupes
    ]
    return JsonResponse({"duplicates": results, "count": len(results)})


# ──────────────────────────────────────────────
# Two-Factor Authentication
# ──────────────────────────────────────────────

@login_required(login_url="login")
def two_factor_setup(request):
    """Setup or view 2FA TOTP configuration."""
    try:
        import pyotp
        import qrcode
    except ImportError:
        return HttpResponse("2FA dependencies not installed.", status=500)

    profile, _ = TwoFactorProfile.objects.get_or_create(user=request.user)

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "generate":
            profile.totp_secret = pyotp.random_base32()
            profile.is_enabled = False
            profile.save()
            return redirect("two_factor_setup")

        elif action == "verify":
            code = request.POST.get("code", "").strip()
            totp = pyotp.TOTP(profile.totp_secret)
            if totp.verify(code):
                # Generate backup codes
                backup_codes = [
                    "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
                    for _ in range(8)
                ]
                profile.is_enabled = True
                profile.backup_codes = backup_codes
                profile.save()
                return render(request, "two_factor_setup.html", {
                    **_base_context(request),
                    "profile": profile,
                    "backup_codes": backup_codes,
                    "just_enabled": True,
                    "active_page": "settings",
                })
            else:
                return render(request, "two_factor_setup.html", {
                    **_base_context(request),
                    "profile": profile,
                    "error": "Invalid code. Try again.",
                    "active_page": "settings",
                    **_get_qr_context(request, profile),
                })

        elif action == "disable":
            profile.is_enabled = False
            profile.totp_secret = ""
            profile.backup_codes = []
            profile.save()
            return redirect("two_factor_setup")

    ctx = {**_base_context(request), "profile": profile, "active_page": "settings"}
    if profile.totp_secret and not profile.is_enabled:
        ctx.update(_get_qr_context(request, profile))
    return render(request, "two_factor_setup.html", ctx)


def _get_qr_context(request, profile):
    import pyotp
    import qrcode
    totp = pyotp.TOTP(profile.totp_secret)
    uri = totp.provisioning_uri(
        name=request.user.email or request.user.username,
        issuer_name="KPIConsole",
    )
    img = qrcode.make(uri)
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    qr_b64 = base64.b64encode(buf.read()).decode()
    return {"qr_code": qr_b64, "totp_secret": profile.totp_secret}


# ──────────────────────────────────────────────
# PWA Manifest & Service Worker
# ──────────────────────────────────────────────

def pwa_manifest(request):
    manifest = {
        "name": "KPIConsole",
        "short_name": "KPIConsole",
        "description": "Hospital Financial Intelligence",
        "start_url": "/dashboard/",
        "display": "standalone",
        "background_color": "#F7F8F5",
        "theme_color": "#1A4D3E",
        "icons": [
            {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
    }
    return JsonResponse(manifest, content_type="application/manifest+json")


def service_worker(request):
    sw_js = """
const CACHE = 'kpiconsole-v1';
const OFFLINE = ['/dashboard/', '/statements/', '/assistant/'];

self.addEventListener('install', e => {
    e.waitUntil(caches.open(CACHE).then(c => c.addAll(OFFLINE)));
    self.skipWaiting();
});
self.addEventListener('activate', e => {
    e.waitUntil(caches.keys().then(keys =>
        Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ));
    self.clients.claim();
});
self.addEventListener('fetch', e => {
    if (e.request.method !== 'GET') return;
    e.respondWith(
        fetch(e.request).catch(() => caches.match(e.request))
    );
});
"""
    return HttpResponse(sw_js, content_type="application/javascript")


# ──────────────────────────────────────────────
# Report Builder
# ──────────────────────────────────────────────

@login_required(login_url="login")
def report_builder_view(request):
    """Report builder configuration page."""
    statements = BankStatement.objects.filter(
        uploaded_by=request.user, is_processed=True
    ).order_by("-statement_period_end")

    ctx = _base_context(request)
    ctx.update({
        "statements": statements,
        "active_page": "report",
    })
    return render(request, "report_builder.html", ctx)


@login_required(login_url="login")
def generate_report_pdf(request):
    """Generate a PDF report based on user-selected options."""
    if request.method != "POST":
        return redirect("report_builder")

    # Parse options from form
    include_kpi_scorecard   = request.POST.get("include_kpi_scorecard") == "on"
    include_rev_exp_chart   = request.POST.get("include_rev_exp_chart") == "on"
    include_profit_chart    = request.POST.get("include_profit_chart") == "on"
    include_expense_mix     = request.POST.get("include_expense_mix") == "on"
    include_tx_table        = request.POST.get("include_tx_table") == "on"
    include_anomaly_report  = request.POST.get("include_anomaly_report") == "on"
    include_projections     = request.POST.get("include_projections") == "on"
    include_ai_narrative    = request.POST.get("include_ai_narrative") == "on"
    statement_ids_raw       = request.POST.getlist("statement_ids")
    date_from_raw           = request.POST.get("date_from", "").strip()
    date_to_raw             = request.POST.get("date_to", "").strip()

    # Build queryset
    statements = BankStatement.objects.filter(
        uploaded_by=request.user, is_processed=True
    ).order_by("statement_period_start")

    if statement_ids_raw:
        statements = statements.filter(id__in=[int(i) for i in statement_ids_raw if i.isdigit()])
    if date_from_raw:
        statements = statements.filter(statement_period_start__gte=date_from_raw)
    if date_to_raw:
        statements = statements.filter(statement_period_end__lte=date_to_raw)

    if not statements.exists():
        return HttpResponse("No statements match the selected criteria.", status=400)

    # Aggregate metrics
    total_rev  = float(statements.aggregate(Sum("total_deposits"))["total_deposits__sum"] or 0)
    total_exp  = float(statements.aggregate(Sum("total_withdrawals"))["total_withdrawals__sum"] or 0)
    net_income = total_rev - total_exp
    profit_margin = round((net_income / total_rev * 100), 2) if total_rev else 0

    # Build chart data
    sorted_stmts = list(statements.order_by("statement_period_start"))
    labels = [
        s.statement_period_start.strftime("%b %Y") if s.statement_period_start else str(s.upload_date.date())
        for s in sorted_stmts
    ]
    revenue_series = [float(s.total_deposits or 0) for s in sorted_stmts]
    expense_series = [float(s.total_withdrawals or 0) for s in sorted_stmts]

    cumulative_profit = []
    running = 0.0
    for r, e in zip(revenue_series, expense_series):
        running += (r - e)
        cumulative_profit.append(round(running, 2))

    # Expense mix for latest statement
    latest_s = sorted_stmts[-1]
    expense_txns = FinancialTransaction.objects.filter(
        bank_statement=latest_s, transaction_type="WITHDRAWAL"
    )
    expense_map = {}
    for tx in expense_txns:
        cat = (tx.category or tx.description.split()[0] if tx.description else "Other")
        expense_map[cat] = expense_map.get(cat, 0) + float(tx.amount)
    exp_labels = list(expense_map.keys())[:10]
    exp_values = [expense_map[k] for k in exp_labels]

    # KPI data for latest statement
    kpis_qs = KPIMetric.objects.filter(bank_statement=latest_s).order_by("metric_type", "metric_name")
    kpi_rows = [(k.metric_name, float(k.current_value), k.unit, k.status) for k in kpis_qs]

    # Transaction sample
    tx_sample = []
    if include_tx_table:
        for s in sorted_stmts[-3:]:
            for tx in FinancialTransaction.objects.filter(bank_statement=s).order_by("-transaction_date")[:30]:
                tx_sample.append({
                    "date": tx.transaction_date,
                    "description": tx.description[:60],
                    "type": tx.transaction_type,
                    "amount": float(tx.amount),
                    "category": tx.category or "",
                })

    # Anomalies
    anomalies = []
    if include_anomaly_report:
        for s in sorted_stmts:
            m = KPIMetric.objects.filter(bank_statement=s, metric_name="Anomaly Count").first()
            if m and float(m.current_value) > 0:
                anomalies.append({
                    "period": s.statement_period_start.strftime("%b %Y") if s.statement_period_start else str(s.upload_date.date()),
                    "count": int(float(m.current_value)),
                    "file": s.file_name,
                })

    # Projections (linear)
    proj_labels = labels.copy()
    rev_proj = expense_proj = []
    if include_projections and len(revenue_series) >= 2:
        steps = 2
        n = len(revenue_series)
        xs = list(range(n))
        x_mean = sum(xs) / n

        def _proj(data):
            y_mean = sum(data) / n
            num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, data))
            den = sum((x - x_mean) ** 2 for x in xs)
            slope = num / den if den else 0
            intercept = y_mean - slope * x_mean
            return [round(intercept + slope * (n + i), 2) for i in range(steps)]

        last_date = sorted_stmts[-1].statement_period_start or datetime.date.today()
        for i in range(1, 3):
            nd = last_date + datetime.timedelta(days=32 * i)
            proj_labels.append(nd.strftime("%b %Y (P)"))
        rev_proj = _proj(revenue_series)
        expense_proj = _proj(expense_series)

    # Generate base64 charts
    from .chart_utils import (
        revenue_vs_expenses_chart, cumulative_profit_chart,
        expense_mix_chart, kpi_scorecard_chart,
    )

    charts = {}
    if include_rev_exp_chart and labels:
        charts["rev_exp"] = revenue_vs_expenses_chart(labels, revenue_series, expense_series)
    if include_profit_chart and cumulative_profit:
        charts["profit"] = cumulative_profit_chart(labels, cumulative_profit)
    if include_expense_mix and exp_labels:
        charts["expense_mix"] = expense_mix_chart(exp_labels, exp_values)
    if include_kpi_scorecard and kpi_rows:
        kpi_names = [r[0] for r in kpi_rows[:15]]
        kpi_values = [r[1] for r in kpi_rows[:15]]
        kpi_statuses = [r[3] for r in kpi_rows[:15]]
        charts["kpi_scorecard"] = kpi_scorecard_chart(kpi_names, kpi_values, kpi_statuses)

    # AI narrative
    ai_narrative = ""
    if include_ai_narrative:
        agent = HospitalKPIAgent()
        context_payload = {
            "aggregate_metrics": {
                "total_revenue": total_rev,
                "total_expenses": total_exp,
                "net_profit": net_income,
                "profit_margin": profit_margin,
            },
            "top_files": [{"file_name": s.file_name, "closing_balance": float(s.closing_balance or 0)} for s in sorted_stmts[-5:]],
            "total_statements_processed": len(sorted_stmts),
        }
        ai_narrative = agent.generate_detailed_report(context_payload)

    # Render HTML template
    report_ctx = {
        "generated_at": timezone.now().strftime("%B %d, %Y at %H:%M"),
        "user": request.user,
        "statement_count": len(sorted_stmts),
        "date_range": f"{sorted_stmts[0].statement_period_start} – {sorted_stmts[-1].statement_period_end}",
        "total_rev": total_rev,
        "total_exp": total_exp,
        "net_income": net_income,
        "profit_margin": profit_margin,
        "charts": charts,
        "kpi_rows": kpi_rows,
        "tx_sample": tx_sample,
        "anomalies": anomalies,
        "proj_labels": proj_labels,
        "revenue_proj_full": revenue_series + rev_proj,
        "expense_proj_full": expense_series + expense_proj,
        "ai_narrative_html": markdown.markdown(ai_narrative, extensions=["tables"]) if ai_narrative else "",
        "include_kpi_scorecard": include_kpi_scorecard,
        "include_tx_table": include_tx_table,
        "include_anomaly_report": include_anomaly_report,
        "include_projections": include_projections,
        "include_ai_narrative": include_ai_narrative,
    }

    html_string = render_to_string("report_pdf_builder.html", report_ctx)

    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="KPI_Report.pdf"'
    pisa_status = pisa.CreatePDF(html_string, dest=response)
    if pisa_status.err:
        return HttpResponse("Error generating PDF.", status=500)

    _audit(request, "EXPORT", f"Report Builder PDF — {len(sorted_stmts)} statements")
    return response


# ──────────────────────────────────────────────
# Queue management
# ──────────────────────────────────────────────

@login_required(login_url="login")
def flush_queue_view(request):
    """Delete all stale OrmQ tasks (fixes BadSignature errors after restarts)."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        from django_q.models import OrmQ
        deleted, _ = OrmQ.objects.all().delete()
        return JsonResponse({"success": True, "deleted": deleted})
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)}, status=500)


# Multi-currency conversion API
# ──────────────────────────────────────────────

@login_required(login_url="login")
def currency_rates_view(request):
    """Return supported currencies; actual rate conversion is client-side or via future API."""
    base = getattr(django_settings, "BASE_CURRENCY", "KES")
    supported = getattr(django_settings, "SUPPORTED_CURRENCIES", ["KES", "USD", "EUR", "GBP"])
    return JsonResponse({"base": base, "supported": supported})


# ──────────────────────────────────────────────
# REST API — DRF endpoints
# ──────────────────────────────────────────────

class StatementListAPI(APIView):
    """GET /api/statements/ — list all statements for the authenticated user."""

    def get(self, request):
        stmts = BankStatement.objects.filter(uploaded_by=request.user).order_by("-upload_date")[:50]
        data = [
            {
                "id": s.id,
                "file_name": s.file_name,
                "upload_date": s.upload_date.isoformat(),
                "period_start": str(s.statement_period_start),
                "period_end": str(s.statement_period_end),
                "is_processed": s.is_processed,
                "total_deposits": float(s.total_deposits or 0),
                "total_withdrawals": float(s.total_withdrawals or 0),
                "closing_balance": float(s.closing_balance or 0),
            }
            for s in stmts
        ]
        return Response({"count": len(data), "results": data})


class TransactionListAPI(APIView):
    """GET /api/statements/<id>/transactions/ — list transactions."""

    def get(self, request, statement_id):
        stmt = get_object_or_404(BankStatement, id=statement_id, uploaded_by=request.user)
        txs = FinancialTransaction.objects.filter(bank_statement=stmt).order_by("-transaction_date")
        search = request.query_params.get("search", "")
        tx_type = request.query_params.get("type", "").upper()
        if search:
            txs = txs.filter(Q(description__icontains=search) | Q(category__icontains=search))
        if tx_type in ("DEPOSIT", "WITHDRAWAL", "TRANSFER"):
            txs = txs.filter(transaction_type=tx_type)
        page = int(request.query_params.get("page", 1))
        page_size = min(int(request.query_params.get("page_size", 50)), 200)
        paginator = Paginator(txs, page_size)
        page_obj = paginator.get_page(page)
        data = [
            {
                "id": t.id,
                "date": str(t.transaction_date),
                "description": t.description,
                "amount": float(t.amount),
                "type": t.transaction_type,
                "category": t.category or "",
                "running_balance": float(t.running_balance) if t.running_balance else None,
            }
            for t in page_obj
        ]
        return Response({
            "count": paginator.count,
            "page": page,
            "total_pages": paginator.num_pages,
            "results": data,
        })


class KPIListAPI(APIView):
    """GET /api/statements/<id>/kpis/ — list KPI metrics."""

    def get(self, request, statement_id):
        stmt = get_object_or_404(BankStatement, id=statement_id, uploaded_by=request.user)
        kpis = KPIMetric.objects.filter(bank_statement=stmt).order_by("metric_type", "metric_name")
        data = [
            {
                "id": k.id,
                "name": k.metric_name,
                "type": k.metric_type,
                "value": float(k.current_value),
                "unit": k.unit,
                "status": k.status,
                "description": k.description or "",
            }
            for k in kpis
        ]
        return Response({"count": len(data), "results": data})


# ──────────────────────────────────────────────
# Sub-Account Management
# ──────────────────────────────────────────────

@login_required(login_url="login")
def accounts_view(request):
    """Manage linked sub-accounts."""
    sub_accounts = SubAccount.objects.filter(owner=request.user)
    ctx = _base_context(request)
    ctx.update({
        "sub_accounts": sub_accounts,
        "industry_sectors": INDUSTRY_SECTORS,
        "active_page": "accounts",
    })
    return render(request, "accounts.html", ctx)


@login_required(login_url="login")
@require_POST
def create_subaccount_view(request):
    name     = request.POST.get("name", "").strip()
    industry = request.POST.get("industry", "HOSPITAL")
    contact_name  = request.POST.get("contact_name", "").strip()
    contact_email = request.POST.get("contact_email", "").strip()

    if not name:
        from django.contrib import messages
        messages.error(request, "Account name is required.")
        return redirect("accounts")

    SubAccount.objects.create(
        owner=request.user, name=name, industry=industry,
        contact_name=contact_name, contact_email=contact_email,
    )
    _audit(request, "UPLOAD", f"Created sub-account: {name}")
    return redirect("accounts")


@login_required(login_url="login")
@require_POST
def delete_subaccount_view(request, sub_id):
    sub = SubAccount.objects.filter(pk=sub_id, owner=request.user).first()
    if sub:
        if request.session.get("active_sub_account_id") == sub_id:
            request.session.pop("active_sub_account_id", None)
        sub.delete()
    return redirect("accounts")


@login_required(login_url="login")
@require_POST
def switch_account_view(request):
    """Switch the active account in session."""
    sub_id = request.POST.get("sub_id", "primary")
    if sub_id == "primary":
        request.session.pop("active_sub_account_id", None)
    else:
        try:
            sub_id_int = int(sub_id)
            SubAccount.objects.get(pk=sub_id_int, owner=request.user, is_active=True)
            request.session["active_sub_account_id"] = sub_id_int
        except (ValueError, SubAccount.DoesNotExist):
            request.session.pop("active_sub_account_id", None)
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or "/"
    return redirect(next_url)
