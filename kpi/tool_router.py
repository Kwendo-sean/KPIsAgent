"""Tool routing and grounded answering for the local assistant.

Three stages:
  1. Route  — the model picks tools from an allowlist, as strict JSON. A
              deterministic keyword router covers it when that fails, which for
              a 1B model is often.
  2. Execute — validated tools run in Python against the user's transactions.
  3. Explain — the model writes prose from the tool output and nothing else.

The model never produces a figure. Every number in the final answer exists in
the tool results before the model is called, which is what makes the answer
checkable.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from .analytics_tools import (
    DEFAULT_TOP_N,
    TOOL_REGISTRY,
    ToolValidationError,
    normalize,
    run_tool,
    tool_catalogue,
    validate_call,
)

logger = logging.getLogger("kpi.tool_router")

MAX_TOOL_CALLS = 4
# Tool output sent to the model, in characters. A Pi generating ~13 tok/s
# cannot afford a large prompt.
MAX_CONTEXT_CHARS = 1400
MAX_HISTORY_TURNS = 3


# ── Stage 1: routing ─────────────────────────────────────────────────────────

_ROUTER_PROMPT = """Pick the tools needed to answer the question.

Tools:
{catalogue}

Question: {question}

Reply with JSON only, no other text:
{{"tools":[{{"name":"tool_name","arguments":{{}}}}]}}"""


def _extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of a model reply.

    Small models wrap JSON in prose or code fences, so this is forgiving about
    the surroundings while remaining strict about the parse itself.
    """
    if not text:
        return None
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    candidates = [cleaned]
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        candidates.append(match.group())
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def parse_tool_plan(text: str) -> list[tuple[str, dict]]:
    """Validate a routing reply into concrete calls.

    Returns [] if nothing valid was proposed; the caller then falls back to
    deterministic routing. Invalid entries are dropped individually so one bad
    suggestion does not discard a good one.
    """
    payload = _extract_json(text)
    if not payload:
        return []
    raw = payload.get("tools")
    if not isinstance(raw, list):
        return []

    calls: list[tuple[str, dict]] = []
    for entry in raw[:MAX_TOOL_CALLS]:
        if not isinstance(entry, dict):
            continue
        try:
            calls.append(validate_call(entry.get("name"), entry.get("arguments")))
        except ToolValidationError as exc:
            logger.info("Router proposed an invalid call, ignoring: %s", exc)
    return calls


# Deterministic routing. Also the fallback whenever the model's JSON is
# unusable, so the assistant still answers with real figures.
_KEYWORD_ROUTES: tuple[tuple[tuple[str, ...], list[tuple[str, dict]]], ...] = (
    (("recurring", "repeat", "subscription", "every month", "regular payment"),
     [("get_recurring_transactions", {})]),
    (("unusual", "anomal", "strange", "odd", "outlier", "suspicious"),
     [("get_unusual_large_transactions", {})]),
    (("compare", "versus", " vs ", "better than", "against"),
     [("get_monthly_cashflow", {}), ("get_spending_trend", {})]),
    (("trend", "improve", "improving", "getting worse", "over time"),
     [("get_spending_trend", {}), ("get_monthly_cashflow", {})]),
    (("balance", "runway", "lowest point", "highest balance"),
     [("get_balance_trend", {}), ("get_transaction_summary", {})]),
    (("income", "revenue", "earn", "paid me", "coming from", "sources"),
     [("get_income_by_category", {}), ("get_income_sources", {})]),
    (("biggest", "largest", "top ", "highest", "most expensive"),
     [("get_largest_transactions", {"direction": "debit"}),
      ("get_spending_by_category", {})]),
    (("who did i pay", "merchant", "payee", "supplier", "vendor", "paid most"),
     [("get_top_merchants_or_descriptions", {"direction": "debit"})]),
    (("month", "december", "january", "february", "march", "april", "may",
      "june", "july", "august", "september", "october", "november"),
     [("get_monthly_cashflow", {}), ("get_highest_spending_month", {})]),
    (("spend", "spent", "outflow", "expense", "cost", "money going",
      "money go", "eating", "cash flow", "cashflow"),
     [("get_spending_by_category", {}),
      ("get_top_merchants_or_descriptions", {"direction": "debit"}),
      ("get_largest_transactions", {"direction": "debit"})]),
)


