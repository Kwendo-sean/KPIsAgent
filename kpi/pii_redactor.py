"""
pii_redactor.py
───────────────
Redacts Personally Identifiable Information (PII) from bank statement text
before it is sent to any external AI API (Claude / Anthropic).

What is redacted
────────────────
  • Bank account numbers  (8–16 consecutive digits, labelled explicitly)
  • Card / PAN numbers    (13–19 digits with clear card grouping)
  • IBAN numbers          (standard 2-letter country + 2 check digits + up to 30 chars)
  • Phone numbers         (Kenyan +254 / 07xx / 01xx and international E.164)
  • Full names on "Account Holder:" / "Name:" header lines

What is kept
────────────
  • Transaction amounts and balances (e.g. 5,000.00 — NOT treated as IDs)
  • KRA PINs (AxxxxxxxxB) — needed for VAT/tax KPI analysis
  • M-PESA receipt codes (UDEPE0P493) — alphanumeric, needed for extraction
  • Transaction dates, descriptions, and currency codes

Design note on the national_id pattern
───────────────────────────────────────
The original pattern (?<!\d)\d{8}(?!\d) was too broad: it matched transaction
amounts, reference codes, and M-PESA paybill numbers, mangling the text that
Claude needs to extract transactions from.  We now only redact 8-digit numbers
that are preceded by a PII-label keyword (e.g. "ID:" or "National ID").
"""

import re

_PATTERNS: list[tuple[str, re.Pattern, str]] = [

    # Card / PAN: 13–19 digits grouped with spaces or hyphens (card format only)
    (
        "card_number",
        re.compile(
            r"\b\d{4}[\s\-]\d{4}[\s\-]\d{4}[\s\-]\d{1,7}\b",
        ),
        "[CARD-REDACTED]",
    ),

    # IBAN: 2-letter country code + 2 check digits + 10–30 alphanumeric chars
    # Anchored tightly to avoid matching M-PESA receipt codes or KRA PINs.
    (
        "iban",
        re.compile(
            r"\b(?:KE|UG|TZ|GB|DE|FR|NL|ZA|NG|GH|RW|ET|MZ|SD|SN|CM|CI|BF|ML|SL|GM|GN|LR|BJ|TG|NE|CF|TD|MR|MG|MW|ZM|ZW|BI|DJ|ER|SO|SS|KM|CV|ST|SZ|LS|BW|NA|AO|CD|CG|GA|GQ|RW|UG|KE|ET|SD|ER|DJ|SO|KM|SC|MU|MZ|ZW|ZM|MW|TZ|BI|RW|UG|KE)\d{2}[A-Z0-9]{10,30}\b",
            re.IGNORECASE,
        ),
        "[IBAN-REDACTED]",
    ),

    # Kenyan phone numbers: +254..., 07..., 01..., 2547...
    (
        "phone_ke",
        re.compile(
            r"(?:\+?254|0)[17]\d{8}\b",
        ),
        "[PHONE-REDACTED]",
    ),

    # Generic international phone: +XX or +XXX followed by 7–12 digits
    (
        "phone_intl",
        re.compile(
            r"\+\d{1,3}[\s\-]?\(?\d{2,4}\)?[\s\-]?\d{3,4}[\s\-]?\d{3,4}",
        ),
        "[PHONE-REDACTED]",
    ),

    # National ID — only when explicitly labelled (avoids matching amounts/codes)
    (
        "national_id_labelled",
        re.compile(
            r"(?:national\s*id|id\s*(?:no|number|#|num)|id\s*card)[.:\s]+\d{6,10}",
            re.IGNORECASE,
        ),
        "[ID-REDACTED]",
    ),

    # Bank account numbers — only when explicitly labelled
    (
        "account_labelled",
        re.compile(
            r"(?:account\s*(?:no|number|#|num)[.:\s]+)\d{6,18}",
            re.IGNORECASE,
        ),
        "[ACCOUNT-REDACTED]",
    ),

    # Account holder name lines
    (
        "name_label",
        re.compile(
            r"(?:account\s*(?:name|holder)|customer\s*name|name)[:\s]+[A-Z][A-Za-z ,.']{2,60}",
            re.IGNORECASE,
        ),
        "[NAME-REDACTED]",
    ),

    # Address lines
    (
        "address",
        re.compile(
            r"(?:address|p\.?\s*o\.?\s*box)[:\s]+.{5,80}",
            re.IGNORECASE,
        ),
        "[ADDRESS-REDACTED]",
    ),
]


def redact(text: str) -> str:
    """Return a copy of text with all matched PII replaced by safe placeholders."""
    if not text:
        return text
    redacted = text
    for _name, pattern, replacement in _PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def redact_summary(original: str, redacted: str) -> dict:
    """Return a dict describing how many substitutions were made per category."""
    summary = {}
    for name, pattern, _ in _PATTERNS:
        original_matches = len(pattern.findall(original))
        if original_matches:
            summary[name] = original_matches
    return summary
