import json
import os

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.files.storage import default_storage
from django.core.paginator import Paginator
from django.db.models import Sum, Avg, Q
from django.http import JsonResponse, HttpResponseForbidden, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.template.loader import get_template
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
import datetime
from django.utils import timezone
import markdown
import csv
from io import StringIO
from decimal import Decimal, InvalidOperation
from xhtml2pdf import pisa

from .models import BankStatement, KPIMetric, AIAnalysis, FinancialTransaction, UserProfile
from .pdf_extractor import BankStatementPDFExtractor, PDFPasswordRequired, PDFWrongPassword
from .ai_agent import HospitalKPIAgent

CURRENCY_SYMBOL = "KES"


def _base_context(request):
    """Return sidebar counts used on every page."""
    total = BankStatement.objects.filter(uploaded_by=request.user).count()
    processed = BankStatement.objects.filter(uploaded_by=request.user, is_processed=True).count()
    return {
        "total_statements": total,
        "processed_statements": processed,
        "currency_symbol": CURRENCY_SYMBOL,
    }


# ──────────────────────────────────────────────
# Auth
# ──────────────────────────────────────────────

def login_view(request):
    """Manager login page."""
    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")
        user = authenticate(request, username=username, password=password)

        if user is not None:
            if user.is_staff or hasattr(user, "userprofile"):
                login(request, user)
                return redirect("dashboard")
            else:
                return render(request, "login.html", {"error": "Only managers can access this system."})
        else:
            return render(request, "login.html", {"error": "Invalid credentials."})

    return render(request, "login.html")


def logout_view(request):
    """Logout user."""
    logout(request)
    return redirect("login")


# ──────────────────────────────────────────────
# Dashboard
# ──────────────────────────────────────────────