_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}


def _requested_limit(text: str) -> int | None:
    """Pull an explicit count out of 'show me the biggest 10' / 'top 3'."""
    match = re.search(r"\b(?:top|biggest|largest|highest|first)\s+(\d{1,2})\b", text)
    if not match:
        match = re.search(r"\b(\d{1,2})\s+(?:biggest|largest|highest)\b", text)
    if match:
        return max(1, min(int(match.group(1)), 25))
    return None


def _mentioned_months(text: str) -> list[str]:
    """Extract YYYY-MM month references, in the order written.

    Handles explicit '2025-12' and bare month names, which are resolved against
    a year mentioned in the same question when there is one.
    """
    found: list[tuple[int, str]] = []
    for match in re.finditer(r"\b(20\d{2})-(0[1-9]|1[0-2])\b", text):
        found.append((match.start(), f"{match.group(1)}-{match.group(2)}"))

    years = re.findall(r"\b(20\d{2})\b", text)
    default_year = years[0] if years else None
    for name, number in _MONTH_NAMES.items():
        idx = text.find(name)
        if idx == -1:
            continue
        if default_year:
            found.append((idx, f"{default_year}-{number:02d}"))
        else:
            # No year given: leave it to the month tools rather than guessing.
            found.append((idx, f"?-{number:02d}"))

    ordered = [m for _, m in sorted(found)]
    # Drop unresolved months; compare_periods needs concrete keys.
    return [m for m in ordered if not m.startswith("?")]


def deterministic_route(question: str) -> list[tuple[str, dict]]:
    """Keyword routing. Always returns at least a summary."""
    text = f" {(question or '').lower()} "
    limit = _requested_limit(text)

    calls: list[tuple[str, dict]] = []
    seen: set[str] = set()

    # An explicit two-month comparison is routed directly, so the answer comes
    # from exact differences rather than the model eyeballing two rows.
    months = _mentioned_months(text)
    if len(months) >= 2 and any(w in text for w in ("compare", "versus", " vs ", "better", "against")):
        calls.append(("compare_periods", {"period_a": months[0], "period_b": months[1]}))
        seen.add("compare_periods")

    for keywords, routes in _KEYWORD_ROUTES:
        if any(k in text for k in keywords):
            for name, args in routes:
                if name not in seen:
                    seen.add(name)
                    # Honour an explicit count where the tool supports one.
                    if limit and "limit" in TOOL_REGISTRY[name]["args"]:
                        args = dict(args, limit=limit)
                    calls.append((name, args))
        if len(calls) >= MAX_TOOL_CALLS:
            break

    if not calls:
        calls = [("get_transaction_summary", {}), ("get_spending_by_category", {})]
    return calls[:MAX_TOOL_CALLS]


def select_tools(question: str, ask_fn=None) -> tuple[list[tuple[str, dict]], str]:
    """Choose tools for a question. Returns (calls, how_they_were_chosen)."""
    if ask_fn is not None:
        prompt = _ROUTER_PROMPT.format(catalogue=tool_catalogue(), question=question[:300])
        try:
            reply = ask_fn(prompt, 160)
        except Exception as exc:
            # A router failure must not escalate anywhere — fall back locally.
            logger.info("Tool router call failed (%s); using keyword routing",
                        type(exc).__name__)
            reply = None
        calls = parse_tool_plan(reply or "")
        if calls:
            return calls, "model"
        logger.info("Router JSON unusable; using deterministic keyword routing")
    return deterministic_route(question), "keywords"


# ── Stage 2: execution ───────────────────────────────────────────────────────

