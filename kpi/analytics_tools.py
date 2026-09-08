"""Deterministic financial analysis tools.

Every number the assistant reports comes from this module. The language model
selects which tools to run and explains their output; it never computes,
adjusts, or infers a financial figure.

Tools take a normalised transaction list and return JSON-safe primitives, so
they are database-agnostic and directly testable. Outputs are deliberately
compact — they are fed to a 1B model running on a Raspberry Pi, so results are
truncated to a small top-N before they ever reach a prompt.
"""
from __future__ import annotations

import datetime as _dt
import re
import statistics
from collections import defaultdict
from typing import Any, Callable, Iterable

# Default number of rows returned to the model. Small on purpose: generation
# runs at ~13 tok/s on the Pi, so every extra row costs visible latency.
DEFAULT_TOP_N = 5
MAX_TOP_N = 25

_DEBIT_WORDS = ("WITHDRAWAL", "DEBIT", "DR", "EXPENSE", "OUT")
_CREDIT_WORDS = ("DEPOSIT", "CREDIT", "CR", "INCOME", "IN")

_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d")


# ── Normalisation ────────────────────────────────────────────────────────────

def _parse_date(value: Any) -> _dt.date | None:
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    if not value:
        return None
    text = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            return _dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _to_float(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        cleaned = re.sub(r"[^\d.\-]", "", str(value))
        return float(cleaned) if cleaned not in ("", "-", ".") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _direction(raw_type: Any) -> str:
    text = str(raw_type or "").upper()
    if any(w in text for w in _DEBIT_WORDS):
        return "debit"
    if any(w in text for w in _CREDIT_WORDS):
        return "credit"
    return "credit"


def normalize(transactions: Iterable[Any]) -> list[dict[str, Any]]:
    """Accept dicts or ORM objects and return one uniform shape.

    Rows without a usable amount are dropped; rows without a date are kept
    (some parsers omit it) but excluded from time-based tools.
    """
    rows: list[dict[str, Any]] = []
    for tx in transactions or []:
        if isinstance(tx, dict):
            get = tx.get
            raw_date = get("date") or get("transaction_date")
            raw_type = get("type") or get("transaction_type")
            balance = get("balance", get("running_balance"))
        else:
            get = lambda k, d=None: getattr(tx, k, d)  # noqa: E731
            raw_date = get("transaction_date")
            raw_type = get("transaction_type")
            balance = get("running_balance")

        amount = abs(_to_float(get("amount")))
        if amount <= 0:
            continue

        rows.append({
            "date": _parse_date(raw_date),
            "description": str(get("description") or "").strip() or "Transaction",
            "amount": amount,
            "direction": _direction(raw_type),
            "category": (str(get("category") or "").strip() or "Uncategorised"),
            "balance": None if balance in (None, "") else _to_float(balance),
        })
    return rows


def _debits(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["direction"] == "debit"]


def _credits(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["direction"] == "credit"]


def _pct(part: float, whole: float) -> float:
    return round(part / whole * 100, 1) if whole > 0 else 0.0


def _clamp_limit(limit: Any, default: int = DEFAULT_TOP_N) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, MAX_TOP_N))


