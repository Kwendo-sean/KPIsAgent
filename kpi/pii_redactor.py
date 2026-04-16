"""
pii_redactor.py
───────────────
Redacts Personally Identifiable Information (PII) from bank statement text
before it is sent to any external AI API (Claude / Anthropic).

What is redacted
────────────────
  • Bank account numbers  (8–16 consecutive digits, or common delimited formats)
  • Card / PAN numbers    (13–19 digits, Luhn-ish patterns)
  • IBAN numbers
  • Phone numbers         (Kenyan +254 / 07xx / 01xx and international E.164)
  • National ID numbers   (Kenyan 8-digit)
  • Full names on "Account Holder:" / "Name:" header lines

What is kept
────────────
  • KRA PINs (AxxxxxxxxB) — needed for VAT/tax KPI analysis
  • Transaction dates, descriptions, and amounts
  • Currency codes and balances

The redaction is one-way: the original text is never stored after processing
and the redacted copy is what gets forwarded to Claude.
"""

import re

# ──────────────────────────────────────────────────────────────────────────────
# Redaction patterns
# ──────────────────────────────────────────────────────────────────────────────

_PATTERNS: list[tuple[str, re.Pattern, str]] = [

    # Card / PAN  (13–19 digits, optionally grouped with spaces/hyphens)
    (
        "card_number",
        re.compile(
            r"\b(?:\d[ -]?){13,19}\b",
            re.IGNORECASE,
        ),
        "[CARD-REDACTED]",
    ),

    # IBAN  (2 letters + 2 digits + up to 30 alphanumeric chars)
    (
        "iban",
        re.compile(
            r"\b[A-Z]{2}\d{2}[A-Z0-9]{1,30}\b",
            re.IGNORECASE,
        ),
        "[IBAN-REDACTED]",
    ),

    # Kenyan phone numbers  (+2547xx, 07xx, 01xx, 2547xx)
    (
        "phone_ke",
        re.compile(
            r"(?:\+?254|0)[17]\d{8}\b",
        ),
        "[PHONE-REDACTED]",
    ),

    # Generic international phone  (+XX followed by 7–12 digits)
    (
        "phone_intl",
        re.compile(
            r"\+\d{1,3}[\s\-]?\(?\d{2,4}\)?[\s\-]?\d{3,4}[\s\-]?\d{3,4}",
        ),
        "[PHONE-REDACTED]",
    ),

    # Kenyan National ID  (standalone 8-digit number)
    (
        "national_id",
        re.compile(
            r"(?<!\d)\d{8}(?!\d)",
        ),
        "[ID-REDACTED]",
    ),

    # Bank account numbers:
    # (a) explicit label  "Account No: 1234567890"
    (
        "account_labelled",
        re.compile(
            r"(?:account\s*(?:no|number|#|num)[.:\s]+)\d{6,18}",
            re.IGNORECASE,
        ),
        "[ACCOUNT-REDACTED]",
    ),

    # (b) standalone 10–16 digit numbers not already matched above
    #     (KRA PINs are excluded by their letter-digit-letter structure)
    (
        "account_standalone",
        re.compile(
            r"(?<!\d)(?<![A-Z])\d{10,16}(?!\d)(?![A-Z])",
        ),
        "[ACCOUNT-REDACTED]",
    ),

    # Account holder name lines  ("Account Name: John Doe" / "Name: ...")
    (
        "name_label",
        re.compile(
            r"(?:account\s*(?:name|holder)|customer\s*name|name)[:\s]+[A-Z][A-Za-z ,.']{2,60}",
            re.IGNORECASE,
        ),
        "[NAME-REDACTED]",
    ),

    # Statement address lines  ("Address: ..." / "P.O. Box ...")
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
    """
    Return a copy of *text* with all matched PII replaced by safe placeholders.

    Patterns are applied in order.  Because card numbers (long digit strings)
    are matched first, subsequent shorter-digit patterns won't double-match.
    """
    if not text:
        return text

    redacted = text
    for _name, pattern, replacement in _PATTERNS:
        redacted = pattern.sub(replacement, redacted)

    return redacted


def redact_summary(original: str, redacted: str) -> dict:
    """
    Return a dict describing how many substitutions were made per category.
    Useful for logging / debugging without exposing the actual values.
    """
    summary = {}
    for name, pattern, _ in _PATTERNS:
        original_matches = len(pattern.findall(original))
        if original_matches:
            summary[name] = original_matches
    return summary
