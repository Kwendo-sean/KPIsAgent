import json
import logging
import re
import time

from django.conf import settings

logger = logging.getLogger("kpi.ai_agent")

try:
    import anthropic as _anthropic_sdk
except ImportError:
    _anthropic_sdk = None

from .pdf_extractor import BankStatementPDFExtractor
from .pii_redactor import redact as _redact_pii

_CLAUDE_MODEL = "claude-haiku-4-5-20251001"

# Max characters per extraction batch.
# ~15k chars ≈ ~110 transactions → ~3,500 tokens output — well within Haiku's 8,192 limit.
_BATCH_CHAR_LIMIT = 15_000

_RETRY_DELAYS = [1, 2, 4]  # seconds between retries


def _claude_client():
    if not _anthropic_sdk:
        logger.warning("anthropic SDK not installed")
        return None

    import os
    from pathlib import Path

    try:
        from dotenv import load_dotenv as _lde
        _lde(Path(__file__).resolve().parent.parent / ".env", override=False)
    except Exception:
        pass

    api_key = (
        getattr(settings, "ANTHROPIC_API_KEY", "").strip()
        or os.environ.get("ANTHROPIC_API_KEY", "").strip()
    )
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY is not set — AI analysis disabled")
        return None
    return _anthropic_sdk.Anthropic(api_key=api_key)


def _strip_code_fences(text: str) -> str:
    import re as _re
    stripped = _re.sub(r"^```[a-zA-Z]*\s*\n?", "", text.strip())
    stripped = _re.sub(r"\n?```\s*$", "", stripped.strip())
    return stripped.strip()


def _extract_html_body(text: str) -> str:
    import re as _re
    body_match = _re.search(r"<body[^>]*>(.*?)</body>", text, _re.DOTALL | _re.IGNORECASE)
    if body_match:
        return body_match.group(1).strip()
    cleaned = _re.sub(r"<!DOCTYPE[^>]*>", "", text, flags=_re.IGNORECASE)
    cleaned = _re.sub(r"<html[^>]*>|</html>", "", cleaned, flags=_re.IGNORECASE)
    cleaned = _re.sub(r"<head[^>]*>.*?</head>", "", cleaned, flags=_re.DOTALL | _re.IGNORECASE)
    cleaned = _re.sub(r"<body[^>]*>|</body>", "", cleaned, flags=_re.IGNORECASE)
    return cleaned.strip()