def _month_key(d: _dt.date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _normalize_payee(description: str) -> str:
    """Collapse a description to a stable merchant-ish key.

    Strips receipt codes, dates, times, and trailing reference numbers so that
    'Sent to JOHN DOE 0722...' and 'Sent to JOHN DOE' group together.
    """
    text = description.upper()
    # M-PESA receipt codes are 10-12 chars mixing letters and digits. The digit
    # requirement matters: without it this also eats ordinary long words such as
    # "MAINTENANCE", which silently mangles category and payee names.
    text = re.sub(r"\b(?=[A-Z0-9]{10,12}\b)(?=[A-Z0-9]*\d)[A-Z0-9]+\b", " ", text)
    text = re.sub(r"\d{4}-\d{2}-\d{2}|\d{2}:\d{2}(:\d{2})?", " ", text)
    text = re.sub(r"\b\d{6,}\b", " ", text)                    # long refs/phones
    text = re.sub(r"[^A-Z& ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:40] or "UNKNOWN"


# ── Tools ────────────────────────────────────────────────────────────────────
# Each returns JSON-safe data only.

def get_transaction_summary(rows: list[dict], opening_balance: float | None = None,
                            closing_balance: float | None = None) -> dict:
    debits, credits = _debits(rows), _credits(rows)
    dated = sorted((r["date"] for r in rows if r["date"]))
    total_out = sum(r["amount"] for r in debits)
    total_in = sum(r["amount"] for r in credits)
    return {
        "date_from": dated[0].isoformat() if dated else None,
        "date_to": dated[-1].isoformat() if dated else None,
        "opening_balance": opening_balance,
        "closing_balance": closing_balance,
        "total_income": round(total_in, 2),
        "total_outflow": round(total_out, 2),
        "net_cashflow": round(total_in - total_out, 2),
        "transaction_count": len(rows),
        "average_debit": round(total_out / len(debits), 2) if debits else 0.0,
        "average_credit": round(total_in / len(credits), 2) if credits else 0.0,
    }


def _group_totals(rows: list[dict], key: Callable[[dict], str], whole: float,
                  limit: int) -> list[dict]:
    buckets: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        buckets[key(r)].append(r["amount"])
    ranked = sorted(
        ({"name": name,
          "total": round(sum(amts), 2),
          "transaction_count": len(amts),
          "pct": _pct(sum(amts), whole)}
         for name, amts in buckets.items()),
        key=lambda d: d["total"], reverse=True,
    )
    return ranked[:limit]


def get_spending_by_category(rows: list[dict], limit: int = DEFAULT_TOP_N) -> dict:
    debits = _debits(rows)
    total = sum(r["amount"] for r in debits)
    return {
        "total_outflow": round(total, 2),
        "categories": _group_totals(debits, lambda r: r["category"], total, _clamp_limit(limit)),
    }


def get_income_by_category(rows: list[dict], limit: int = DEFAULT_TOP_N) -> dict:
    credits = _credits(rows)
    total = sum(r["amount"] for r in credits)
    return {
        "total_income": round(total, 2),
        "categories": _group_totals(credits, lambda r: r["category"], total, _clamp_limit(limit)),
    }


def get_top_merchants_or_descriptions(rows: list[dict], limit: int = DEFAULT_TOP_N,
                                      direction: str = "debit") -> dict:
    subset = _debits(rows) if direction == "debit" else _credits(rows)
    total = sum(r["amount"] for r in subset)
    return {
        "direction": direction,
        "total": round(total, 2),
        "payees": _group_totals(subset, lambda r: _normalize_payee(r["description"]),
                                total, _clamp_limit(limit)),
    }


def get_income_sources(rows: list[dict], limit: int = DEFAULT_TOP_N) -> dict:
    result = get_top_merchants_or_descriptions(rows, limit=limit, direction="credit")
    return {"total_income": result["total"], "sources": result["payees"]}


def get_largest_transactions(rows: list[dict], direction: str = "debit",
                             limit: int = DEFAULT_TOP_N) -> dict:
    subset = _debits(rows) if direction == "debit" else _credits(rows)
    top = sorted(subset, key=lambda r: r["amount"], reverse=True)[:_clamp_limit(limit)]
    return {
        "direction": direction,
        "transactions": [
            {"date": r["date"].isoformat() if r["date"] else None,
             "description": r["description"][:70],
             "amount": round(r["amount"], 2),
             "direction": r["direction"],
             "category": r["category"]}
            for r in top
        ],
    }


def get_monthly_cashflow(rows: list[dict], limit: int = 12) -> dict:
    months: dict[str, dict] = defaultdict(
        lambda: {"total_income": 0.0, "total_outflow": 0.0, "transaction_count": 0})
    for r in rows:
        if not r["date"]:
            continue
        bucket = months[_month_key(r["date"])]
        bucket["transaction_count"] += 1
        if r["direction"] == "debit":
            bucket["total_outflow"] += r["amount"]
        else:
            bucket["total_income"] += r["amount"]

    ordered = []
    for month in sorted(months):
        b = months[month]
        ordered.append({
            "month": month,
            "total_income": round(b["total_income"], 2),
            "total_outflow": round(b["total_outflow"], 2),
            "net_cashflow": round(b["total_income"] - b["total_outflow"], 2),
            "transaction_count": b["transaction_count"],
        })
    return {"months": ordered[-_clamp_limit(limit, 12):]}


def get_highest_spending_month(rows: list[dict]) -> dict:
    months = get_monthly_cashflow(rows, limit=MAX_TOP_N)["months"]
    if not months:
        return {"month": None}
    peak = max(months, key=lambda m: m["total_outflow"])
    idx = months.index(peak)
    previous = months[idx - 1] if idx > 0 else None
    change = None
    if previous and previous["total_outflow"] > 0:
        change = round(
            (peak["total_outflow"] - previous["total_outflow"])
            / previous["total_outflow"] * 100, 1)
    return {
        "month": peak["month"],
        "outflow": peak["total_outflow"],
        "previous_month": previous["month"] if previous else None,
        "previous_outflow": previous["total_outflow"] if previous else None,
        "pct_change_vs_previous": change,
    }


def get_spending_trend(rows: list[dict]) -> dict:
    months = get_monthly_cashflow(rows, limit=MAX_TOP_N)["months"]
    trend = []
    for i, m in enumerate(months):
        change = None
        if i > 0 and months[i - 1]["total_outflow"] > 0:
            change = round(
                (m["total_outflow"] - months[i - 1]["total_outflow"])
                / months[i - 1]["total_outflow"] * 100, 1)
        trend.append({"month": m["month"], "outflow": m["total_outflow"],
                      "pct_change": change})
    direction = None
    if len(trend) >= 2:
        first, last = trend[0]["outflow"], trend[-1]["outflow"]
        if first > 0:
            direction = "increasing" if last > first else "decreasing" if last < first else "flat"
    return {"months": trend[-6:], "overall_direction": direction}


def compare_periods(rows: list[dict], period_a: str, period_b: str) -> dict:
    """Compare two YYYY-MM months. Returns exact differences only."""
    months = {m["month"]: m for m in get_monthly_cashflow(rows, limit=MAX_TOP_N)["months"]}
    a, b = months.get(period_a), months.get(period_b)
    if not a or not b:
        return {"error": "period_not_found",
                "available_months": sorted(months)[:12],
                "requested": [period_a, period_b]}

    def delta(x: float, y: float) -> dict:
        diff = round(x - y, 2)
        pct = round((x - y) / y * 100, 1) if y > 0 else None
        return {"difference": diff, "pct_change": pct}

    return {
        "period_a": a, "period_b": b,
        "outflow_change": delta(a["total_outflow"], b["total_outflow"]),
        "income_change": delta(a["total_income"], b["total_income"]),
    }


def search_transactions(rows: list[dict], query: str = "", date_from: str | None = None,
                        date_to: str | None = None, category: str | None = None,
                        direction: str | None = None, min_amount: float | None = None,
                        max_amount: float | None = None,
                        limit: int = DEFAULT_TOP_N) -> dict:
    start, end = _parse_date(date_from), _parse_date(date_to)
    needle = (query or "").strip().upper()
    matched = []
    for r in rows:
        if needle and needle not in r["description"].upper() and needle not in r["category"].upper():
            continue
        if direction and r["direction"] != direction:
            continue
        if category and r["category"].upper() != category.upper():
            continue
        if start and (not r["date"] or r["date"] < start):
            continue
        if end and (not r["date"] or r["date"] > end):
            continue
        if min_amount is not None and r["amount"] < min_amount:
            continue
        if max_amount is not None and r["amount"] > max_amount:
            continue
        matched.append(r)

    matched.sort(key=lambda r: r["amount"], reverse=True)
    total = sum(r["amount"] for r in matched)
    return {
        "match_count": len(matched),
        "total_amount": round(total, 2),
        "transactions": [
            {"date": r["date"].isoformat() if r["date"] else None,
             "description": r["description"][:70],
             "amount": round(r["amount"], 2),
             "direction": r["direction"],
             "category": r["category"]}
            for r in matched[:_clamp_limit(limit)]
        ],
    }


def get_category_transactions(rows: list[dict], category: str,
                              limit: int = DEFAULT_TOP_N) -> dict:
    result = search_transactions(rows, category=category, limit=limit)
    return {"category": category, "total": result["total_amount"],
            "transaction_count": result["match_count"],
            "transactions": result["transactions"]}


def get_statements(rows: list[dict], statements: list[dict] | None = None,
                   limit: int = DEFAULT_TOP_N) -> dict:
    """List the user's uploaded statements with their periods and totals.

    Reads the statement records supplied by the caller, so the assistant can
    answer questions about the account's history rather than only its
    transactions.
    """
    statements = statements or []
    return {
        "statement_count": len(statements),
        "statements": [
            {"file_name": s.get("file_name"),
             "period_start": s.get("period_start"),
             "period_end": s.get("period_end"),
             "deposits": s.get("deposits"),
             "withdrawals": s.get("withdrawals"),
             "closing_balance": s.get("closing_balance")}
            for s in statements[:_clamp_limit(limit)]
        ],
    }


def get_kpi_metrics(rows: list[dict], kpis: list[dict] | None = None,
                    limit: int = DEFAULT_TOP_N) -> dict:
    """Return stored KPI metrics with their thresholds and status.

    These were computed by the deterministic KPI engine at upload time; this
    tool only reads them back.
    """
    kpis = kpis or []
    return {
        "metric_count": len(kpis),
        "metrics": [
            {"name": k.get("name"), "value": k.get("value"),
             "unit": k.get("unit"), "status": k.get("status"),
             "type": k.get("type")}
            for k in kpis[:_clamp_limit(limit, 10)]
        ],
    }


def list_categories(rows: list[dict]) -> dict:
    """Every category present, so the assistant can suggest valid filters."""
    debit = sorted({r["category"] for r in _debits(rows)})
    credit = sorted({r["category"] for r in _credits(rows)})
    return {"expense_categories": debit[:25], "income_categories": credit[:25]}


def get_balance_trend(rows: list[dict]) -> dict:
    dated = sorted((r for r in rows if r["date"] and r["balance"] is not None),
                   key=lambda r: r["date"])
    if not dated:
        return {"available": False}
    lo = min(dated, key=lambda r: r["balance"])
    hi = max(dated, key=lambda r: r["balance"])
    return {
        "available": True,
        "beginning": round(dated[0]["balance"], 2),
        "beginning_date": dated[0]["date"].isoformat(),
        "ending": round(dated[-1]["balance"], 2),
        "ending_date": dated[-1]["date"].isoformat(),
        "min": round(lo["balance"], 2), "min_date": lo["date"].isoformat(),
        "max": round(hi["balance"], 2), "max_date": hi["date"].isoformat(),
    }


def get_unusual_large_transactions(rows: list[dict], limit: int = DEFAULT_TOP_N) -> dict:
    """Flag outlying debits by a fixed statistical rule.

    Threshold is the average debit plus two standard deviations, computed here.
    The model is never asked to judge what counts as unusual.

    Being above the threshold means only that an amount sits outside the normal
    spread of this account's debits. It is not evidence of error or wrongdoing,
    and nothing in this output should be presented as such.

    The metric is named `average_debit` to match get_transaction_summary
    exactly. They are the same quantity, and giving it two names invited the
    model to "compare" it with itself.
    """
    debits = _debits(rows)
    amounts = [r["amount"] for r in debits]
    if len(amounts) < 4:
        return {"available": False, "reason": "not_enough_transactions",
                "debit_count": len(amounts)}

    mean = statistics.fmean(amounts)
    sd = statistics.pstdev(amounts)
    threshold = mean + 2 * sd
    flagged = [r for r in debits if r["amount"] > threshold]
    shown = sorted(flagged, key=lambda r: r["amount"], reverse=True)[:_clamp_limit(limit)]
    return {
        "available": True,
        "method": "average_debit_plus_2_standard_deviations",
        "average_debit": round(mean, 2),
        "standard_deviation": round(sd, 2),
        "threshold": round(threshold, 2),
        "count": len(flagged),
        "shown": len(shown),
        "transactions": [
            {"date": r["date"].isoformat() if r["date"] else None,
             "description": r["description"][:70],
             "amount": round(r["amount"], 2),
             "direction": r["direction"],
             "category": r["category"],
             "reason": "amount_above_outlier_threshold"}
            for r in shown
        ],
    }


# ── Recurring payments ───────────────────────────────────────────────────────
# Cadence bands keyed by mean interval in days. The previous implementation
# treated everything from 20-45 days as monthly, which is how a 44-day gap on an
# "ANNUAL SAFETY INSPECTION" came to be displayed as a monthly figure.
_CADENCE_BANDS = (
    (5, 9, "weekly", 52 / 12),
    (10, 18, "biweekly", 26 / 12),
    (26, 35, "monthly", 1.0),
    (36, 55, "every 6-8 weeks", 365.25 / 45 / 12),
    (75, 115, "quarterly", 1 / 3),
    (150, 210, "semiannual", 1 / 6),
    (300, 420, "annual", 1 / 12),
)

# Words in a description that assert a cadence. If the description says annual,
# two samples 44 days apart are not evidence enough to overrule it.
_CADENCE_WORDS = {
    "ANNUAL": "annual", "YEARLY": "annual", "PER YEAR": "annual",
    "QUARTERLY": "quarterly", "SEMIANNUAL": "semiannual", "BIANNUAL": "semiannual",
    "MONTHLY": "monthly", "PER MONTH": "monthly",
    "WEEKLY": "weekly", "FORTNIGHTLY": "biweekly", "BIWEEKLY": "biweekly",
}


def _classify_cadence(mean_interval: float, occurrences: int, description: str,
                      interval_spread: float) -> tuple[str, str, float | None]:
    """Return (label, confidence, periods_per_month).

    periods_per_month is None when the cadence is too uncertain to annualise —
    callers must not produce a monthly figure in that case.
    """
    stated = None
    upper = description.upper()
    for word, label in _CADENCE_WORDS.items():
        if word in upper:
            stated = label
            break

    band = None
    for lo, hi, label, per_month in _CADENCE_BANDS:
        if lo <= mean_interval <= hi:
            band = (label, per_month)
            break

    # A stated cadence that contradicts the observed spacing wins, because the
    # sample is small and the wording is explicit.
    if stated and (band is None or band[0] != stated):
        return stated, "stated_in_description", None

    if band is None:
        return "irregular recurring", "low", None

    label, per_month = band
    # Confidence needs both repetition and consistent spacing.
    consistent = interval_spread <= 0.25
    if occurrences >= 4 and consistent:
        confidence = "high"
    elif occurrences >= 3 and consistent:
        confidence = "medium"
    else:
        confidence = "low"

    # Only annualise when the cadence is actually established.
    return label, confidence, (per_month if confidence in ("high", "medium") else None)


def get_recurring_transactions(rows: list[dict], limit: int = DEFAULT_TOP_N) -> dict:
    """Detect repeating debits and classify their cadence honestly.

    Reports a monthly-equivalent figure only when the cadence is established;
    weak evidence is surfaced as a possible recurring payment instead.
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in _debits(rows):
        if r["date"]:
            groups[_normalize_payee(r["description"])].append(r)

    results = []
    for key, entries in groups.items():
        if len(entries) < 2:
            continue
        entries.sort(key=lambda r: r["date"])
        dates = [r["date"] for r in entries]
        amounts = [r["amount"] for r in entries]
        intervals = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
        intervals = [i for i in intervals if i > 0]
        if not intervals:
            continue

        mean_interval = statistics.fmean(intervals)
        spread = (statistics.pstdev(intervals) / mean_interval) if mean_interval else 1.0
        amount_spread = (max(amounts) / min(amounts)) if min(amounts) > 0 else 999
        if amount_spread > 1.35:
            continue  # amounts vary too much to call it the same recurring charge

        label, confidence, per_month = _classify_cadence(
            mean_interval, len(entries), entries[0]["description"], spread)

        avg_amount = round(statistics.fmean(amounts), 2)
        results.append({
            "description": entries[0]["description"][:60],
            "avg_amount": avg_amount,
            "occurrences": len(entries),
            "avg_interval_days": round(mean_interval),
            "cadence": label,
            "confidence": confidence,
            # None when the cadence is not established — do not invent a rate.
            "monthly_equivalent": (round(avg_amount * per_month, 2)
                                   if per_month is not None else None),
            "label": (f"{label}" if confidence != "low" else f"possible recurring ({label})"),
        })

    results.sort(key=lambda d: d["avg_amount"], reverse=True)
    established = [r for r in results if r["monthly_equivalent"] is not None]
    return {
        "recurring": results[:_clamp_limit(limit)],
        "count": len(results),
        # Only sums cadences we are confident about.
        "estimated_monthly_total": round(
            sum(r["monthly_equivalent"] for r in established), 2) if established else 0.0,
        "excluded_uncertain": len(results) - len(established),
    }


# ── Registry ─────────────────────────────────────────────────────────────────
# Allowlist. Only these names may execute, and only these argument names are
# accepted, with types coerced and validated before the call.

_STR = "str"
_INT = "int"
_NUM = "float"

TOOL_REGISTRY: dict[str, dict[str, Any]] = {
    "get_transaction_summary": {
        "fn": get_transaction_summary,
        "desc": "Overall totals: date range, income, outflow, net, counts, averages.",
        "args": {},
    },
    "get_spending_by_category": {
        "fn": get_spending_by_category,
        "desc": "Rank spending categories by total, with percentage of outflow.",
        "args": {"limit": _INT},
    },
    "get_income_by_category": {
        "fn": get_income_by_category,
        "desc": "Rank income categories by total.",
        "args": {"limit": _INT},
    },
    "get_top_merchants_or_descriptions": {
        "fn": get_top_merchants_or_descriptions,
        "desc": "Rank who was paid (or who paid) by grouped description.",
        "args": {"limit": _INT, "direction": _STR},
    },
    "get_income_sources": {
        "fn": get_income_sources,
        "desc": "Rank where income came from.",
        "args": {"limit": _INT},
    },
    "get_largest_transactions": {
        "fn": get_largest_transactions,
        "desc": "Biggest individual transactions. direction is debit or credit.",
        "args": {"direction": _STR, "limit": _INT},
    },
    "get_monthly_cashflow": {
        "fn": get_monthly_cashflow,
        "desc": "Income, outflow and net per month.",
        "args": {"limit": _INT},
    },
    "get_highest_spending_month": {
        "fn": get_highest_spending_month,
        "desc": "The month with the most spending, and how it compares to the previous one.",
        "args": {},
    },
    "get_spending_trend": {
        "fn": get_spending_trend,
        "desc": "Month-on-month spending changes and overall direction.",
        "args": {},
    },
    "compare_periods": {
        "fn": compare_periods,
        "desc": "Compare two months, given as YYYY-MM, e.g. 2025-12 and 2026-01.",
        "args": {"period_a": _STR, "period_b": _STR},
    },
    "search_transactions": {
        "fn": search_transactions,
        "desc": "Find transactions matching text, dates, category, direction or amount.",
        "args": {"query": _STR, "date_from": _STR, "date_to": _STR, "category": _STR,
                 "direction": _STR, "min_amount": _NUM, "max_amount": _NUM, "limit": _INT},
    },
    "get_category_transactions": {
        "fn": get_category_transactions,
        "desc": "All transactions in one category, plus the category total.",
        "args": {"category": _STR, "limit": _INT},
    },
    "get_balance_trend": {
        "fn": get_balance_trend,
        "desc": "Running balance start, end, minimum and maximum with dates.",
        "args": {},
    },
    "get_unusual_large_transactions": {
        "fn": get_unusual_large_transactions,
        "desc": "Debits above mean plus two standard deviations.",
        "args": {"limit": _INT},
    },
    "get_recurring_transactions": {
        "fn": get_recurring_transactions,
        "desc": "Repeating payments with cadence and confidence.",
        "args": {"limit": _INT},
    },
    "get_statements": {
        "fn": get_statements,
        "desc": "List uploaded statements with their periods and totals.",
        "args": {"limit": _INT},
        "context": ("statements",),
    },
    "get_kpi_metrics": {
        "fn": get_kpi_metrics,
        "desc": "Stored KPI metrics such as profit margin and liquidity, with status.",
        "args": {"limit": _INT},
        "context": ("kpis",),
    },
    "list_categories": {
        "fn": list_categories,
        "desc": "All expense and income categories present in the data.",
        "args": {},
    },
}

# Context keys a tool may receive beyond the transaction rows. Supplied by the
# caller (the view), never by the model.
_CONTEXT_KEYS = ("statements", "kpis", "opening_balance", "closing_balance")

TOOL_REGISTRY["get_transaction_summary"]["context"] = ("opening_balance", "closing_balance")

_COERCERS: dict[str, Callable[[Any], Any]] = {
    _STR: lambda v: str(v)[:100],
    _INT: lambda v: int(v),
    _NUM: lambda v: float(v),
}


class ToolValidationError(ValueError):
    """Raised when a requested tool or argument fails validation."""


def validate_call(name: Any, arguments: Any) -> tuple[str, dict[str, Any]]:
    """Validate a model-proposed call against the allowlist.

    Unknown tool names and unknown arguments are rejected rather than ignored,
    so a confused model cannot reach anything it was not offered.
    """
    if not isinstance(name, str) or name not in TOOL_REGISTRY:
        raise ToolValidationError(f"unknown tool: {name!r}")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise ToolValidationError(f"arguments for {name} must be an object")

    schema = TOOL_REGISTRY[name]["args"]
    clean: dict[str, Any] = {}
    for key, value in arguments.items():
        if key not in schema:
            raise ToolValidationError(f"unknown argument {key!r} for {name}")
        if value is None:
            continue
        try:
            clean[key] = _COERCERS[schema[key]](value)
        except (TypeError, ValueError) as exc:
            raise ToolValidationError(
                f"argument {key!r} for {name} has invalid type") from exc
    return name, clean


def run_tool(name: str, rows: list[dict], arguments: dict[str, Any] | None = None,
             context: dict[str, Any] | None = None) -> Any:
    """Execute a validated tool. Never call with unvalidated input.

    `context` carries caller-supplied data (statements, stored KPIs, balances).
    Only the keys a tool declares are passed, and only from the caller — the
    model cannot reach into it.
    """
    name, clean = validate_call(name, arguments or {})
    meta = TOOL_REGISTRY[name]
    extra = {
        key: (context or {}).get(key)
        for key in meta.get("context", ())
        if key in _CONTEXT_KEYS
    }
    return meta["fn"](rows, **clean, **extra)


def tool_catalogue() -> str:
    """Compact tool list for the router prompt. Kept terse for a 1B model."""
    return "\n".join(f"- {name}: {meta['desc']}" for name, meta in TOOL_REGISTRY.items())