@login_required(login_url="login")
def dashboard_view(request):
    """Main KPI dashboard."""
    bank_statements = BankStatement.objects.filter(uploaded_by=request.user).order_by("-upload_date")
    latest_statement = bank_statements.first()

    # Get selected summary type from query params
    summary_type = request.GET.get("summary_type", "overview")

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
    summary_options = [
        {"key": "overview", "label": "Overview", "icon": "fa-chart-pie"},
        {"key": "revenue_sources", "label": "Revenue Sources", "icon": "fa-money-bill-trend-up"},
        {"key": "expense_breakdown", "label": "Expense Breakdown", "icon": "fa-receipt"},
        {"key": "transaction_patterns", "label": "Transaction Patterns", "icon": "fa-clock-rotate-left"},
        {"key": "top_insights", "label": "Top Insights", "icon": "fa-lightbulb"},
    ]

    if latest_statement:
        kpis = KPIMetric.objects.filter(bank_statement=latest_statement)
        alerts = AIAnalysis.objects.filter(
            bank_statement=latest_statement,
            analysis_type__in=["ALERT", "RECOMMENDATION"],
        ).order_by("-created_at")[:5]

        statements = list(bank_statements.order_by("statement_period_start"))
        overview_chart_labels = [
            s.statement_period_start.strftime("%b %Y") if s.statement_period_start else s.upload_date.strftime("%b %Y")
            for s in statements
        ]
        overview_revenue_series = [float(getattr(s, "total_deposits", 0) or 0) for s in statements]
        overview_expense_series = [float(getattr(s, "total_withdrawals", 0) or 0) for s in statements]

        # Expense mix for latest statement
        expenses = FinancialTransaction.objects.filter(
            bank_statement=latest_statement, transaction_type="WITHDRAWAL"
        )
        expense_map = {}
        for tx in expenses:
            cat = tx.description.split()[0] if tx.description else "Other"
            expense_map[cat] = expense_map.get(cat, 0) + float(tx.amount or 0)
        expense_labels = list(expense_map.keys())[:10]
        expense_values = [expense_map[k] for k in expense_labels]

        total_deposits = float(getattr(latest_statement, "total_deposits", 0) or 0)
        total_withdrawals = float(getattr(latest_statement, "total_withdrawals", 0) or 0)
        net_income = total_deposits - total_withdrawals
        closing_balance = float(getattr(latest_statement, "closing_balance", 0) or 0)

        summary_cards = [
            {
                "label": "Closing Balance",
                "value": f"{CURRENCY_SYMBOL} {closing_balance:,.2f}",
                "meta": "as of last statement",
                "icon": "fa-wallet",
                "tone": "green",
            },
            {
                "label": "Total Revenue",
                "value": f"{CURRENCY_SYMBOL} {total_deposits:,.2f}",
                "meta": "deposits this period",
                "icon": "fa-arrow-trend-up",
                "tone": "blue",
            },
            {
                "label": "Total Expenses",
                "value": f"{CURRENCY_SYMBOL} {total_withdrawals:,.2f}",
                "meta": "withdrawals this period",
                "icon": "fa-arrow-trend-down",
                "tone": "amber",
            },
            {
                "label": "Net Income",
                "value": f"{CURRENCY_SYMBOL} {net_income:,.2f}",
                "meta": "net period earnings",
                "icon": "fa-chart-line",
                "tone": "rose" if net_income < 0 else "green",
            },
        ]

        statement_rows = [
            {
                "id": s.id,
                "file_name": s.file_name,
                "period": (
                    f"{getattr(s, 'statement_period_start', '') or ''} – {getattr(s, 'statement_period_end', '') or ''}"
                ).strip(" –"),
                "closing_balance": float(getattr(s, "closing_balance", 0) or 0),
                "status": "Processed" if getattr(s, "is_processed", False) else "Pending",
            }
            for s in statements
        ]

        # Generate detailed summaries based on transaction analysis
        detailed_summaries = _generate_detailed_summaries(
            latest_statement, bank_statements, summary_type
        )

    ctx = _base_context(request)
    ctx.update({
        "bank_statements": bank_statements,
        "latest_statement": latest_statement,
        "kpis": kpis,
        "alerts": alerts,
        "overview_chart_labels_json": json.dumps(overview_chart_labels),
        "overview_revenue_series_json": json.dumps(overview_revenue_series),
        "overview_expense_series_json": json.dumps(overview_expense_series),
        "expense_labels_json": json.dumps(expense_labels),
        "expense_values_json": json.dumps(expense_values),
        "summary_cards": summary_cards,
        "statement_rows": statement_rows,
        "detailed_summaries": detailed_summaries,
        "summary_type": summary_type,
        "summary_options": summary_options,
        "active_page": "overview",
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
    """List all bank statements for the current user."""
    bank_statements = BankStatement.objects.filter(uploaded_by=request.user).order_by("-upload_date")
    latest_statement = bank_statements.first()

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

    # Paginate statements — 15 per page
    paginator = Paginator(all_statement_rows, 15)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    # Latest transactions for sidebar panel — paginated separately
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
        "statement_rows": page_obj,          # now a Page object
        "page_obj": page_obj,
        "tx_page_obj": tx_page_obj,
        "latest_transactions": tx_page_obj,
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
    transactions = FinancialTransaction.objects.filter(bank_statement=statement).order_by("-transaction_date")
    analyses = AIAnalysis.objects.filter(bank_statement=statement).order_by("-created_at")

    # Group KPIs by type
    kpi_groups = {}
    for kpi in kpis:
        kpi_groups.setdefault(kpi.metric_type, []).append(kpi)

    ctx = _base_context(request)
    ctx.update({
        "statement": statement,
        "kpis": kpis,
        "kpi_groups": kpi_groups,
        "transactions": transactions,
        "analyses": analyses,
        "active_page": "statements",
    })
    return render(request, "statement_detail.html", ctx)


# ──────────────────────────────────────────────
# KPI Comparison
# ──────────────────────────────────────────────

@login_required(login_url="login")
def kpi_comparison(request):
    """Compare KPIs across multiple statements."""
    statements = BankStatement.objects.filter(
        uploaded_by=request.user, is_processed=True
    ).order_by("-statement_period_end")[:6]

    kpi_comparison_data = {}
    for statement in statements:
        for kpi in KPIMetric.objects.filter(bank_statement=statement):
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
        if len(data_list) < 2: return [None, None]
        # Very simple average growth projection
        growths = [(data_list[i] - data_list[i-1]) for i in range(1, len(data_list))]
        avg_growth = sum(growths) / len(growths)
        last_val = data_list[-1]
        return [round(last_val + avg_growth, 2), round(last_val + 2 * avg_growth, 2)]

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

    # 4. Efficiency Matrix (Revenue per Transaction)
    # We'll calculate a few more metrics if missing (e.g. Total Revenue / Transaction Count)
    efficiency_data = []
    for s in sorted_view_statements:
        tx_count = FinancialTransaction.objects.filter(bank_statement=s).count() or 1
        rev = float(KPIMetric.objects.filter(bank_statement=s, metric_name="Total Revenue").first().current_value if KPIMetric.objects.filter(bank_statement=s, metric_name="Total Revenue").exists() else 0)
        efficiency_data.append(round(rev / tx_count, 2))

    # datasets
    colors = ["#2C7A5C", "#D97706", "#1A4D3E", "#5E907A", "#B3D0BE", "#F59E0B"]
    
    chart_datasets = []
    for i, metric in enumerate(key_metrics):
        points = [ {entry["period"]: entry["value"] for entry in kpi_comparison_data.get(metric, [])}.get(label, 0) for label in chart_labels ]
        chart_datasets.append({
            "label": metric,
            "data": points,
            "backgroundColor": colors[i % len(colors)],
            "borderColor": colors[i % len(colors)],
            "borderRadius": 8,
        })

    ctx = _base_context(request)
    ctx.update({
        "statements": statements,
        "kpi_comparison_json": json.dumps(kpi_comparison_data),
        "chart_labels_json": json.dumps(chart_labels),
        "chart_datasets_json": json.dumps(chart_datasets),
        "projection_labels_json": json.dumps(projection_labels),
        "revenue_projection_json": json.dumps(revenue_data + rev_proj),
        "expense_projection_json": json.dumps(expense_data + exp_proj),
        "cumulative_profit_json": json.dumps(cumulative_profit),
        "efficiency_data_json": json.dumps(efficiency_data),
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

        bank_statement = BankStatement.objects.create(
            uploaded_by=request.user,
            file_name=uploaded_file.name,
            file=uploaded_file,
        )

        try:
            uploaded_file.seek(0)

            if file_name.endswith(".csv"):
                # Smart CSV AI parser to handle any column structure securely without token truncation
                extracted_text = uploaded_file.read().decode('utf-8', errors='replace')
                bank_statement.extracted_text = extracted_text
                bank_statement.save()

                agent = HospitalKPIAgent()
                
                # Robustly detect delimiter
                sample = extracted_text[:2048]
                try:
                    dialect = csv.Sniffer().sniff(sample)
                    reader = csv.reader(StringIO(extracted_text), dialect)
                except csv.Error:
                    reader = csv.reader(StringIO(extracted_text))
                
                lines = list(reader)
                if not lines:
                    raise ValueError("The uploaded CSV is empty.")
                
                # Filter out empty rows
                lines = [l for l in lines if any(x.strip() for x in l)]
                
                if not lines:
                    raise ValueError("The uploaded CSV contains no data rows.")

                header_context = "\n".join([",".join(row) for row in lines[:10]])
                mapping = agent.extract_csv_structure_with_ai(header_context)
                print(f"DEBUG: Smart CSV Mapping found: {mapping}")

                if not mapping or mapping.get("date_index") is None or (mapping.get("amount_index") is None and mapping.get("credit_index") is None):
                    raise ValueError(f"AI could not determine the structure. Mapping response: {mapping}")

                transactions = []
                # Find where the data actually starts (it's not always row 1)
                start_row = 1
                for i, row in enumerate(lines[:5]):
                    # If we see a date-like thing in the date_index column, this might be a data row
                    val = row[mapping["date_index"]] if mapping["date_index"] < len(row) else ""
                    if BankStatementPDFExtractor._parse_date(val):
                        start_row = i
                        break
                
                print(f"DEBUG: Starting CSV parse at row {start_row}")

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

                print(f"DEBUG: Successfully extracted {len(transactions)} CSV transactions.")
                if not transactions:
                    raise ValueError("AI structured the columns but no valid transaction lines were extracted from the data.")

                # Sort by date regardless of statement order (ascending = oldest first)
                def _parse_tx_date(d):
                    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y"):
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

                if BankStatementPDFExtractor.needs_ocr(extracted_text):
                    # Scanned PDF — render pages then OCR via Claude Vision
                    agent_for_ocr = HospitalKPIAgent()
                    page_images = BankStatementPDFExtractor.render_pdf_pages_to_images(
                        uploaded_file, password=pdf_password
                    )
                    if page_images:
                        ocr_text = agent_for_ocr.ocr_pdf_pages(page_images)
                        if ocr_text:
                            extracted_text = ocr_text

                bank_statement.extracted_text = BankStatementPDFExtractor.clean_pdf_text(extracted_text)
                bank_statement.save()
                process_bank_statement_with_ai(bank_statement)

            return JsonResponse({
                "success": True,
                "message": "Statement uploaded and processed successfully.",
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
        bank_statement.opening_balance = financial_data.get("opening_balance", 0)
        bank_statement.closing_balance = financial_data.get("closing_balance", 0)

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
        bank_statement.save()

        # Save transactions
        for tx in financial_data.get("transactions", []):
            FinancialTransaction.objects.create(
                bank_statement=bank_statement,
                transaction_date=tx["date"],
                description=tx["description"],
                amount=tx["amount"],
                transaction_type=tx["type"],
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
        for alert in alerts:
            AIAnalysis.objects.create(
                bank_statement=bank_statement,
                analysis_type="ALERT",
                title=alert["title"],
                content=alert["content"],
                severity=alert["severity"],
            )

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

        try:
            # Build system-wide context
            all_statements = BankStatement.objects.filter(
                uploaded_by=request.user, is_processed=True
            ).order_by("-upload_date")

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
                for tx in FinancialTransaction.objects.filter(bank_statement=s).order_by("-transaction_date")[:50]:
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
        return Response({"statements": data, "summary": summary}, status=status.HTTP_200_OK)

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
        return HttpResponse('We had some errors generated the PDF', status=500)
    return response