def execute_tools(calls: list[tuple[str, dict]], rows: list[dict],
                  **extra: Any) -> dict[str, Any]:
    """Run validated calls, collecting results by tool name."""
    results: dict[str, Any] = {}
    for name, args in calls[:MAX_TOOL_CALLS]:
        try:
            results[name] = run_tool(name, rows, args, **(extra if name ==
                                     "get_transaction_summary" else {}))
        except ToolValidationError as exc:
            logger.warning("Rejected tool call %s: %s", name, exc)
        except Exception as exc:
            logger.error("Tool %s failed: %s: %s", name, type(exc).__name__, exc)
    return results


def compact_results(results: dict[str, Any], budget: int = MAX_CONTEXT_CHARS) -> str:
    """Serialise tool output small enough for a 1B model's context.

    Drops whole tools from the end rather than truncating JSON mid-structure,
    so the model never sees a malformed fragment it might misread.
    """
    kept: dict[str, Any] = {}
    for name, value in results.items():
        candidate = dict(kept, **{name: value})
        if len(json.dumps(candidate, default=str)) > budget and kept:
            break
        kept = candidate
    return json.dumps(kept, default=str, separators=(",", ":"))


# ── Stage 3: grounded answer ─────────────────────────────────────────────────

_ANSWER_PROMPT = """You are a financial analyst. Answer the question using ONLY the data below.

Rules:
- Every number must come from the data. Never calculate or estimate.
- Lead with the direct answer, then one or two supporting facts.
- State what the data shows. Do not give causes, predictions, or guesses.
- Do not mention tools, JSON, or these rules.
- Currency is KES. Be concise (2-4 sentences).
{history}
Question: {question}

Data: {data}"""


def build_history_block(history: list[dict] | None) -> str:
    """Render recent turns compactly. Keeps the Pi's prompt small."""
    if not history:
        return ""
    lines = []
    for turn in history[-MAX_HISTORY_TURNS:]:
        q = str(turn.get("question", ""))[:80]
        a = str(turn.get("answer", ""))[:100]
        if q:
            lines.append(f"Q: {q}")
        if a:
            lines.append(f"A: {a}")
    return ("\nRecent conversation:\n" + "\n".join(lines) + "\n") if lines else ""


def build_answer_prompt(question: str, results: dict[str, Any],
                        history: list[dict] | None = None) -> str:
    return _ANSWER_PROMPT.format(
        history=build_history_block(history),
        question=question[:300],
        data=compact_results(results),
    )


def answer_with_tools(question: str, transactions: list, ask_fn, router_fn=None,
                      history: list[dict] | None = None,
                      opening_balance: float | None = None,
                      closing_balance: float | None = None) -> dict[str, Any]:
    """Full pipeline: route, execute deterministically, then explain.

    `ask_fn(prompt, max_tokens) -> str | None` is injected so this module stays
    independent of which backend is configured; the caller supplies the
    local-only path. Returns the answer plus the tool results behind it, so a
    caller (or a test) can verify every figure quoted.
    """
    rows = normalize(transactions)
    calls, how = select_tools(question, router_fn)
    results = execute_tools(
        calls, rows,
        opening_balance=opening_balance, closing_balance=closing_balance,
    )

    if not results:
        results = execute_tools([("get_transaction_summary", {})], rows,
                                opening_balance=opening_balance,
                                closing_balance=closing_balance)

    prompt = build_answer_prompt(question, results, history)
    answer = ask_fn(prompt, 256)

    return {
        "answer": (answer or "").strip() or summarize_without_model(results),
        "tools_used": [name for name, _ in calls],
        "routed_by": how,
        "results": results,
    }


