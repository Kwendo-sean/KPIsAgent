"""Deterministic extraction and credit-control aggregates for documents.

Same rule as the rest of the app: every figure, date and identifier here is
produced by Python. A language model may later describe what was found, but it
never supplies a value.

Kept dependency-free — it reuses the date and decimal parsing already proven in
pdf_extractor rather than introducing a parsing stack.
"""
from __future__ import annotations

import datetime as _dt
import re
from collections import defaultdict
from decimal import Decimal
from typing import Any, Iterable

from .pdf_extractor import BankStatementPDFExtractor as _Extractor

# Aging buckets used across credit control, in days past due.
AGING_BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("Current", -10**6, 0),
    ("1-30 days", 1, 30),
    ("31-60 days", 31, 60),
    ("61-90 days", 61, 90),
    ("90+ days", 91, None),
)

_CURRENCY_WORDS = ("KES", "KSH", "USD", "EUR", "GBP", "UGX", "TZS", "NGN", "ZAR")

# Labelled-total patterns, most specific first — "amount due" beats "subtotal".
_AMOUNT_PATTERNS = (
    r"(?:total\s+amount\s+due|amount\s+due|balance\s+due|total\s+due)\s*[:\-]?\s*"
    r"(?:[A-Z]{3}\s*)?([\d,]+\.\d{2}|[\d,]{2,})",
    r"(?:grand\s+total|total\s+payable|net\s+payable)\s*[:\-]?\s*"
    r"(?:[A-Z]{3}\s*)?([\d,]+\.\d{2}|[\d,]{2,})",
    r"(?:^|\n)\s*total\s*[:\-]?\s*(?:[A-Z]{3}\s*)?([\d,]+\.\d{2}|[\d,]{2,})",
)

_REFERENCE_PATTERNS = (
    r"(?:invoice\s*(?:no|number|#)|inv\s*(?:no|#))\s*[:\-#]?\s*([A-Za-z0-9\-/]{3,30})",
    r"(?:reference|ref|receipt\s*(?:no|#)|document\s*(?:no|#))\s*[:\-#]?\s*([A-Za-z0-9\-/]{3,30})",
    r"(?:account\s*(?:no|number))\s*[:\-#]?\s*([A-Za-z0-9\-/]{4,30})",
)

_COUNTERPARTY_PATTERNS = (
    r"(?:bill\s*to|invoice\s*to|customer|client|supplier|vendor|payee|paid\s*to|"
    r"sold\s*to|received\s*from|sent\s*to)\s*[:\-]?\s*([A-Za-z0-9][^\n,;|]{2,60})",
    r"(?:company|organisation|organization|business)\s*name\s*[:\-]?\s*([A-Za-z0-9][^\n,;|]{2,60})",
)

_DUE_DATE_PATTERNS = (
    r"(?:due\s*date|payment\s*due|due\s*on|pay\s*by)\s*[:\-]?\s*([0-9A-Za-z/\-\s,]{6,25})",
)

_DOC_DATE_PATTERNS = (
    r"(?:invoice\s*date|document\s*date|issue\s*date|dated|date\s*of\s*issue)"
    r"\s*[:\-]?\s*([0-9A-Za-z/\-\s,]{6,25})",
    r"(?:^|\n)\s*date\s*[:\-]\s*([0-9A-Za-z/\-\s,]{6,25})",
)

# Terms like "Net 30" imply a due date relative to the document date.
_NET_TERMS = re.compile(r"\bnet\s*(\d{1,3})\b", re.IGNORECASE)

_TYPE_HINTS = (
    ("INVOICE", ("invoice", "tax invoice", "proforma", "amount due", "bill to")),
    ("RECEIPT", ("receipt", "paid in full", "payment received", "thank you for your payment")),
    ("REMITTANCE", ("remittance", "payment advice", "funds transfer advice")),
    ("CONTRACT", ("agreement", "contract", "terms and conditions", "hereby agree")),
    ("KYC", ("passport", "national id", "identity", "kyc", "certificate of incorporation")),
    ("STATEMENT", ("bank statement", "mpesa statement", "account statement", "opening balance")),
    ("CORRESPONDENCE", ("dear sir", "dear madam", "yours faithfully", "letter of")),
)


def _clean_amount(raw: str) -> Decimal | None:
    try:
        value = Decimal(str(raw).replace(",", "").strip())
    except Exception:
        return None
    return value if value > 0 else None


def _parse_date(raw: str) -> _dt.date | None:
    """Reuse the statement extractor's date handling rather than re-inventing it."""
    if not raw:
        return None
    text = re.sub(r"\s+", " ", str(raw)).strip(" .,:;-")
    parsed = _Extractor._parse_date(text)
    if parsed:
        return parsed
    # Fall back to the first date-looking run inside a longer phrase.
    match = re.search(r"\d{1,4}[/\-\s][A-Za-z0-9]{1,9}[/\-\s]\d{2,4}", text)
    if match:
        return _Extractor._parse_date(match.group())
    return None


def _first_match(patterns: Iterable[str], text: str) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            value = match.group(1).strip(" .,:;-|")
            if value:
                return value
    return None


def detect_document_type(text: str, file_name: str = "") -> str:
    """Classify by keyword evidence. Returns OTHER when nothing matches.

    Keyword counting, not inference — an unrecognised document stays OTHER so a
    person classifies it, rather than being given a confident wrong label.
    """
    haystack = f"{file_name}\n{text[:4000]}".lower()
    best_type, best_score = "OTHER", 0
    for doc_type, hints in _TYPE_HINTS:
        score = sum(1 for hint in hints if hint in haystack)
        if score > best_score:
            best_type, best_score = doc_type, score
    return best_type


