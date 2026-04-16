import json
import re

from django.conf import settings

try:
    import anthropic as _anthropic_sdk
except ImportError:
    _anthropic_sdk = None

from .pdf_extractor import BankStatementPDFExtractor
from .pii_redactor import redact as _redact_pii

# Model used for all text analysis
_CLAUDE_MODEL = "claude-haiku-4-5-20251001"


def _claude_client():
    """Return an Anthropic client, reading the key from Django settings then os.environ."""
    if not _anthropic_sdk:
        print("[AI_AGENT] anthropic SDK not installed")
        return None

    import os
    from pathlib import Path

    # Re-run load_dotenv here so the key is available even if Django's settings
    # module loaded before the .env file was parsed (common in some run configs).
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
        print("[AI_AGENT] ANTHROPIC_API_KEY is not set — AI analysis disabled")
        return None
    return _anthropic_sdk.Anthropic(api_key=api_key)


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences that Claude sometimes wraps around HTML/JSON responses."""
    import re as _re
    # Remove ```html ... ``` or ``` ... ``` wrappers
    stripped = _re.sub(r"^```[a-zA-Z]*\s*\n?", "", text.strip())
    stripped = _re.sub(r"\n?```\s*$", "", stripped.strip())
    return stripped.strip()


def _extract_html_body(text: str) -> str:
    """
    If Claude returns a full HTML document, extract just the <body> content.
    Otherwise return as-is (already a fragment).
    """
    import re as _re
    body_match = _re.search(r"<body[^>]*>(.*?)</body>", text, _re.DOTALL | _re.IGNORECASE)
    if body_match:
        return body_match.group(1).strip()
    # Strip any stray <!DOCTYPE...> or <html>/<head> wrappers without a </body>
    cleaned = _re.sub(r"<!DOCTYPE[^>]*>", "", text, flags=_re.IGNORECASE)
    cleaned = _re.sub(r"<html[^>]*>|</html>", "", cleaned, flags=_re.IGNORECASE)
    cleaned = _re.sub(r"<head[^>]*>.*?</head>", "", cleaned, flags=_re.DOTALL | _re.IGNORECASE)
    cleaned = _re.sub(r"<body[^>]*>|</body>", "", cleaned, flags=_re.IGNORECASE)
    return cleaned.strip()


def _ask(client, prompt: str, max_tokens: int = 1024) -> str | None:
    """Send a single-turn prompt to Claude and return the text, or None on failure."""
    if client is None:
        return None
    try:
        response = client.messages.create(
            model=_CLAUDE_MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text if response.content else None
    except Exception as e:
        print(f"[AI_AGENT] _ask failed: {type(e).__name__}: {e}")
        return None


class HospitalKPIAgent:
    """AI and fallback logic for analysing hospital financial data."""

    CURRENCY_UNIT = "KES"
    DAILY_CURRENCY_UNIT = "KES/Day"

    def __init__(self):
        self._client = None  # lazily initialised on first use

    @property
    def client(self):
        """Return a live Anthropic client, creating one if needed."""
        if self._client is None:
            self._client = _claude_client()
        return self._client

    # ──────────────────────────────────────────────
    # OCR  (scanned PDF pages → text via Claude Vision)
    # ──────────────────────────────────────────────

    def ocr_pdf_pages(self, page_images_b64: list[str]) -> str | None:
        """
        Send up to 5 base-64 encoded PNG images of PDF pages to Claude Vision
        and get back the extracted text.
        page_images_b64: list of base64-encoded PNG strings (one per page).
        Returns the extracted text, or None if OCR is unavailable.
        """
        if self.client is None or not page_images_b64:
            return None
        content = []
        for img_b64 in page_images_b64[:5]:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": img_b64,
                },
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
            return response.content[0].text if response.content else None
        except Exception:
            return None

    # ──────────────────────────────────────────────
    # Extraction
    # ──────────────────────────────────────────────

    def extract_csv_structure_with_ai(self, csv_head_text: str) -> dict | None:
        # Redact PII from the sample rows before sending to Claude
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

        # Header-name fallback
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
        # Redact PII before sending to Claude — account numbers, phone numbers,
        # card numbers, names, and addresses are stripped. Dates and amounts are kept.
        safe_text = _redact_pii(bank_statement_text)
        print(f"[AI_AGENT] extract_financial_data called. Original text length={len(bank_statement_text)}, redacted length={len(safe_text)}")
        print(f"[AI_AGENT] First 300 chars of redacted text: {repr(safe_text[:300])}")

        prompt = f"""You are a financial data extraction expert. Extract ALL data from this bank statement text.