def _ask(client, prompt: str, max_tokens: int = 1024) -> str | None:
    """Send a prompt to Claude with retry on transient failures."""
    if client is None:
        return None
    for attempt, delay in enumerate([0] + _RETRY_DELAYS, start=1):
        if delay:
            time.sleep(delay)
        try:
            response = client.messages.create(
                model=_CLAUDE_MODEL,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text if response.content else None
        except Exception as e:
            if attempt <= len(_RETRY_DELAYS):
                logger.warning("_ask attempt %d failed: %s — retrying in %ss", attempt, e, _RETRY_DELAYS[attempt - 1] if attempt <= len(_RETRY_DELAYS) else 0)
            else:
                logger.error("_ask failed after %d attempts: %s", attempt, e)
                return None
    return None


class HospitalKPIAgent:
    """AI and fallback logic for analysing hospital financial data."""

    CURRENCY_UNIT = "KES"
    DAILY_CURRENCY_UNIT = "KES/Day"

    def __init__(self):
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client = _claude_client()
        return self._client

    # ──────────────────────────────────────────────
    # OCR  (scanned PDF pages → text via Claude Vision)
    # ──────────────────────────────────────────────

    def ocr_pdf_pages(self, page_images_b64: list[str]) -> str | None:
        """
        Send PDF page images to Claude Vision for OCR.
        Processes in batches of 5 to respect API limits.
        """
        if self.client is None or not page_images_b64:
            return None

        all_text_parts = []
        batch_size = 5
        for batch_start in range(0, len(page_images_b64), batch_size):
            batch = page_images_b64[batch_start:batch_start + batch_size]
            content = []
            for img_b64 in batch:
                content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": img_b64},
                })
            content.append({
                "type": "text",
                "text": (
                    "You are an OCR engine. Extract ALL text from these bank statement "
                    "page images, preserving structure: dates, transaction descriptions, "
                    "amounts (debits, credits, balances). Output the raw text only — "
                    "no commentary, no markdown fencing."
                ),
            })
            try:
                response = self.client.messages.create(
                    model=_CLAUDE_MODEL,
                    max_tokens=4096,
                    messages=[{"role": "user", "content": content}],
                )
                if response.content:
                    all_text_parts.append(response.content[0].text)
            except Exception as e:
                logger.error("OCR batch %d failed: %s", batch_start // batch_size + 1, e)

        return "\n".join(all_text_parts) if all_text_parts else None

    # ──────────────────────────────────────────────
    # Batched transaction extraction
    # ──────────────────────────────────────────────

    @staticmethod
    def _normalize_text(text: str) -> str:
        """
        For M-PESA and similar concatenated statement text (no newlines),
        insert a newline before each transaction's receipt code so that
        line-based chunking splits cleanly at transaction boundaries.
        """
        mpesa_re = re.compile(r'([A-Z0-9]{10,12})\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})')
        if not mpesa_re.search(text):
            return text
        return mpesa_re.sub(r'\n\1 \2', text)

    @staticmethod
    def _chunk_text(text: str, max_chars: int) -> list[str]:
        """Split text at line boundaries, falling back to word boundaries for very long lines."""
        if len(text) <= max_chars:
            return [text]
        chunks = []
        lines = text.splitlines(keepends=True)
        current: list[str] = []
        current_len = 0
        for line in lines:
            if len(line) > max_chars:
                if current:
                    chunks.append("".join(current))
                    current, current_len = [], 0
                start = 0
                while start < len(line):
                    end = start + max_chars
                    if end >= len(line):
                        current.append(line[start:])
                        current_len += len(line) - start
                        break
                    split_at = line.rfind(" ", start, end)
                    if split_at <= start:
                        split_at = end
                    chunks.append(line[start:split_at])
                    start = split_at + 1
            elif current_len + len(line) > max_chars and current:
                chunks.append("".join(current))
                current = [line]
                current_len = len(line)
            else:
                current.append(line)
                current_len += len(line)
        if current:
            chunks.append("".join(current))
        return chunks

    def _extract_batch(self, chunk: str, batch_num: int, total_batches: int) -> list[dict]:
        """Send one text chunk to Claude and return a list of parsed transactions."""
        safe_chunk = _redact_pii(chunk)
        prompt = (
            f"You are a financial transaction extraction expert specialised in Kenyan bank statements.\n"
            f"Extract ALL transactions from this bank statement text segment.\n"
            f"This is segment {batch_num} of {total_batches} from the same statement.\n\n"
            "── M-PESA FORMAT GUIDE ──────────────────────────────────────────────────\n"
            "If the text is an M-PESA statement, each transaction line begins with a\n"
            "10–12 character receipt code (e.g. UDEPE0P493) followed by date (YYYY-MM-DD)\n"
            "and time (HH:MM:SS). Parse each such line as one transaction.\n\n"
            "DEPOSIT keywords: 'Funds received from', 'Customer Payment', 'DEPOSIT KES',\n"
            "  'Salary', 'Received'\n"
            "WITHDRAWAL keywords: 'Sent to', 'Buy Goods', 'Withdraw Cash', 'Pay Bill',\n"
            "  'Airtime', 'Business Payment', 'Agent Withdrawal', 'Merchant Payment'\n\n"
            "Withdrawal amount = the NEGATIVE number before the running balance (remove the minus sign).\n"
            "Deposit amount   = the number after 'DEPOSIT KES' or the first positive number.\n\n"
            "Example M-PESA lines and correct extraction:\n"
            "  UDEPE0P493 2026-04-14 18:40:07 Customer Payment Funds received from JOHN DOE Completed DEPOSIT KES 5,000.00 10,927.44\n"
            "  → {\"date\":\"2026-04-14\",\"description\":\"Customer Payment from JOHN DOE\",\"amount\":5000.00,\"type\":\"DEPOSIT\",\"category\":\"Customer Payment\"}\n\n"
            "  TJVPE8QU4X 2025-10-31 10:25:05 Airtime Purchase Completed -20.00 151.61\n"
            "  → {\"date\":\"2025-10-31\",\"description\":\"Airtime Purchase\",\"amount\":20.00,\"type\":\"WITHDRAWAL\",\"category\":\"Airtime\"}\n\n"
            "── GENERAL RULES ────────────────────────────────────────────────────────\n"
            "- Extract EVERY transaction — do not skip any.\n"
            "- date: YYYY-MM-DD format.\n"
            "- amount: positive number only, no commas, no currency symbols. E.g. 12345.67\n"
            "- type: DEPOSIT (money IN) or WITHDRAWAL (money OUT).\n"
            "- category: one of: Customer Payment, Salary, Insurance, Refund,\n"
            "    Staff Salaries, Medical Supplies, Utilities, Rent, Equipment,\n"
            "    Airtime, Buy Goods, Pay Bill, Send Money, Withdraw Cash, Other.\n"
            "- Return ONLY a raw JSON array. No commentary, no markdown.\n"
            "- If no transactions found in this segment, return: []\n\n"
            f"Text segment:\n{safe_chunk}\n\n"
            'Return ONLY a JSON array:\n'
            '[{"date":"YYYY-MM-DD","description":"...","amount":0.00,"type":"DEPOSIT","category":"Other"}]'
        )
        text = _ask(self.client, prompt, max_tokens=8192)
        if not text:
            logger.warning("Batch %d/%d: no response from Claude", batch_num, total_batches)
            return []

        clean = _strip_code_fences(text)
        match = re.search(r"\[.*\]", clean, re.DOTALL)
        if not match:
            logger.warning("Batch %d/%d: no JSON array found in response", batch_num, total_batches)
            return []

        try:
            raw_list = json.loads(match.group())
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error("Batch %d JSON parse error: %s", batch_num, exc)
            return []

        validated = []
        for tx in raw_list:
            if not isinstance(tx, dict):
                continue
            date_str = str(tx.get("date", "")).strip()
            description = str(tx.get("description", "Bank transaction")).strip() or "Bank transaction"
            try:
                amount = abs(float(str(tx.get("amount", 0)).replace(",", "")))
            except (ValueError, TypeError):
                continue
            tx_type = str(tx.get("type", "DEPOSIT")).upper()
            if tx_type not in ("DEPOSIT", "WITHDRAWAL", "TRANSFER"):
                tx_type = "DEPOSIT"
            category = str(tx.get("category", "Other")).strip() or "Other"
            if amount <= 0:
                continue
            validated.append({
                "date": date_str,
                "description": description,
                "amount": amount,
                "type": tx_type,
                "category": category,
            })
        return validated

    def extract_transactions_in_batches(self, raw_text: str) -> list[dict]:
        """
        Split raw_text into chunks and extract transactions from each chunk
        via sequential Claude API calls. Merges and deduplicates results.
        """
        normalized = self._normalize_text(raw_text)
        chunks = self._chunk_text(normalized, _BATCH_CHAR_LIMIT)
        logger.info("Batched extraction: %s chars → %d batch(es)", f"{len(raw_text):,}", len(chunks))

        all_transactions: list[dict] = []
        for idx, chunk in enumerate(chunks):
            batch_num = idx + 1
            logger.info("Batch %d/%d: %s chars", batch_num, len(chunks), f"{len(chunk):,}")
            batch_txs = self._extract_batch(chunk, batch_num, len(chunks))
            logger.info("Batch %d → %d transactions", batch_num, len(batch_txs))
            all_transactions.extend(batch_txs)

        # Deduplicate: allow same description+type+date if amounts differ by >1% (different transactions)
        seen: set[tuple] = set()
        unique: list[dict] = []
        for tx in all_transactions:
            amount_bucket = round(tx.get("amount", 0) / 10) * 10  # bucket to nearest 10
            key = (tx.get("date"), tx.get("description", "")[:80], amount_bucket, tx.get("type"))
            if key not in seen:
                seen.add(key)
                unique.append(tx)

        logger.info("Batched extraction complete: %d unique transactions", len(unique))
        return unique

    def _validate_completeness(self, transactions: list[dict], stated_total_deposits: float, stated_total_withdrawals: float) -> None:
        """Log a warning if extracted totals differ significantly from stated totals."""
        if not stated_total_deposits and not stated_total_withdrawals:
            return
        extracted_dep = sum(t["amount"] for t in transactions if t.get("type") == "DEPOSIT")
        extracted_wd  = sum(t["amount"] for t in transactions if t.get("type") == "WITHDRAWAL")
        if stated_total_deposits > 0:
            dep_diff_pct = abs(extracted_dep - stated_total_deposits) / stated_total_deposits * 100
            if dep_diff_pct > 20:
                logger.warning(
                    "Completeness check: stated deposits=%.2f, extracted=%.2f (%.1f%% gap — some transactions may be missing)",
                    stated_total_deposits, extracted_dep, dep_diff_pct,
                )
        if stated_total_withdrawals > 0:
            wd_diff_pct = abs(extracted_wd - stated_total_withdrawals) / stated_total_withdrawals * 100
            if wd_diff_pct > 20:
                logger.warning(
                    "Completeness check: stated withdrawals=%.2f, extracted=%.2f (%.1f%% gap)",
                    stated_total_withdrawals, extracted_wd, wd_diff_pct,
                )

    # ──────────────────────────────────────────────
    # Extraction
    # ──────────────────────────────────────────────

    def extract_csv_structure_with_ai(self, csv_head_text: str) -> dict | None:
        safe_csv = _redact_pii(csv_head_text)
        prompt = f"""You are a data extraction expert. Analyze the following CSV header/sample rows and identify the zero-based column indices for each financial field.

CSV Sample:
{safe_csv}

Respond ONLY with valid JSON using exactly this structure. Use null if a field is absent:
{{
    "date_index": 0,
    "description_index": 1,
    "amount_index": 2,
    "debit_index": null,
    "credit_index": null,
    "balance_index": null,
    "transaction_type_index": null
}}"""
        text = _ask(self.client, prompt, max_tokens=512)
        if text:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group())
                    if parsed:
                        return parsed
                except (json.JSONDecodeError, ValueError):
                    pass

        mapping = {k: None for k in ("date_index", "description_index", "amount_index",
                                     "debit_index", "credit_index", "balance_index",
                                     "transaction_type_index")}
        try:
            rows = [line.split(",") for line in csv_head_text.splitlines()]
            if not rows:
                return None
            headers = [h.strip().lower() for h in rows[0]]
            for i, h in enumerate(headers):
                if any(kw in h for kw in ("date", "time")):
                    mapping["date_index"] = i
                elif any(kw in h for kw in ("desc", "narrat", "detail", "particular")):
                    mapping["description_index"] = i
                elif any(kw in h for kw in ("amount", "value", "sum")):
                    mapping["amount_index"] = i
                elif "debit" in h:
                    mapping["debit_index"] = i
                elif "credit" in h:
                    mapping["credit_index"] = i
                elif "balance" in h:
                    mapping["balance_index"] = i
                elif any(kw in h for kw in ("type", "cr/dr", "transact_type")):
                    mapping["transaction_type_index"] = i
            if mapping["date_index"] is not None and (
                mapping["amount_index"] is not None or mapping["credit_index"] is not None
            ):
                return mapping
        except Exception:
            pass
        return None

    def extract_financial_data(self, bank_statement_text: str) -> dict | None:
        safe_text = _redact_pii(bank_statement_text)
        logger.info("extract_financial_data: %s chars", f"{len(bank_statement_text):,}")

        transactions: list[dict] = []
        if self.client is not None:
            transactions = self.extract_transactions_in_batches(bank_statement_text)

        if not transactions:
            logger.warning("Claude batched extraction returned nothing — using regex fallback")
            regex_data = BankStatementPDFExtractor.build_fallback_financial_data_from_text(bank_statement_text)
            if regex_data and regex_data.get("transactions"):
                transactions = regex_data["transactions"]
            elif regex_data:
                return regex_data
            else:
                return None

        summary_prompt = (
            "You are a financial data extraction expert specialized in M-PESA and bank statements.\n"
            "Extract ONLY the summary fields — do NOT list individual transactions.\n\n"
            "RULES:\n"
            "- Return ONLY raw JSON, no markdown, no code fences.\n"
            "- All monetary amounts: plain numbers, no commas, no currency symbols. E.g. 12345.67\n"
            "- For M-PESA: IGNORE page-header grouping dates (e.g. 'Dec. 25, 2027') — PDF layout artifacts.\n"
            "  Use ONLY the ISO dates inside receipt lines (RECEIPT_CODE YYYY-MM-DD HH:MM:SS).\n"
            "- statement_period_start = EARLIEST receipt-line date\n"
            "- statement_period_end   = LATEST receipt-line date\n"
            "- If opening balance is not stated, use 0.00\n"
            "- If closing balance is not stated, use 0.00 (will be recalculated)\n\n"
            f"Bank Statement Text (first 15000 chars):\n{safe_text[:15000]}\n\n"
            "Respond ONLY with valid JSON:\n"
            '{"statement_period_start":"YYYY-MM-DD","statement_period_end":"YYYY-MM-DD",'
            '"opening_balance":0.00,"closing_balance":0.00,"total_deposits":0.00,"total_withdrawals":0.00}'
        )
        summary_text = _ask(self.client, summary_prompt, max_tokens=512)
        logger.info("summary response: %s", repr(summary_text[:300]) if summary_text else "None")

        summary: dict = {}
        if summary_text:
            clean = _strip_code_fences(summary_text)
            match = re.search(r"\{.*\}", clean, re.DOTALL)
            if match:
                try:
                    summary = json.loads(match.group())
                except (json.JSONDecodeError, ValueError) as exc:
                    logger.error("Summary JSON parse error: %s", exc)

        # Validate completeness against stated totals from summary
        self._validate_completeness(
            transactions,
            float(summary.get("total_deposits") or 0),
            float(summary.get("total_withdrawals") or 0),
        )

        total_dep = sum(t["amount"] for t in transactions if t.get("type") == "DEPOSIT")
        total_wd  = sum(t["amount"] for t in transactions if t.get("type") == "WITHDRAWAL")

        dated = [t for t in transactions if t.get("date")]
        dated.sort(key=lambda t: t["date"])

        opening_balance = float(summary.get("opening_balance") or 0.0)
        closing_balance = float(summary.get("closing_balance") or 0.0)
        if closing_balance == 0.0:
            closing_balance = opening_balance + total_dep - total_wd

        period_start = dated[0]["date"]  if dated else summary.get("statement_period_start")
        period_end   = dated[-1]["date"] if dated else summary.get("statement_period_end")

        result = {
            "statement_period_start": period_start,
            "statement_period_end":   period_end,
            "opening_balance":  opening_balance,
            "closing_balance":  closing_balance,
            "total_deposits":   total_dep,
            "total_withdrawals": total_wd,
            "transactions": transactions,
        }
        logger.info(
            "Final result: %d transactions, deposits=%s, withdrawals=%s, period=%s → %s",
            len(transactions), f"{total_dep:,.2f}", f"{total_wd:,.2f}", period_start, period_end,
        )
        return result

    # ──────────────────────────────────────────────
    # KPI Calculation
    # ──────────────────────────────────────────────

    @staticmethod
    def _amt(t) -> float:
        val = t.get("amount", 0)
        if isinstance(val, (int, float)):
            return float(val)
        try:
            return float(str(val).replace(",", "").replace("KES", "").replace("Ksh", "").strip())
        except (ValueError, TypeError):
            return 0.0

    @staticmethod
    def _tx_type(t) -> str:
        return str(t.get("type", t.get("transaction_type", ""))).upper()

    def calculate_kpis(self, transactions, opening_balance, closing_balance, period_days=30):
        kpis = {}
        days = max(period_days, 1)

        deposits    = [t for t in transactions if self._tx_type(t) in ("DEPOSIT",  "CREDIT", "CR")]
        withdrawals = [t for t in transactions if self._tx_type(t) in ("WITHDRAWAL", "DEBIT", "DR")]

        total_deposits    = sum(self._amt(t) for t in deposits)
        total_withdrawals = sum(self._amt(t) for t in withdrawals)
        deposit_amounts    = [self._amt(t) for t in deposits]
        withdrawal_amounts = [self._amt(t) for t in withdrawals]

        expense_ratio = (total_withdrawals / total_deposits * 100) if total_deposits > 0 else 0
        net_income = total_deposits - total_withdrawals
        profit_margin = (net_income / total_deposits * 100) if total_deposits > 0 else 0
        transaction_count = len(transactions)
        avg_transaction_size = (total_deposits + total_withdrawals) / transaction_count if transaction_count else 0
        daily_withdrawal_rate = total_withdrawals / days if total_withdrawals > 0 else 0
        liquidity_ratio = closing_balance / daily_withdrawal_rate if daily_withdrawal_rate > 0 else 0
        avg_deposit_size = total_deposits / len(deposit_amounts) if deposit_amounts else 0
        avg_withdrawal_size = total_withdrawals / len(withdrawal_amounts) if withdrawal_amounts else 0
        largest_deposit = max(deposit_amounts) if deposit_amounts else 0
        largest_expense = max(withdrawal_amounts) if withdrawal_amounts else 0
        deposit_frequency = len(deposit_amounts)
        expense_frequency = len(withdrawal_amounts)
        runway_months = (liquidity_ratio / 30) if liquidity_ratio else 0
        balance_to_expense_ratio = (closing_balance / total_withdrawals * 100) if total_withdrawals > 0 else 0

        # ── Anomaly detection ────────────────────────────────────
        anomaly_count = 0
        if deposit_amounts and len(deposit_amounts) >= 3:
            mean_dep = sum(deposit_amounts) / len(deposit_amounts)
            variance = sum((x - mean_dep) ** 2 for x in deposit_amounts) / len(deposit_amounts)
            std_dep = variance ** 0.5
            anomaly_count += sum(1 for x in deposit_amounts if abs(x - mean_dep) > 2.5 * std_dep)
        if withdrawal_amounts and len(withdrawal_amounts) >= 3:
            mean_wd = sum(withdrawal_amounts) / len(withdrawal_amounts)
            variance = sum((x - mean_wd) ** 2 for x in withdrawal_amounts) / len(withdrawal_amounts)
            std_wd = variance ** 0.5
            anomaly_count += sum(1 for x in withdrawal_amounts if abs(x - mean_wd) > 2.5 * std_wd)

        # ── M-PESA specific KPIs ──────────────────────────────────
        airtime_total = sum(
            self._amt(t) for t in withdrawals
            if "airtime" in str(t.get("description", "")).lower() or
               str(t.get("category", "")).lower() == "airtime"
        )
        paybill_total = sum(
            self._amt(t) for t in withdrawals
            if "pay bill" in str(t.get("description", "")).lower() or
               str(t.get("category", "")).lower() == "pay bill"
        )
        buygoods_total = sum(
            self._amt(t) for t in withdrawals
            if "buy goods" in str(t.get("description", "")).lower() or
               str(t.get("category", "")).lower() == "buy goods"
        )
        send_money_total = sum(
            self._amt(t) for t in withdrawals
            if "sent to" in str(t.get("description", "")).lower() or
               str(t.get("category", "")).lower() == "send money"
        )

        # ── Status helper using thresholds ────────────────────────
        def _status(value, warn_threshold, critical_threshold, higher_is_worse=True):
            if higher_is_worse:
                if value >= critical_threshold:
                    return "CRITICAL"
                if value >= warn_threshold:
                    return "WARNING"
            else:
                if value <= critical_threshold:
                    return "CRITICAL"
                if value <= warn_threshold:
                    return "WARNING"
            return "HEALTHY"

        def _kpi(value, unit, ktype, desc, warn=None, crit=None, higher_is_worse=True):
            entry = {"value": value, "unit": unit, "type": ktype, "description": desc}
            if warn is not None and crit is not None:
                entry["status"] = _status(value, warn, crit, higher_is_worse)
                entry["warning_threshold"] = warn
                entry["critical_threshold"] = crit
            return entry

        kpis["Total_Revenue"]           = _kpi(total_deposits, self.CURRENCY_UNIT, "REVENUE", "Total deposits/income in the period")
        kpis["Average_Daily_Revenue"]   = _kpi(total_deposits / days, self.DAILY_CURRENCY_UNIT, "REVENUE", f"Average daily revenue over {days} days")
        kpis["Average_Deposit_Size"]    = _kpi(avg_deposit_size, self.CURRENCY_UNIT, "REVENUE", "Average size of incoming deposits")
        kpis["Deposit_Frequency"]       = _kpi(deposit_frequency, "Deposits", "REVENUE", "Number of incoming deposit transactions")
        kpis["Total_Expenses"]          = _kpi(total_withdrawals, self.CURRENCY_UNIT, "COST", "Total expenses in the period")
        kpis["Average_Daily_Expense"]   = _kpi(total_withdrawals / days, self.DAILY_CURRENCY_UNIT, "COST", f"Average daily expenses over {days} days")
        kpis["Average_Expense_Size"]    = _kpi(avg_withdrawal_size, self.CURRENCY_UNIT, "COST", "Average size of outgoing expense transactions")
        kpis["Expense_Frequency"]       = _kpi(expense_frequency, "Expenses", "COST", "Number of outgoing expense transactions")
        kpis["Expense_to_Revenue_Ratio"] = _kpi(expense_ratio, "%", "COST", "Expenses as percentage of revenue", warn=75, crit=90)
        kpis["Net_Income"]              = _kpi(net_income, self.CURRENCY_UNIT, "PROFITABILITY", "Revenue minus expenses", warn=0, crit=-1, higher_is_worse=False)
        kpis["Profit_Margin"]           = _kpi(profit_margin, "%", "PROFITABILITY", "Net profit as percentage of revenue", warn=5, crit=0, higher_is_worse=False)
        kpis["Largest_Deposit"]         = _kpi(largest_deposit, self.CURRENCY_UNIT, "PROFITABILITY", "Largest single incoming transaction")
        kpis["Largest_Expense"]         = _kpi(largest_expense, self.CURRENCY_UNIT, "PROFITABILITY", "Largest single outgoing transaction")
        kpis["Net_Cash_Flow"]           = _kpi(closing_balance - opening_balance, self.CURRENCY_UNIT, "CASHFLOW", "Change in cash balance over the period")
        kpis["Closing_Balance"]         = _kpi(closing_balance, self.CURRENCY_UNIT, "CASHFLOW", "Final cash balance", warn=50000, crit=10000, higher_is_worse=False)
        kpis["Cash_Conversion_Ratio"]   = _kpi(profit_margin, "%", "CASHFLOW", "Percentage of revenue converted to net cash")
        kpis["Cash_Runway_Months"]      = _kpi(runway_months, "Months", "CASHFLOW", "Approximate cash runway at current expense levels", warn=2, crit=1, higher_is_worse=False)
        kpis["Transaction_Count"]       = _kpi(transaction_count, "Count", "EFFICIENCY", "Total number of transactions")
        kpis["Average_Transaction_Size"] = _kpi(avg_transaction_size, self.CURRENCY_UNIT, "EFFICIENCY", "Average size of all transactions")
        kpis["Anomaly_Count"]           = _kpi(anomaly_count, "Transactions", "EFFICIENCY", "Transactions more than 2.5 std deviations from mean amount", warn=3, crit=10)
        kpis["Operating_Margin"]        = _kpi(profit_margin, "%", "FINANCIAL_HEALTH", "Operating profit margin", warn=5, crit=0, higher_is_worse=False)
        kpis["Liquidity_Days"]          = _kpi(min(liquidity_ratio, 999), "Days", "FINANCIAL_HEALTH", "Days of expenses covered by current balance", warn=30, crit=7, higher_is_worse=False)
        kpis["Balance_to_Expense_Ratio"] = _kpi(balance_to_expense_ratio, "%", "FINANCIAL_HEALTH", "Closing balance as a percentage of total expenses")

        # M-PESA specific KPIs (only include if non-zero)
        if airtime_total > 0:
            kpis["Airtime_Spend"]       = _kpi(airtime_total, self.CURRENCY_UNIT, "COST", "Total airtime purchases via M-PESA")
        if paybill_total > 0:
            kpis["PayBill_Spend"]       = _kpi(paybill_total, self.CURRENCY_UNIT, "COST", "Total Pay Bill transactions (utilities, services)")
        if buygoods_total > 0:
            kpis["Buy_Goods_Spend"]     = _kpi(buygoods_total, self.CURRENCY_UNIT, "COST", "Total Buy Goods & Services payments")
        if send_money_total > 0:
            kpis["Send_Money_Total"]    = _kpi(send_money_total, self.CURRENCY_UNIT, "COST", "Total funds sent to individuals")

        return kpis

    # ──────────────────────────────────────────────
    # Financial Health Score + Risk Tier
    # ──────────────────────────────────────────────

    @staticmethod
    def compute_health_score(kpis: dict) -> dict:
        """
        Composite 0–100 financial health score and named risk tier.

        Weights:
          Profit Margin        25 pts  (20 %+ = full marks)
          Cash Runway          25 pts  (6+ months = full marks)
          Expense Ratio        20 pts  (0 % = full marks)
          Revenue Consistency  15 pts  (≥20 deposits = full marks)
          Anomaly Penalty    −15 pts  (1.5 pts per anomaly, capped)
        """
        pm      = kpis.get("Profit_Margin",           {}).get("value", 0)
        runway  = kpis.get("Cash_Runway_Months",       {}).get("value", 0)
        er      = kpis.get("Expense_to_Revenue_Ratio", {}).get("value", 100)
        dep_f   = kpis.get("Deposit_Frequency",        {}).get("value", 0)
        anom    = kpis.get("Anomaly_Count",            {}).get("value", 0)

        pm_score      = min(25.0, max(0.0,  pm / 20.0 * 25.0))
        runway_score  = min(25.0, max(0.0,  runway / 6.0 * 25.0))
        er_score      = max(0.0, (100.0 - er) / 100.0 * 20.0)
        freq_score    = min(15.0, max(0.0,  dep_f / 20.0 * 15.0))
        penalty       = min(15.0, anom * 1.5)

        final = round(max(0.0, min(100.0, pm_score + runway_score + er_score + freq_score - penalty)))

        if   final >= 80: tier, tone = "Stable",    "green"
        elif final >= 60: tier, tone = "Watchlist", "amber"
        elif final >= 40: tier, tone = "At Risk",   "orange"
        elif final >= 20: tier, tone = "Distressed","red"
        else:             tier, tone = "Critical",  "critical"

        return {
            "score": final,
            "tier":  tier,
            "tone":  tone,
            "breakdown": {
                "profit_margin":        round(pm_score, 1),
                "cash_runway":          round(runway_score, 1),
                "expense_ratio":        round(er_score, 1),
                "revenue_consistency":  round(freq_score, 1),
                "anomaly_penalty":      round(-penalty, 1),
            },
        }

    # ──────────────────────────────────────────────
    # Recurring Payment Detection
    # ──────────────────────────────────────────────

    @staticmethod
    def detect_recurring_payments(transactions: list) -> list:
        """
        Identify withdrawal transactions that recur approximately monthly
        (20–45 day intervals) with consistent amounts (within ±25 %).

        Returns a list sorted by avg_amount desc:
          [{"description", "avg_amount", "occurrences", "avg_interval_days"}, …]
        """
        import datetime as _dt

        def _parse(d):
            if isinstance(d, _dt.date):
                return d
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
                try:
                    return _dt.datetime.strptime(str(d).strip(), fmt).date()
                except ValueError:
                    continue
            return None

        withdrawals = [
            t for t in transactions
            if str(t.get("type", t.get("transaction_type", ""))).upper()
               in ("WITHDRAWAL", "DEBIT", "DR")
        ]

        # Group by first 35 chars of normalised description
        groups: dict[str, list] = {}
        for tx in withdrawals:
            key = str(tx.get("description", "")).strip()[:35].upper()
            if not key:
                continue
            d = _parse(tx.get("date") or tx.get("transaction_date"))
            if d is None:
                continue
            groups.setdefault(key, []).append((_parse(tx.get("date") or tx.get("transaction_date")), float(tx.get("amount", 0))))

        recurring = []
        for desc, entries in groups.items():
            if len(entries) < 2:
                continue
            entries_sorted = sorted(entries, key=lambda x: x[0])
            dates   = [e[0] for e in entries_sorted]
            amounts = [e[1] for e in entries_sorted]
            intervals = [(dates[i+1] - dates[i]).days for i in range(len(dates)-1)]
            avg_interval = sum(intervals) / len(intervals)
            avg_amount   = sum(amounts) / len(amounts)
            min_amt, max_amt = min(amounts), max(amounts)
            variance_ratio = (max_amt / min_amt) if min_amt > 0 else 999

            if 20 <= avg_interval <= 45 and variance_ratio <= 1.25:
                recurring.append({
                    "description":       desc,
                    "avg_amount":        round(avg_amount, 2),
                    "occurrences":       len(entries_sorted),
                    "avg_interval_days": round(avg_interval),
                })

        return sorted(recurring, key=lambda x: x["avg_amount"], reverse=True)

    # ──────────────────────────────────────────────
    # Insights & Alerts
    # ──────────────────────────────────────────────

    def generate_insights(self, transactions, kpis, statement_period):
        prompt = f"""You are a hospital financial analyst. Based on the KPIs below, write a clear financial performance summary.

Structure your response as HTML using only these tags: <h3>, <p>, <ul>, <li>, <strong>.
Do NOT use markdown asterisks, hashes, or backticks — only plain HTML.
Cover: (1) overall performance, (2) key concerns, (3) actionable recommendations.
Use KES for currency. Be specific with numbers.

KPI Summary:
{json.dumps(kpis, default=str, indent=2)}

Statement Period: {statement_period}"""
        text = _ask(self.client, prompt, max_tokens=1024)
        if text:
            text = _strip_code_fences(text)
            text = _extract_html_body(text)
        return text if text else self._generate_local_insights(transactions, kpis, statement_period)

    def generate_alerts(self, kpis):
        alerts = []
        expense_ratio = kpis.get("Expense_to_Revenue_Ratio", {}).get("value", 0)
        profit_margin = kpis.get("Profit_Margin", {}).get("value", 0)
        liquidity_days = kpis.get("Liquidity_Days", {}).get("value", 999)
        anomaly_count = kpis.get("Anomaly_Count", {}).get("value", 0)

        if expense_ratio > 90:
            alerts.append({"severity": "CRITICAL", "title": "Critical Expense Ratio",
                           "content": f"Expenses are {expense_ratio:.1f}% of revenue. Immediate cost reduction is required."})
        elif expense_ratio > 75:
            alerts.append({"severity": "WARNING", "title": "High Expense Ratio",
                           "content": f"Expenses are {expense_ratio:.1f}% of revenue. Review cost centres."})
        if profit_margin < 0:
            alerts.append({"severity": "CRITICAL", "title": "Negative Profit Margin",
                           "content": f"Operating at a loss with {profit_margin:.1f}% margin. Immediate action required."})
        elif profit_margin < 5:
            alerts.append({"severity": "WARNING", "title": "Low Profit Margin",
                           "content": f"Profit margin of {profit_margin:.1f}% is below the recommended 10%."})
        if liquidity_days < 7:
            alerts.append({"severity": "CRITICAL", "title": "Critical Liquidity",
                           "content": f"Cash covers only {liquidity_days:.0f} days of expenses. Immediate action required."})
        elif liquidity_days < 30:
            alerts.append({"severity": "WARNING", "title": "Low Liquidity",
                           "content": f"Cash covers only {liquidity_days:.0f} days of expenses."})
        if anomaly_count >= 5:
            alerts.append({"severity": "WARNING", "title": "Unusual Transactions Detected",
                           "content": f"{anomaly_count} transactions have amounts significantly outside the normal range. Review for errors or fraud."})
        return alerts

    # ──────────────────────────────────────────────
    # Q&A
    # ──────────────────────────────────────────────

    def answer_question(self, question, kpis, bank_statement_text):
        prompt = f"""You are a hospital financial expert. Answer this question using the KPIs below.

Question: {question}

KPI Summary:
{json.dumps(kpis, default=str, indent=2)}

Give a direct, data-driven answer. Use KES for currency. Format clearly with bullet points for lists."""
        text = _ask(self.client, prompt, max_tokens=1024)
        return text if text else self._answer_locally(question, kpis)

    def answer_system_question(self, question, system_context):
        prompt = f"""You are the finance copilot for a hospital KPI dashboard.
Answer questions about uploaded statements, KPIs, transactions, comparisons, and system usage.
Stay grounded in the provided context. For general finance questions not in context, answer as a helpful finance expert.
Use KES for currency. Format clearly using bullet points (- or •) for lists. Do not use semicolons to chain multiple items.

Question:
{question}

System Context:
{json.dumps(system_context, default=str, indent=2)[:8000]}"""
        text = _ask(self.client, prompt, max_tokens=1024)
        return text if text else self._answer_from_system_context(question, system_context)

    def generate_report_summary(self, report_context):
        prompt = f"""You are preparing a finance summary report for a hospital KPI dashboard.
Write:
1. Executive summary
2. Most important KPI highlights
3. Risk areas
4. Recommended actions

Keep it concise and professional. Use KES for currency.

Analytics Context:
{json.dumps(report_context, default=str, indent=2)}"""
        text = _ask(self.client, prompt, max_tokens=1024)
        return text if text else self._build_local_report_summary(report_context)

    def generate_detailed_report(self, report_context):
        prompt = f"""You are a fractional CFO and expert financial analyst.
Write a comprehensive, multi-section financial report based on the data below.

Include:
1. Executive Summary
2. Revenue & Income Analysis
3. Expense & Cost Center Breakdown
4. Cash Flow & Liquidity Health
5. Key Risk Areas & Strategic Recommendations

Use KES for all monetary values.
Format entirely in clean Markdown: headings (##, ###), bold, bullet lists, tables where useful. Authoritative and data-driven tone.

Analytics Context:
{json.dumps(report_context, default=str, indent=2)}"""
        text = _ask(self.client, prompt, max_tokens=4096)
        if text:
            return text
        summary = self._build_local_report_summary(report_context)
        return f"# Comprehensive Financial Report\n\n## Executive Summary\n{summary}\n\n*Note: AI analysis is currently unavailable. This is a fallback summary.*"

    # ──────────────────────────────────────────────
    # Local fallbacks
    # ──────────────────────────────────────────────

    def _generate_local_insights(self, transactions, kpis, statement_period):
        rev = kpis.get("Total_Revenue", {}).get("value", 0)
        exp = kpis.get("Total_Expenses", {}).get("value", 0)
        net = kpis.get("Net_Income", {}).get("value", 0)
        margin = kpis.get("Profit_Margin", {}).get("value", 0)
        exp_ratio = kpis.get("Expense_to_Revenue_Ratio", {}).get("value", 0)
        closing = kpis.get("Closing_Balance", {}).get("value", 0)
        liquidity = kpis.get("Liquidity_Days", {}).get("value", 0)
        return (
            f"Statement period: {statement_period}. Revenue was {rev:,.2f} KES against "
            f"expenses of {exp:,.2f} KES, producing net income of {net:,.2f} KES. "
            f"Profit margin is {margin:.1f}% and the expense-to-revenue ratio is {exp_ratio:.1f}%. "
            f"Closing cash stands at {closing:,.2f} KES, covering about {liquidity:.0f} days of expenses. "
            f"The statement contains {len(transactions)} transactions."
        )

    def _answer_locally(self, question, kpis):
        q = question.lower()
        mapping = {
            "profit margin": "Profit_Margin", "net income": "Net_Income",
            "profit": "Net_Income", "income": "Net_Income",
            "revenue": "Total_Revenue", "deposit": "Total_Revenue",
            "expenses": "Total_Expenses", "spend": "Total_Expenses", "expense": "Total_Expenses",
            "cash flow": "Net_Cash_Flow", "cashflow": "Net_Cash_Flow",
            "closing balance": "Closing_Balance", "balance": "Closing_Balance",
            "liquidity": "Liquidity_Days", "runway": "Cash_Runway_Months",
            "largest expense": "Largest_Expense", "largest deposit": "Largest_Deposit",
            "anomal": "Anomaly_Count",
        }
        for phrase, metric_name in mapping.items():
            if phrase in q and metric_name in kpis:
                metric = kpis[metric_name]
                return f"{metric_name.replace('_', ' ')} is {metric['value']:,.2f} {metric['unit']}."
        return "I can answer using the imported KPI data. Ask about revenue, expenses, profit margin, cash flow, balance, or liquidity."

    def _answer_from_system_context(self, question, system_context):
        q = question.lower().strip()
        statements = system_context.get("statements", [])
        metrics = system_context.get("aggregate_metrics", {})
        transactions = system_context.get("transactions", [])
        history = system_context.get("history", [])

        for statement in statements:
            file_name = statement.get("file_name", "")
            if file_name and file_name.lower() in q:
                return (
                    f"{file_name} covers {statement.get('period')} with deposits of "
                    f"{statement.get('deposits', 0):,.2f} KES, withdrawals of "
                    f"{statement.get('withdrawals', 0):,.2f} KES, and closing balance of "
                    f"{statement.get('closing_balance', 0):,.2f} KES."
                )
        if any(word in q for word in ("previous", "before", "last answer", "earlier")):
            if history:
                prev = history[0]
                return f"Your last assistant exchange was: {prev.get('title')} → {prev.get('content')[:200]}"
        if "cashflow" in q or "cash flow" in q:
            return (f"Total cash flow activity: revenue of {metrics.get('total_revenue', 0):,.2f} KES "
                    f"and expenses of {metrics.get('total_expenses', 0):,.2f} KES across all processed statements.")
        if any(w in q for w in ("highest", "largest", "biggest")) and "closing balance" in q:
            if statements:
                top = max(statements, key=lambda s: float(s.get("closing_balance", 0) or 0))
                return f"{top.get('file_name')} has the highest closing balance at {float(top.get('closing_balance', 0)):,.2f} KES."
        if "profit" in q:
            return (f"Net profit is {metrics.get('net_profit', 0):,.2f} KES and profit margin is "
                    f"{metrics.get('profit_margin', 0):.2f}% across all processed statements.")
        if "closing balance" in q and statements:
            top = statements[0]
            return f"Closing balance for {top.get('file_name')} is {float(top.get('closing_balance', 0)):,.2f} KES."
        if "balance" in q and statements:
            top = statements[0]
            return f"The most recent file, {top.get('file_name')}, has a closing balance of {float(top.get('closing_balance', 0)):,.2f} KES."
        if "revenue" in q or "deposit" in q:
            return f"Total revenue across all processed statements: {metrics.get('total_revenue', 0):,.2f} KES."
        if "expense" in q or "spend" in q or "withdraw" in q:
            return f"Total expenses across all processed statements: {metrics.get('total_expenses', 0):,.2f} KES."

        query_terms = [
            t for t in re.findall(r"[a-z0-9]+", q)
            if len(t) > 2 and t not in {"what", "was", "were", "show", "find", "transaction",
                                        "transactions", "payment", "the", "for", "did"}
        ]
        scored = []
        for tx in transactions:
            haystack = " ".join([
                str(tx.get("file_name", "")), str(tx.get("description", "")),
                str(tx.get("category", "")), str(tx.get("date", "")),
            ]).lower()
            score = sum(1 for t in query_terms if t in haystack)
            desc = tx.get("description", "").lower()
            if query_terms and all(t in desc for t in query_terms):
                score += 3
            elif len(query_terms) >= 2 and " ".join(query_terms) in haystack:
                score += 4
            if "kra" in q and "kra" in haystack:
                score += 4
            if "tax" in q and ("tax" in haystack or "kra" in haystack):
                score += 3
            if score > 0:
                scored.append((score, tx))
        scored.sort(key=lambda x: (-x[0], str(x[1].get("date", ""))))
        min_score = 1 if any(k in q for k in ("kra", "tax")) else 2
        matched = [x[1] for x in scored[:3] if x[0] >= min_score]
        if matched:
            formatted = [
                f"{tx.get('date')} – {tx.get('description')} in {tx.get('file_name')} "
                f"for {tx.get('amount'):,.2f} KES ({tx.get('transaction_type')})"
                for tx in matched
            ]
            return ("Relevant transactions:\n• " + "\n• ".join(formatted)) if len(formatted) > 1 else formatted[0]
        if any(w in q for w in ("transaction", "find", "show")):
            return "I searched all available transactions but found no clear match. Try a file name, date, category, or description keyword."
        if any(w in q for w in ("system", "how", "work")):
            return ("This system imports PDF and CSV bank statements (including scanned PDFs via OCR), "
                    "extracts transactions using Claude AI, calculates KPIs, and lets you review statements, "
                    "analytics, and transaction details by file.")
        return ("I can answer across uploaded files, KPIs, transactions, prior assistant exchanges, and system behavior. "
                "Try asking about a file name, a transaction description, revenue, expenses, cash flow, balance, or profit.")

    def _build_local_report_summary(self, report_context):
        aggregate = report_context.get("aggregate_metrics", {})
        top_files = report_context.get("top_files", [])
        total_revenue = float(aggregate.get("total_revenue", 0))
        total_expenses = float(aggregate.get("total_expenses", 0))
        net_profit = float(aggregate.get("net_profit", 0))
        profit_margin = float(aggregate.get("profit_margin", 0))
        lines = [
            f"Executive summary: total revenue is {total_revenue:,.2f} KES, total expenses are {total_expenses:,.2f} KES, and net profit is {net_profit:,.2f} KES.",
            f"Profit margin stands at {profit_margin:.2f}%, which indicates {'pressure on profitability' if profit_margin < 0 else 'positive operating performance'}.",
        ]
        if top_files:
            strongest = max(top_files, key=lambda s: float(s.get("closing_balance", 0) or 0))
            lines.append(f"Top balance file: {strongest.get('file_name')} closed at {float(strongest.get('closing_balance', 0)):,.2f} KES.")
        if total_expenses > total_revenue:
            lines.append("Risk area: expenses are exceeding revenue — spending controls and revenue follow-up should be prioritised.")
        else:
            lines.append("Risk area: maintain discipline around the largest expense categories to protect current profitability.")
        lines.append("Recommended actions: review the largest withdrawals, monitor cash runway, and compare period-level revenue against cost growth.")
        return " ".join(lines)