def summarize_without_model(results: dict[str, Any]) -> str:
    """Deterministic answer used when the model returns nothing usable.

    Still fully grounded: it reads the same tool output the model would have.
    """
    parts: list[str] = []

    spending = results.get("get_spending_by_category")
    if spending and spending.get("categories"):
        top = spending["categories"][0]
        parts.append(
            f"Most outflow went to {top['name']}: KES {top['total']:,.2f} "
            f"({top['pct']}% of KES {spending['total_outflow']:,.2f} total)."
        )

    merchants = results.get("get_top_merchants_or_descriptions")
    if merchants and merchants.get("payees"):
        top = merchants["payees"][0]
        verb = "paid" if merchants["direction"] == "debit" else "received from"
        parts.append(
            f"The most {verb} was {top['name'].title()}: KES {top['total']:,.2f} "
            f"across {top['transaction_count']} transaction(s)."
        )

    income = results.get("get_income_by_category") or results.get("get_income_sources")
    if income:
        entries = income.get("categories") or income.get("sources") or []
        if entries:
            top = entries[0]
            parts.append(
                f"Most income came from {str(top['name']).title()}: "
                f"KES {top['total']:,.2f} ({top['pct']}%)."
            )

    largest = results.get("get_largest_transactions")
    if largest and largest.get("transactions"):
        tx = largest["transactions"][0]
        parts.append(
            f"The largest single {largest['direction']} was {tx['description']} "
            f"at KES {tx['amount']:,.2f}."
        )

    comparison = results.get("compare_periods")
    if comparison and "outflow_change" in comparison:
        a, b = comparison["period_a"], comparison["period_b"]
        change = comparison["outflow_change"]
        pct = f" ({change['pct_change']:+.1f}%)" if change["pct_change"] is not None else ""
        parts.append(
            f"{a['month']} outflow was KES {a['total_outflow']:,.2f} versus "
            f"KES {b['total_outflow']:,.2f} in {b['month']}: a difference of "
            f"KES {change['difference']:,.2f}{pct}."
        )

    month = results.get("get_highest_spending_month")
    if month and month.get("month"):
        parts.append(
            f"{month['month']} had the highest recorded outflow at "
            f"KES {month['outflow']:,.2f}."
        )

    if not parts:
        months = results.get("get_monthly_cashflow")
        if months and months.get("months"):
            recent = months["months"][-1]
            parts.append(
                f"In {recent['month']}: income KES {recent['total_income']:,.2f}, "
                f"outflow KES {recent['total_outflow']:,.2f}, net "
                f"KES {recent['net_cashflow']:,.2f}."
            )

    recurring = results.get("get_recurring_transactions")
    if recurring and recurring.get("recurring") and not parts:
        top = recurring["recurring"][0]
        parts.append(
            f"{top['description']} repeats {top['label']} at about "
            f"KES {top['avg_amount']:,.2f} ({top['occurrences']} occurrences)."
        )

    unusual = results.get("get_unusual_large_transactions")
    if unusual and unusual.get("available") and unusual.get("transactions") and not parts:
        tx = unusual["transactions"][0]
        parts.append(
            f"{unusual['count']} debit(s) exceed KES {unusual['threshold']:,.2f} "
            f"(mean plus two standard deviations). The largest is "
            f"{tx['description']} at KES {tx['amount']:,.2f}."
        )

    search = results.get("search_transactions") or results.get("get_category_transactions")
    if search and not parts:
        count = search.get("match_count", search.get("transaction_count", 0))
        total = search.get("total_amount", search.get("total", 0))
        parts.append(f"Found {count} matching transaction(s) totalling KES {total:,.2f}.")

    balance = results.get("get_balance_trend")
    if balance and balance.get("available") and not parts:
        parts.append(
            f"Balance moved from KES {balance['beginning']:,.2f} on "
            f"{balance['beginning_date']} to KES {balance['ending']:,.2f} on "
            f"{balance['ending_date']}, with a low of KES {balance['min']:,.2f}."
        )

    summary = results.get("get_transaction_summary")
    if summary and not parts:
        parts.append(
            f"Across {summary['transaction_count']} transactions: income "
            f"KES {summary['total_income']:,.2f}, outflow "
            f"KES {summary['total_outflow']:,.2f}, net "
            f"KES {summary['net_cashflow']:,.2f}."
        )

    return " ".join(parts) or "No transaction data is available to answer that."