def extract_document_fields(text: str, file_name: str = "") -> dict[str, Any]:
    """Pull the fields a credit-control clerk needs out of raw document text.

    Absent fields come back as None. Nothing is guessed: if the document does
    not state an amount, none is returned.
    """
    text = text or ""
    window = text[:20000]  # long contracts: the useful header is at the top

    amount = None
    for pattern in _AMOUNT_PATTERNS:
        match = re.search(pattern, window, re.IGNORECASE | re.MULTILINE)
        if match:
            amount = _clean_amount(match.group(1))
            if amount is not None:
                break

    currency = ""
    currency_match = re.search(r"\b(" + "|".join(_CURRENCY_WORDS) + r")\b", window, re.IGNORECASE)
    if currency_match:
        currency = currency_match.group(1).upper()
        if currency == "KSH":
            currency = "KES"

    doc_date = _parse_date(_first_match(_DOC_DATE_PATTERNS, window) or "")
    due_date = _parse_date(_first_match(_DUE_DATE_PATTERNS, window) or "")

    # "Net 30" only yields a due date when there is a document date to add to.
    if due_date is None and doc_date is not None:
        terms = _NET_TERMS.search(window)
        if terms:
            due_date = doc_date + _dt.timedelta(days=int(terms.group(1)))

    counterparty = _first_match(_COUNTERPARTY_PATTERNS, window) or ""
    counterparty = re.sub(r"\s{2,}", " ", counterparty)[:200]

    return {
        "doc_type": detect_document_type(text, file_name),
        "amount": amount,
        "currency": currency,
        "doc_date": doc_date,
        "due_date": due_date,
        "counterparty": counterparty,
        "reference": (_first_match(_REFERENCE_PATTERNS, window) or "")[:100],
    }


# ── Credit-control aggregates ────────────────────────────────────────────────

def _bucket_for(days_overdue: int | None) -> str:
    if days_overdue is None:
        return "Current"
    for label, low, high in AGING_BUCKETS:
        if days_overdue >= low and (high is None or days_overdue <= high):
            return label
    return "90+ days"


def build_aging_report(documents: Iterable[Any], today: _dt.date | None = None) -> dict[str, Any]:
    """Age outstanding documents into buckets. Pure arithmetic over due dates.

    Only documents that are both outstanding and carry an amount are counted;
    a document with no stated amount cannot contribute to an exposure figure.
    """
    today = today or _dt.date.today()
    buckets: dict[str, dict[str, Any]] = {
        label: {"bucket": label, "total": Decimal("0"), "count": 0}
        for label, _, _ in AGING_BUCKETS
    }
    total = Decimal("0")
    overdue_total = Decimal("0")
    overdue_docs: list[Any] = []

    for doc in documents:
        if not doc.is_outstanding or doc.amount is None:
            continue
        days = doc.days_overdue(today)
        label = _bucket_for(days)
        buckets[label]["total"] += doc.amount
        buckets[label]["count"] += 1
        total += doc.amount
        if days and days > 0:
            overdue_total += doc.amount
            overdue_docs.append(doc)

    overdue_docs.sort(key=lambda d: (d.days_overdue(today) or 0), reverse=True)
    return {
        "as_of": today,
        "buckets": [
            {**b, "total": float(b["total"]),
             "pct": round(float(b["total"]) / float(total) * 100, 1) if total else 0.0}
            for b in buckets.values()
        ],
        "total_outstanding": float(total),
        "overdue_total": float(overdue_total),
        "overdue_count": len(overdue_docs),
        "overdue_documents": overdue_docs[:10],
    }


def build_counterparty_exposure(documents: Iterable[Any], limit: int = 10,
                                today: _dt.date | None = None) -> list[dict[str, Any]]:
    """Rank outstanding exposure by counterparty."""
    today = today or _dt.date.today()
    groups: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"total": Decimal("0"), "count": 0, "overdue": Decimal("0"),
                 "max_days_overdue": 0})

    for doc in documents:
        if not doc.is_outstanding or doc.amount is None:
            continue
        name = (doc.counterparty or "Unattributed").strip() or "Unattributed"
        entry = groups[name]
        entry["total"] += doc.amount
        entry["count"] += 1
        days = doc.days_overdue(today) or 0
        if days > 0:
            entry["overdue"] += doc.amount
            entry["max_days_overdue"] = max(entry["max_days_overdue"], days)

    ranked = sorted(
        ({"counterparty": name,
          "total": float(v["total"]),
          "overdue": float(v["overdue"]),
          "count": v["count"],
          "max_days_overdue": v["max_days_overdue"]}
         for name, v in groups.items()),
        key=lambda d: d["total"], reverse=True,
    )
    return ranked[:limit]


def summarize_queue(documents: Iterable[Any]) -> dict[str, int]:
    """Counts by status and by department, for the section header."""
    by_status: dict[str, int] = defaultdict(int)
    by_department: dict[str, int] = defaultdict(int)
    missing_fields = 0
    for doc in documents:
        by_status[doc.status] += 1
        by_department[doc.department] += 1
        if doc.amount is None or not doc.counterparty:
            missing_fields += 1
    return {
        "total": sum(by_status.values()),
        "by_status": dict(by_status),
        "by_department": dict(by_department),
        "needs_attention": missing_fields,
    }
