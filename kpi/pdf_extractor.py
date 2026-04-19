import base64
import csv
import io
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

import PyPDF2

try:
    import fitz as _fitz  # PyMuPDF
except ImportError:
    _fitz = None


class PDFPasswordRequired(Exception):
    """Raised when a PDF is encrypted and no (or wrong) password was supplied."""


class PDFWrongPassword(Exception):
    """Raised when the supplied password is incorrect."""


class BankStatementPDFExtractor:
    """Extract text and structured data from bank statement files."""

    # Minimum character count to consider PyPDF2 extraction successful.
    # Below this threshold we assume the PDF is scanned and attempt OCR.
    _OCR_THRESHOLD = 100

    DATE_FORMATS = (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%d/%m/%y",
        "%m/%d/%y",
        "%d-%m-%Y",
        "%d-%m-%y",
        "%d-%b-%Y",
        "%d-%B-%Y",
        "%b %d, %Y",
        "%B %d, %Y",
        # M-PESA specific formats: "Oct. 25, 2025" or "March 26, 2025"
        "%b. %d, %Y",
        "%B. %d, %Y",
        # Statement header format: "16 Oct 2025"
        "%d %b %Y",
        "%d %B %Y",
    )

    @staticmethod
    def extract_text_from_pdf(pdf_file, password: str = ""):
        """
        Extract text from a PDF file, with optional password for encrypted files.

        Raises:
            PDFPasswordRequired  — PDF is encrypted and no password was given.
            PDFWrongPassword     — PDF is encrypted and the supplied password is wrong.
            ValueError           — Any other unreadable PDF.

        Strategy:
        1. Try PyMuPDF (fitz) — better text extraction, explicit encryption handling.
        2. Fall back to PyPDF2 if PyMuPDF is unavailable.
        """
        text = ""

        if _fitz is not None:
            try:
                pdf_file.seek(0)
                doc = _fitz.open(stream=pdf_file.read(), filetype="pdf")

                if doc.is_encrypted:
                    if not password:
                        doc.close()
                        raise PDFPasswordRequired(
                            "This PDF is password-protected. Please enter the password to continue."
                        )
                    result = doc.authenticate(password)
                    # authenticate() returns 0 on failure, >0 on success
                    if result == 0:
                        doc.close()
                        raise PDFWrongPassword(
                            "Incorrect password. Please check and try again."
                        )

                for page in doc:
                    text += page.get_text() or ""
                doc.close()
            except (PDFPasswordRequired, PDFWrongPassword):
                raise
            except Exception:
                text = ""

        # Fallback to PyPDF2 if PyMuPDF is unavailable or yielded nothing
        if not text.strip():
            try:
                pdf_file.seek(0)
                reader = PyPDF2.PdfReader(pdf_file)
                if reader.is_encrypted:
                    if not password:
                        raise PDFPasswordRequired(
                            "This PDF is password-protected. Please enter the password to continue."
                        )
                    result = reader.decrypt(password)
                    if result == 0:
                        raise PDFWrongPassword(
                            "Incorrect password. Please check and try again."
                        )
                for page in reader.pages:
                    text += page.extract_text() or ""
            except (PDFPasswordRequired, PDFWrongPassword):
                raise
            except Exception as exc:
                raise ValueError(f"Could not read PDF: {exc}") from exc

        return text

    @classmethod
    def render_pdf_pages_to_images(cls, pdf_file, dpi: int = 150, password: str = "") -> list[str]:
        """
        Render each PDF page to a base-64 encoded PNG for Claude Vision OCR.
        Handles encrypted PDFs using the supplied password.
        Returns an empty list if PyMuPDF is not installed or rendering fails.
        """
        if _fitz is None:
            return []
        images = []
        try:
            pdf_file.seek(0)
            doc = _fitz.open(stream=pdf_file.read(), filetype="pdf")
            if doc.is_encrypted:
                if not password or doc.authenticate(password) == 0:
                    doc.close()
                    return []
            mat = _fitz.Matrix(dpi / 72, dpi / 72)
            for page in doc:
                pix = page.get_pixmap(matrix=mat)
                png_bytes = pix.tobytes("png")
                images.append(base64.b64encode(png_bytes).decode("utf-8"))
            doc.close()
        except Exception:
            pass
        return images

    @classmethod
    def needs_ocr(cls, text: str) -> bool:
        """Return True if extracted text is too sparse to be useful."""
        return len(text.strip()) < cls._OCR_THRESHOLD

    @staticmethod
    def clean_pdf_text(text):
        text = " ".join((text or "").split())
        return re.sub(r"[\x00-\x1f\x7f-\x9f]", "", text)

    @classmethod
    def extract_financial_data_from_csv(cls, uploaded_file):
        try:
            uploaded_file.seek(0)
            decoded = uploaded_file.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            uploaded_file.seek(0)
            decoded = uploaded_file.read().decode("latin-1")

        sample = decoded[:2048]
        delimiter = ","
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            delimiter = dialect.delimiter
        except csv.Error:
            delimiter = "," if decoded.count(",") >= decoded.count(";") else ";"

        reader = csv.DictReader(io.StringIO(decoded), delimiter=delimiter)
        if not reader.fieldnames:
            raise ValueError("CSV file is missing a header row.")

        headers = {cls._normalize_header(header): header for header in reader.fieldnames if header}
        date_field = cls._find_header(headers, ("date", "transactiondate", "posteddate", "valuedate", "trandate"))
        description_field = cls._find_header(
            headers,
            ("description", "details", "narration", "memo", "reference", "transactiondescription"),
        )
        amount_field = cls._find_header(headers, ("amount", "transactionamount", "netamount"))
        debit_field = cls._find_header(headers, ("debit", "withdrawal", "moneyout", "debitamount"))
        credit_field = cls._find_header(headers, ("credit", "deposit", "moneyin", "creditamount"))
        balance_field = cls._find_header(headers, ("balance", "runningbalance", "closingbalance", "availablebalance"))
        transaction_type_field = cls._find_header(headers, ("transactiontype", "type", "entrytype"))

        if not date_field or not description_field or (not amount_field and not (debit_field or credit_field)):
            raise ValueError(
                "CSV must include date and description columns, plus either amount or debit/credit columns. "
                f"Found headers: {', '.join(reader.fieldnames)}"
            )

        transactions = []
        balances = []
        for raw_row in reader:
            row = {key: (value or "").strip() for key, value in raw_row.items() if key}
            if not any(row.values()):
                continue

            transaction_date = cls._parse_date(row.get(date_field, ""))
            if not transaction_date:
                continue

            description = row.get(description_field) or "Bank transaction"
            tx_type, amount = cls._resolve_amounts(
                row,
                amount_field=amount_field,
                debit_field=debit_field,
                credit_field=credit_field,
                transaction_type_field=transaction_type_field,
            )
            balance_value = cls._parse_decimal(row.get(balance_field, "")) if balance_field else None
            if balance_value is not None:
                balances.append(balance_value)

            if amount == Decimal("0.00") and tx_type == "TRANSFER" and not description:
                continue

            transactions.append(
                {
                    "date": transaction_date.isoformat(),
                    "description": description,
                    "amount": float(amount),
                    "type": tx_type,
                    "balance": float(balance_value) if balance_value is not None else None,
                }
            )

        if not transactions:
            raise ValueError("No transactions could be parsed from the CSV.")

        transactions.sort(key=lambda tx: tx["date"])
        opening_balance = balances[0] if balances else Decimal("0.00")
        closing_balance = balances[-1] if balances else opening_balance + sum(
            Decimal(str(tx["amount"])) if tx["type"] == "DEPOSIT" else -Decimal(str(tx["amount"]))
            for tx in transactions
            if tx["type"] != "TRANSFER"
        )
        total_deposits = sum(Decimal(str(tx["amount"])) for tx in transactions if tx["type"] == "DEPOSIT")
        total_withdrawals = sum(Decimal(str(tx["amount"])) for tx in transactions if tx["type"] == "WITHDRAWAL")

        return {
            "statement_period_start": transactions[0]["date"],
            "statement_period_end": transactions[-1]["date"],
            "opening_balance": float(opening_balance),
            "closing_balance": float(closing_balance),
            "total_deposits": float(total_deposits),
            "total_withdrawals": float(total_withdrawals),
            "transactions": [
                {key: value for key, value in tx.items() if key != "balance"} for tx in transactions
            ],
            "raw_text": cls._csv_rows_to_text(transactions, opening_balance, closing_balance),
        }

    @classmethod
    def build_fallback_financial_data_from_text(cls, text):
        """
        Regex-based fallback parser.  Handles:
          • M-PESA statement summary tables (PAID IN / PAID OUT columns)
          • Generic bank statements via date+amount heuristics
        """
        cleaned = cls.clean_pdf_text(text)

        # ── M-PESA / mobile-money summary block ──────────────────────────────
        mpesa_result = cls._parse_mpesa_summary(cleaned)
        if mpesa_result:
            return mpesa_result

        # ── Generic fallback ──────────────────────────────────────────────────
        transactions_for_dates = cls._parse_mpesa_detail_rows(cleaned)
        tx_dates = sorted(tx["date"] for tx in transactions_for_dates if tx.get("date"))

        dates = tx_dates if tx_dates else cls.extract_dates(cleaned)
        amounts = cls.extract_numbers(cleaned)
        if not dates and not amounts:
            return None

        # All amounts in bank statements are positive; can't distinguish
        # deposits from withdrawals without column context — use totals heuristic:
        # first large number after "total" or similar keyword is a safe aggregate.
        total_in, total_out = cls._extract_totals_from_text(cleaned)

        transactions = cls._parse_transaction_rows(cleaned)

        opening_balance = amounts[0] if amounts else 0.0
        closing_balance = amounts[-1] if len(amounts) > 1 else opening_balance

        return {
            "statement_period_start": dates[0] if dates else None,
            "statement_period_end": dates[-1] if len(dates) > 1 else (dates[0] if dates else None),
            "opening_balance": opening_balance,
            "closing_balance": closing_balance,
            "total_deposits": total_in,
            "total_withdrawals": total_out,
            "transactions": transactions,
        }

    @classmethod
    def parse_mpesa_transactions(cls, text: str) -> list:
        """
        Public method to parse M-PESA transactions using all available methods.
        This is called as a fallback when AI extraction fails.
        """
        cleaned = cls.clean_pdf_text(text)

        # Try detailed row parsing first (most accurate)
        transactions = cls._parse_mpesa_detail_rows(cleaned)

        # If we got transactions from detailed parsing, use them
        if transactions:
            return transactions

        # Fallback to generic transaction parsing
        transactions = cls._parse_transaction_rows(cleaned)

        return transactions

    @classmethod
    def _parse_mpesa_summary(cls, text: str) -> dict | None:
        """
        Parse the SUMMARY block found in M-PESA / Safaricom statements:

            TRANSACTION TYPE    PAID IN    PAID OUT
            RECEIVED MONEY      246151.00  0.00
            SEND MONEY          0.00       67105.00
            ...
            TOTAL               412411.88  412706.07

        Returns a financial_data dict if a valid summary is found, else None.
        """
        # Look for the summary header + TOTAL line
        total_match = re.search(
            r"TOTAL\s+([\d,]+\.?\d*)\s+([\d,]+\.?\d*)",
            text, re.IGNORECASE,
        )
        if not total_match:
            return None

        def _n(s):
            return float(s.replace(",", ""))

        total_in  = _n(total_match.group(1))
        total_out = _n(total_match.group(2))

        # Build synthetic transactions from each summary row
        transactions = []
        row_pattern = re.compile(
            r"^(?P<desc>[A-Z][A-Z &()/_-]+?)\s{2,}(?P<in>[\d,]+\.?\d*)\s+([\d,]+\.?\d*)$",
            re.MULTILINE,
        )
        for m in row_pattern.finditer(text):
            amount_in = _n(m.group("in"))
            if amount_in > 0:
                transactions.append({
                    "date": None,
                    "description": m.group("desc").strip(),
                    "amount": amount_in,
                    "type": "DEPOSIT",
                })

        out_pattern = re.compile(
            r"^(?P<desc>[A-Z][A-Z &()/_-]+?)\s{2,}[\d,]+\.?\d*\s+(?P<out>[\d,]+\.\d+)$",
            re.MULTILINE,
        )
        for m in out_pattern.finditer(text):
            amount_out = _n(m.group("out"))
            if amount_out > 0:
                transactions.append({
                    "date": None,
                    "description": m.group("desc").strip(),
                    "amount": amount_out,
                    "type": "WITHDRAWAL",
                })

        # Also parse the detailed transaction rows that follow the summary
        detailed = cls._parse_mpesa_detail_rows(text)
        if detailed:
            transactions = detailed  # prefer detail rows if available

        # Derive period from actual transaction dates (reliable) rather than raw text scan
        tx_dates = sorted(tx["date"] for tx in transactions if tx.get("date"))
        if tx_dates:
            dates = [tx_dates[0], tx_dates[-1]]
        else:
            dates = [d for d in cls.extract_dates(text) if d]

        # Closing balance — look for pattern like "Closing Balance 234.00" or just balance after "TOTAL"
        closing = 0.0
        closing_match = re.search(r"closing\s*balance\s*[:\-]?\s*([\d,]+\.?\d*)", text, re.IGNORECASE)
        if not closing_match:
            # Try looking for a number that appears to be the balance near the total
            # In some statements it's just a number after the total out.
            after_total = text[total_match.end():total_match.end()+100]
            bal_match = re.search(r"([\d,]+\.\d{2})", after_total)
            if bal_match:
                closing = _n(bal_match.group(1))
        else:
            closing = _n(closing_match.group(1))

        return {
            "statement_period_start": dates[0] if dates else None,
            "statement_period_end": dates[-1] if len(dates) > 1 else (dates[0] if dates else None),
            "opening_balance": 0.0,
            "closing_balance": closing,
            "total_deposits": total_in,
            "total_withdrawals": total_out,
            "transactions": transactions,
        }

    @classmethod
    def _parse_mpesa_detail_rows(cls, text: str) -> list:
        """
        Parse individual M-PESA transaction rows.

        The Safaricom M-PESA mini-statement extracts each PDF row as a long concatenated
        string containing ONE deposit ('Completed — DEPOSIT KES X') plus MANY embedded
        withdrawal lines, each identified by its 10-char receipt code + ISO date.

        Example withdrawal embedded in description:
          TJVPE8QU4X 2025-10-31 10:25:05 Airtime Purchase Completed -20.00 151.61

        Strategy:
          Pattern 5  - Receipt-code anchored lines (PRIMARY — most reliable for M-PESA)
          Pattern 1  - Traditional columnar dd/mm/yyyy paid_in paid_out (fallback only)
          Pattern 2  - Month-name grouped DEPOSIT rows (fallback only)
        """
        transactions = []

        # ── Pattern 5 (PRIMARY): Receipt-code anchored transaction lines ─────────
        # Uses ISO dates embedded in receipt lines (YYYY-MM-DD) — immune to PDF
        # page-header date artifacts like "Dec. 25, 2027".
        receipt_wd_re = re.compile(
            r"[A-Z0-9]{10}\s+"
            r"(\d{4}-\d{2}-\d{2})\s+"           # ISO date (authoritative)
            r"\d{2}:\d{2}:\d{2}\s+"
            r"(.+?)\s+"                           # description
            r"Completed\s+"
            r"(-[\d,]+\.\d{2})"                  # negative amount → withdrawal
            r"\s+[\d,]+\.\d{2}",                 # running balance
            re.MULTILINE,
        )
        receipt_dep_re = re.compile(
            r"[A-Z0-9]{10}\s+"
            r"(\d{4}-\d{2}-\d{2})\s+"
            r"\d{2}:\d{2}:\d{2}\s+"
            r"(.+?)\s+"
            r"Completed\s+.{1,4}\s*DEPOSIT\s+KES\s*([\d,]+\.\d{2})",
            re.IGNORECASE | re.MULTILINE,
        )

        for m in receipt_wd_re.finditer(text):
            iso_date = m.group(1)
            desc     = m.group(2).strip()
            amount   = abs(float(m.group(3).replace(",", "")))
            if amount <= 0:
                continue
            parsed_date = cls._parse_date(iso_date, strict=True)
            if not parsed_date:
                continue
            transactions.append({"date": parsed_date.isoformat(), "description": desc, "amount": amount, "type": "WITHDRAWAL"})

        for m in receipt_dep_re.finditer(text):
            iso_date = m.group(1)
            desc     = m.group(2).strip()
            amount   = float(m.group(3).replace(",", ""))
            parsed_date = cls._parse_date(iso_date, strict=True)
            if not parsed_date:
                continue
            transactions.append({"date": parsed_date.isoformat(), "description": desc, "amount": amount, "type": "DEPOSIT"})

        # If Pattern 5 found receipt-anchored transactions, skip the fallback patterns —
        # Pattern 1 is known to misparse ISO timestamps (e.g. "26-04-16" from "2026-04-16")
        # as past dates and merges multiple transactions into one description row.
        if transactions:
            seen = set()
            unique = []
            for tx in transactions:
                key = (tx["date"], tx["description"], tx["amount"], tx["type"])
                if key not in seen:
                    seen.add(key)
                    unique.append(tx)
            return unique

        # ── Pattern 2: Month-name grouped DEPOSIT rows (fallback) ──────────────
        row_re2 = re.compile(
            r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{1,2},?\s+\d{4})"
            r"\s+\d{1,2}:\d{2}:\d{2}"
            r"\s+(.+?)\s+"
            r"(?:DEPOSIT|DEPOSIT\s+KES)\s*"
            r"([\d,]+\.\d{2})",
            re.IGNORECASE | re.MULTILINE,
        )
        for m in row_re2.finditer(text):
            raw_date = m.group(1)
            desc = m.group(2).strip()
            amount = float(m.group(3).replace(",", ""))
            parsed_date = cls._parse_date(raw_date, strict=True)
            if not parsed_date:
                continue
            transactions.append({"date": parsed_date.isoformat(), "description": desc, "amount": amount, "type": "DEPOSIT"})

        if transactions:
            seen = set()
            unique = []
            for tx in transactions:
                key = (tx["date"], tx["description"], tx["amount"], tx["type"])
                if key not in seen:
                    seen.add(key)
                    unique.append(tx)
            return unique

        # ── Pattern 1: Traditional columnar format (last resort fallback) ────────
        # WARNING: This pattern can misparse ISO timestamps embedded in text as dates.
        # Only use when no receipt-code lines were found (non-M-PESA statements).
        row_re1 = re.compile(
            r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})"
            r"(?:\s+\d{1,2}:\d{2})?"
            r"\s+(.+?)\s+"
            r"([\d,]+\.\d{2})\s+"
            r"([\d,]+\.\d{2})"
            r"(?:\s+([\d,]+\.\d{2}))?",
            re.MULTILINE,
        )
        for m in row_re1.finditer(text):
            raw_date = m.group(1)
            desc     = m.group(2).strip()
            paid_in  = float(m.group(3).replace(",", ""))
            paid_out = float(m.group(4).replace(",", ""))
            parsed_date = cls._parse_date(raw_date, strict=True)
            if not parsed_date:
                continue
            date_str = parsed_date.isoformat()
            if paid_in > 0 and paid_out == 0:
                transactions.append({"date": date_str, "description": desc, "amount": paid_in,  "type": "DEPOSIT"})
            elif paid_out > 0 and paid_in == 0:
                transactions.append({"date": date_str, "description": desc, "amount": paid_out, "type": "WITHDRAWAL"})
            elif paid_in > 0:
                transactions.append({"date": date_str, "description": desc, "amount": paid_in,  "type": "DEPOSIT"})

        seen = set()
        unique_transactions = []
        for tx in transactions:
            key = (tx["date"], tx["description"], tx["amount"], tx["type"])
            if key not in seen:
                seen.add(key)
                unique_transactions.append(tx)

        return unique_transactions

    @classmethod
    def _extract_totals_from_text(cls, text: str) -> tuple:
        """Return (total_in, total_out) from a generic statement's TOTAL line."""
        m = re.search(r"total\s+([\d,]+\.?\d*)\s+([\d,]+\.?\d*)", text, re.IGNORECASE)
        if m:
            return float(m.group(1).replace(",", "")), float(m.group(2).replace(",", ""))
        return 0.0, 0.0

    @classmethod
    def _parse_transaction_rows(cls, text: str) -> list:
        """Generic row parser — date + description + amount."""
        transactions = []
        row_re = re.compile(
            r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})"
            r"\s+(.+?)\s+"
            r"([\d,]+\.\d{2})",
            re.MULTILINE,
        )
        for m in row_re.finditer(text):
            parsed_date = cls._parse_date(m.group(1))
            date_str = parsed_date.isoformat() if parsed_date else m.group(1)
            amount = float(m.group(3).replace(",", ""))
            transactions.append({
                "date": date_str,
                "description": m.group(2).strip(),
                "amount": amount,
                "type": "DEPOSIT",  # unknown direction — treat as deposit
            })
        return transactions

    @staticmethod
    def extract_numbers(text):
        pattern = r"-?\$?\s*\d{1,3}(?:,\d{3})*(?:\.\d{2})?"
        matches = re.findall(pattern, text or "")
        results = []
        for match in matches:
            value = match.replace("$", "").replace(",", "").replace(" ", "")
            try:
                results.append(float(value))
            except ValueError:
                continue
        return results

    @classmethod
    def extract_dates(cls, text):
        date_patterns = [
            r"\d{1,2}/\d{1,2}/\d{2,4}",          # 16/10/2025
            r"\d{4}-\d{1,2}-\d{1,2}",             # 2025-10-16
            r"\d{1,2}-\w{3}-\d{2,4}",             # 16-Oct-2025
            r"\w{3,9}\s+\d{1,2},?\s+\d{4}",       # Oct 25, 2031 or October 25, 2031
        ]
        parsed_dates = []
        for pattern in date_patterns:
            for match in re.findall(pattern, text or ""):
                parsed = cls._parse_date(match)
                if parsed:
                    parsed_dates.append(parsed.isoformat())
        return parsed_dates

    @staticmethod
    def _find_header(headers, candidates):
        for candidate in candidates:
            if candidate in headers:
                return headers[candidate]
        return None

    @staticmethod
    def _normalize_header(header):
        return re.sub(r"[^a-z0-9]", "", (header or "").strip().lower())

    @classmethod
    def _parse_date(cls, raw_value, strict=False):
        """
        Parse a date string using known formats.
        If strict=True, rejects dates that are clearly outliers (future or distant past).
        """
        value = (raw_value or "").strip()
        if not value:
            return None

        # For M-PESA statements, handle "Oct. 25, 2031" format by stripping the period
        original_value = value

        # First try with original value
        for fmt in cls.DATE_FORMATS:
            try:
                d = datetime.strptime(value, fmt).date()
                if not (1990 <= d.year <= 2099):
                    continue
                
                if strict:
                    current_year = datetime.now().year
                    if d.year > current_year or d.year < current_year - 5:
                        continue

                return d
            except ValueError:
                continue

        # Try stripping periods from month names (e.g., "Oct." -> "Oct")
        value_without_period = re.sub(r'\.(\s)', r'\1', original_value)
        if value_without_period != original_value:
            for fmt in cls.DATE_FORMATS:
                try:
                    d = datetime.strptime(value_without_period, fmt).date()
                    if not (1990 <= d.year <= 2099):
                        continue
                    
                    if strict:
                        current_year = datetime.now().year
                        if d.year > current_year or d.year < current_year - 5:
                            continue
                            
                    return d
                except ValueError:
                    continue

        return None

    @staticmethod
    def _parse_decimal(raw_value):
        value = (raw_value or "").strip()
        if not value:
            return None
        normalized = (
            value.replace(",", "")
            .replace("$", "")
            .replace("(", "-")
            .replace(")", "")
            .replace("CR", "")
            .replace("DR", "")
            .strip()
        )
        try:
            return Decimal(normalized)
        except InvalidOperation:
            return None

    @classmethod
    def _resolve_amounts(cls, row, amount_field=None, debit_field=None, credit_field=None, transaction_type_field=None):
        if credit_field or debit_field:
            credit = cls._parse_decimal(row.get(credit_field, "")) if credit_field else None
            debit = cls._parse_decimal(row.get(debit_field, "")) if debit_field else None
            if credit not in (None, Decimal("0")):
                return "DEPOSIT", abs(credit)
            if debit not in (None, Decimal("0")):
                return "WITHDRAWAL", abs(debit)

        amount = cls._parse_decimal(row.get(amount_field, "")) if amount_field else None
        transaction_type = (row.get(transaction_type_field, "") if transaction_type_field else "").strip().upper()
        if amount is None:
            if "DEPOSIT" in transaction_type or "CREDIT" in transaction_type:
                return "DEPOSIT", Decimal("0.00")
            if "WITHDRAW" in transaction_type or "DEBIT" in transaction_type:
                return "WITHDRAWAL", Decimal("0.00")
            return "TRANSFER", Decimal("0.00")
        if "WITHDRAW" in transaction_type or "DEBIT" in transaction_type:
            return "WITHDRAWAL", abs(amount)
        if "DEPOSIT" in transaction_type or "CREDIT" in transaction_type:
            return "DEPOSIT", abs(amount)
        if amount > 0:
            return "DEPOSIT", amount
        if amount < 0:
            return "WITHDRAWAL", abs(amount)
        return "TRANSFER", Decimal("0.00")

    @staticmethod
    def _csv_rows_to_text(transactions, opening_balance, closing_balance):
        lines = [
            "Imported CSV bank statement",
            f"Opening balance: {opening_balance}",
            f"Closing balance: {closing_balance}",
        ]
        for tx in transactions:
            lines.append(f"{tx['date']} | {tx['description']} | {tx['type']} | {tx['amount']}")
        return "\n".join(lines)