CRITICAL RULES:
- Return ONLY raw JSON — no markdown, no code fences, no explanatory text.
- All monetary amounts must be plain numbers (no commas, no currency symbols). E.g. 12345.67 not "12,345.67" or "KES 12,345".
- statement_period_start = EARLIEST date found; statement_period_end = LATEST date. Even if the statement lists newest first.
- transaction "type" must be exactly "DEPOSIT" or "WITHDRAWAL" — nothing else.
- Extract EVERY transaction line you can see — do not summarise or skip rows.
- If you cannot determine a field, use 0 for numbers and null for dates.

Bank Statement Text:
{safe_text[:12000]}

Respond ONLY with valid JSON:
{{
    "statement_period_start": "YYYY-MM-DD",
    "statement_period_end": "YYYY-MM-DD",
    "opening_balance": 0.00,
    "closing_balance": 0.00,
    "total_deposits": 0.00,
    "total_withdrawals": 0.00,
    "transactions": [
        {{"date": "YYYY-MM-DD", "description": "...", "amount": 0.00, "type": "DEPOSIT|WITHDRAWAL|TRANSFER"}}
    ]
}}"""
        text = _ask(self.client, prompt, max_tokens=4096)
        print(f"[AI_AGENT] extract_financial_data raw response (first 500 chars): {repr(text[:500]) if text else 'None'}")
        if text:
            clean = _strip_code_fences(text)
            match = re.search(r"\{.*\}", clean, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group())
                    tx_count = len(parsed.get("transactions", []))
                    print(f"[AI_AGENT] Parsed JSON OK — {tx_count} transactions, deposits={parsed.get('total_deposits')}, withdrawals={parsed.get('total_withdrawals')}")
                    if parsed:
                        return parsed
                except (json.JSONDecodeError, ValueError) as e:
                    print(f"[AI_AGENT] JSON parse error: {e}")
            else:
                print(f"[AI_AGENT] No JSON object found in response")
        print(f"[AI_AGENT] Falling back to regex extractor. Text length={len(bank_statement_text)}")
        return BankStatementPDFExtractor.build_fallback_financial_data_from_text(bank_statement_text)

    # ──────────────────────────────────────────────
    # KPI Calculation  (pure Python — no AI needed)
    # ──────────────────────────────────────────────

    @staticmethod
    def _amt(t) -> float:
        """Safely parse an amount field that may be a string like '1,234.56'."""
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

        total_deposits    = sum(self._amt(t) for t in transactions if self._tx_type(t) in ("DEPOSIT",  "CREDIT", "CR"))
        total_withdrawals = sum(self._amt(t) for t in transactions if self._tx_type(t) in ("WITHDRAWAL","DEBIT",  "DR"))
        deposit_amounts    = [self._amt(t) for t in transactions if self._tx_type(t) in ("DEPOSIT",  "CREDIT", "CR")]
        withdrawal_amounts = [self._amt(t) for t in transactions if self._tx_type(t) in ("WITHDRAWAL","DEBIT",  "DR")]

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

        kpis["Total_Revenue"] = {"value": total_deposits, "unit": self.CURRENCY_UNIT, "type": "REVENUE", "description": "Total deposits/income in the period"}
        kpis["Average_Daily_Revenue"] = {"value": total_deposits / days, "unit": self.DAILY_CURRENCY_UNIT, "type": "REVENUE", "description": f"Average daily revenue over {days} days"}
        kpis["Average_Deposit_Size"] = {"value": avg_deposit_size, "unit": self.CURRENCY_UNIT, "type": "REVENUE", "description": "Average size of incoming deposits"}
        kpis["Deposit_Frequency"] = {"value": deposit_frequency, "unit": "Deposits", "type": "REVENUE", "description": "Number of incoming deposit transactions"}
        kpis["Total_Expenses"] = {"value": total_withdrawals, "unit": self.CURRENCY_UNIT, "type": "COST", "description": "Total expenses in the period"}
        kpis["Average_Daily_Expense"] = {"value": total_withdrawals / days, "unit": self.DAILY_CURRENCY_UNIT, "type": "COST", "description": f"Average daily expenses over {days} days"}
        kpis["Average_Expense_Size"] = {"value": avg_withdrawal_size, "unit": self.CURRENCY_UNIT, "type": "COST", "description": "Average size of outgoing expense transactions"}
        kpis["Expense_Frequency"] = {"value": expense_frequency, "unit": "Expenses", "type": "COST", "description": "Number of outgoing expense transactions"}
        kpis["Expense_to_Revenue_Ratio"] = {"value": expense_ratio, "unit": "%", "type": "COST", "description": "Expenses as percentage of revenue"}
        kpis["Net_Income"] = {"value": net_income, "unit": self.CURRENCY_UNIT, "type": "PROFITABILITY", "description": "Revenue minus expenses"}
        kpis["Profit_Margin"] = {"value": profit_margin, "unit": "%", "type": "PROFITABILITY", "description": "Net profit as percentage of revenue"}
        kpis["Largest_Deposit"] = {"value": largest_deposit, "unit": self.CURRENCY_UNIT, "type": "PROFITABILITY", "description": "Largest single incoming transaction"}
        kpis["Largest_Expense"] = {"value": largest_expense, "unit": self.CURRENCY_UNIT, "type": "PROFITABILITY", "description": "Largest single outgoing transaction"}
        kpis["Net_Cash_Flow"] = {"value": closing_balance - opening_balance, "unit": self.CURRENCY_UNIT, "type": "CASHFLOW", "description": "Change in cash balance over the period"}
        kpis["Closing_Balance"] = {"value": closing_balance, "unit": self.CURRENCY_UNIT, "type": "CASHFLOW", "description": "Final cash balance"}
        kpis["Cash_Conversion_Ratio"] = {"value": profit_margin, "unit": "%", "type": "CASHFLOW", "description": "Percentage of revenue converted to net cash"}
        kpis["Cash_Runway_Months"] = {"value": runway_months, "unit": "Months", "type": "CASHFLOW", "description": "Approximate cash runway at current expense levels"}
        kpis["Transaction_Count"] = {"value": transaction_count, "unit": "Count", "type": "EFFICIENCY", "description": "Total number of transactions"}
        kpis["Average_Transaction_Size"] = {"value": avg_transaction_size, "unit": self.CURRENCY_UNIT, "type": "EFFICIENCY", "description": "Average size of all transactions"}
        kpis["Operating_Margin"] = {"value": profit_margin, "unit": "%", "type": "FINANCIAL_HEALTH", "description": "Operating profit margin"}
        kpis["Liquidity_Days"] = {"value": min(liquidity_ratio, 999), "unit": "Days", "type": "FINANCIAL_HEALTH", "description": "Days of expenses covered by current balance"}
        kpis["Balance_to_Expense_Ratio"] = {"value": balance_to_expense_ratio, "unit": "%", "type": "FINANCIAL_HEALTH", "description": "Closing balance as a percentage of total expenses"}

        return kpis

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

        if expense_ratio > 80:
            alerts.append({"severity": "CRITICAL", "title": "High Expense Ratio",
                           "content": f"Expenses are {expense_ratio:.1f}% of revenue. Cost reduction is urgent."})
        if profit_margin < 0:
            alerts.append({"severity": "CRITICAL", "title": "Negative Profit Margin",
                           "content": f"Operating at a loss with {profit_margin:.1f}% margin. Immediate action required."})
        elif profit_margin < 5:
            alerts.append({"severity": "WARNING", "title": "Low Profit Margin",
                           "content": f"Profit margin of {profit_margin:.1f}% is below the recommended 10%."})
        if liquidity_days < 30:
            alerts.append({"severity": "WARNING", "title": "Low Liquidity",
                           "content": f"Cash covers only {liquidity_days:.0f} days of expenses."})
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
        if "profit" in q and "Net Income" in kpis and "Profit Margin" in kpis:
            ni = kpis["Net Income"]
            pm = kpis["Profit Margin"]
            return f"Net Income is {ni['value']:,.2f} {ni['unit']}, and Profit Margin is {pm['value']:.2f}{pm['unit']}."
        mapping = {
            "profit margin": "Profit Margin", "net income": "Net Income",
            "profit": "Net Income", "income": "Net Income",
            "revenue": "Total Revenue", "deposit": "Total Revenue",
            "expenses": "Total Expenses", "spend": "Total Expenses", "expense": "Total Expenses",
            "cash flow": "Net Cash Flow", "cashflow": "Net Cash Flow",
            "closing balance": "Closing Balance", "balance": "Closing Balance",
            "liquidity": "Liquidity Days", "runway": "Cash Runway Months",
            "largest expense": "Largest Expense", "largest deposit": "Largest Deposit",
        }
        for phrase, metric_name in mapping.items():
            if phrase in q and metric_name in kpis:
                metric = kpis[metric_name]
                return f"{metric_name} is {metric['value']:,.2f} {metric['unit']}."
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
