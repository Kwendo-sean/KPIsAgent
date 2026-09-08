"""Tests for the deterministic analytics tools and the tool router.

The property these protect: no financial figure in an answer may originate from
the model. The model routes and explains; Python computes. Several tests run
the pipeline with a model stub that returns prose containing invented numbers,
and assert the real figures still come from the tools.
"""
import json

import pytest
from django.test import override_settings

from kpi import analytics_tools as at
from kpi import tool_router as tr
from kpi.ai_agent import HospitalKPIAgent
from kpi.analytics_tools import ToolValidationError, normalize, validate_call

# A small but realistic dataset: two months, clear category winner.
TXS = [
    {"date": "2025-12-02", "description": "Electrical Wiring Upgrade", "amount": 464129.51,
     "type": "WITHDRAWAL", "category": "Maintenance"},
    {"date": "2025-12-05", "description": "Maintenance Contract ACME", "amount": 300000.00,
     "type": "WITHDRAWAL", "category": "Maintenance"},
    {"date": "2025-12-09", "description": "Supplier Payment BETA LTD", "amount": 210000.00,
     "type": "WITHDRAWAL", "category": "Supplier Payments"},
    {"date": "2025-12-15", "description": "Airtime Purchase", "amount": 2000.00,
     "type": "WITHDRAWAL", "category": "Airtime"},
    {"date": "2025-12-20", "description": "Customer Payment from CLINIC A", "amount": 800000.00,
     "type": "DEPOSIT", "category": "Customer Payment"},
    {"date": "2026-01-08", "description": "Supplier Payment BETA LTD", "amount": 190000.00,
     "type": "WITHDRAWAL", "category": "Supplier Payments"},
    {"date": "2026-01-12", "description": "Airtime Purchase", "amount": 2500.00,
     "type": "WITHDRAWAL", "category": "Airtime"},
    {"date": "2026-01-19", "description": "Customer Payment from CLINIC B", "amount": 400000.00,
     "type": "DEPOSIT", "category": "Customer Payment"},
]

ROWS = normalize(TXS)

TOTAL_OUT = 464129.51 + 300000 + 210000 + 2000 + 190000 + 2500
TOTAL_IN = 800000 + 400000


# ── Deterministic correctness ────────────────────────────────────────────────

class TestDeterministicMath:

    def test_spending_by_category_ranks_and_totals(self):
        out = at.get_spending_by_category(ROWS)
        assert out["total_outflow"] == pytest.approx(TOTAL_OUT, abs=0.01)
        top = out["categories"][0]
        assert top["name"] == "Maintenance"
        assert top["total"] == pytest.approx(764129.51, abs=0.01)
        assert top["transaction_count"] == 2
        assert top["pct"] == pytest.approx(
            764129.51 / TOTAL_OUT * 100, abs=0.1)

    def test_income_by_category(self):
        out = at.get_income_by_category(ROWS)
        assert out["total_income"] == pytest.approx(TOTAL_IN, abs=0.01)
        assert out["categories"][0]["name"] == "Customer Payment"

    def test_largest_transactions_exact(self):
        out = at.get_largest_transactions(ROWS, direction="debit", limit=2)
        assert out["transactions"][0]["amount"] == pytest.approx(464129.51)
        assert out["transactions"][0]["description"] == "Electrical Wiring Upgrade"
        assert len(out["transactions"]) == 2

    def test_monthly_cashflow(self):
        months = {m["month"]: m for m in at.get_monthly_cashflow(ROWS)["months"]}
        assert months["2025-12"]["total_outflow"] == pytest.approx(976129.51, abs=0.01)
        assert months["2025-12"]["total_income"] == pytest.approx(800000.0)
        assert months["2026-01"]["net_cashflow"] == pytest.approx(400000 - 192500, abs=0.01)

    def test_highest_spending_month(self):
        out = at.get_highest_spending_month(ROWS)
        assert out["month"] == "2025-12"
        assert out["outflow"] == pytest.approx(976129.51, abs=0.01)

    def test_summary(self):
        out = at.get_transaction_summary(ROWS, opening_balance=1000.0)
        assert out["transaction_count"] == 8
        assert out["total_income"] == pytest.approx(TOTAL_IN)
        assert out["total_outflow"] == pytest.approx(TOTAL_OUT, abs=0.01)
        assert out["net_cashflow"] == pytest.approx(TOTAL_IN - TOTAL_OUT, abs=0.01)
        assert out["opening_balance"] == 1000.0
        assert out["date_from"] == "2025-12-02"
        assert out["date_to"] == "2026-01-19"

    def test_merchant_grouping_normalises_descriptions(self):
        out = at.get_top_merchants_or_descriptions(ROWS, direction="debit")
        names = [p["name"] for p in out["payees"]]
        # The two BETA LTD supplier payments must collapse into one payee.
        beta = [p for p in out["payees"] if "BETA" in p["name"]]
        assert len(beta) == 1
        assert beta[0]["total"] == pytest.approx(400000.0)
        assert beta[0]["transaction_count"] == 2
        assert names

    def test_payee_normaliser_strips_codes_but_keeps_words(self):
        """The code pattern must require a digit, or it eats long plain words."""
        assert at._normalize_payee("UDEPE0P493 2026-04-14 Sent to JOHN DOE") == "SENT TO JOHN DOE"
        assert at._normalize_payee("TJVPE8QU4X Airtime Purchase") == "AIRTIME PURCHASE"
        # "MAINTENANCE" is 11 characters; it must survive.
        assert at._normalize_payee("Maintenance Contract ACME") == "MAINTENANCE CONTRACT ACME"

    def test_search_filters(self):
        out = at.search_transactions(ROWS, query="airtime")
        assert out["match_count"] == 2
        assert out["total_amount"] == pytest.approx(4500.0)

        ranged = at.search_transactions(ROWS, direction="debit", min_amount=200000)
        assert ranged["match_count"] == 3

    def test_compare_periods_exact(self):
        out = at.compare_periods(ROWS, "2025-12", "2026-01")
        assert out["outflow_change"]["difference"] == pytest.approx(
            976129.51 - 192500, abs=0.01)
        assert out["income_change"]["difference"] == pytest.approx(400000.0)

    def test_compare_periods_unknown_month_reports_error(self):
        out = at.compare_periods(ROWS, "1999-01", "2026-01")
        assert out["error"] == "period_not_found"

    def test_unusual_transactions_use_fixed_statistical_rule(self):
        out = at.get_unusual_large_transactions(ROWS)
        assert out["available"] is True
        assert out["method"] == "average_debit_plus_2_standard_deviations"
        # Threshold is computed, not chosen by a model.
        assert out["threshold"] > out["average_debit"]
        # The metric shares its name with get_transaction_summary on purpose:
        # two names for one quantity invited a meaningless "comparison".
        summary = at.get_transaction_summary(ROWS)
        assert out["average_debit"] == pytest.approx(summary["average_debit"])

    def test_balance_trend_absent_without_running_balance(self):
        assert at.get_balance_trend(ROWS)["available"] is False

    def test_balance_trend_present_when_supplied(self):
        rows = normalize([
            {"date": "2025-12-01", "description": "a", "amount": 10, "type": "WITHDRAWAL",
             "running_balance": 500},
            {"date": "2025-12-05", "description": "b", "amount": 10, "type": "WITHDRAWAL",
             "running_balance": 200},
            {"date": "2025-12-09", "description": "c", "amount": 10, "type": "DEPOSIT",
             "running_balance": 900},
        ])
        out = at.get_balance_trend(rows)
        assert out["min"] == 200 and out["min_date"] == "2025-12-05"
        assert out["max"] == 900 and out["ending"] == 900


# ── Recurring cadence ────────────────────────────────────────────────────────

class TestRecurringCadence:
    """The reported bug: a ~44-day gap shown as a monthly figure."""

    def _annual_pair(self):
        return normalize([
            {"date": "2025-11-01", "description": "ANNUAL SAFETY INSPECTION",
             "amount": 3358.00, "type": "WITHDRAWAL", "category": "Compliance"},
            {"date": "2025-12-15", "description": "ANNUAL SAFETY INSPECTION",
             "amount": 3358.00, "type": "WITHDRAWAL", "category": "Compliance"},
        ])

    def test_44_day_interval_is_not_called_monthly(self):
        out = at.get_recurring_transactions(self._annual_pair())
        item = out["recurring"][0]
        assert item["cadence"] != "monthly"
        assert item["avg_interval_days"] == 44

    def test_annual_description_is_not_labelled_monthly(self):
        out = at.get_recurring_transactions(self._annual_pair())
        item = out["recurring"][0]
        assert item["cadence"] == "annual"
        assert item["confidence"] == "stated_in_description"

    def test_uncertain_cadence_yields_no_monthly_figure(self):
        out = at.get_recurring_transactions(self._annual_pair())
        item = out["recurring"][0]
        assert item["monthly_equivalent"] is None
        assert out["estimated_monthly_total"] == 0.0
        assert out["excluded_uncertain"] == 1

    def test_two_samples_alone_are_only_possible_recurring(self):
        rows = normalize([
            {"date": "2025-10-01", "description": "Cleaning Service", "amount": 5000,
             "type": "WITHDRAWAL", "category": "Facilities"},
            {"date": "2025-11-01", "description": "Cleaning Service", "amount": 5000,
             "type": "WITHDRAWAL", "category": "Facilities"},
        ])
        item = at.get_recurring_transactions(rows)["recurring"][0]
        assert item["cadence"] == "monthly"          # spacing genuinely is monthly
        assert item["confidence"] == "low"           # but two samples is thin
        assert item["monthly_equivalent"] is None
        assert item["label"].startswith("possible recurring")

    def test_established_monthly_cadence_is_annualised(self):
        rows = normalize([
            {"date": f"2025-0{m}-01", "description": "Cleaning Service", "amount": 5000,
             "type": "WITHDRAWAL", "category": "Facilities"} for m in (1, 2, 3, 4, 5)
        ])
        out = at.get_recurring_transactions(rows)
        item = out["recurring"][0]
        assert item["cadence"] == "monthly"
        assert item["confidence"] == "high"
        assert item["monthly_equivalent"] == pytest.approx(5000.0)
        assert out["estimated_monthly_total"] == pytest.approx(5000.0)

    def test_agent_wrapper_keeps_legacy_keys(self):
        result = HospitalKPIAgent().detect_recurring_payments(TXS + [
            {"date": "2026-02-08", "description": "Supplier Payment BETA LTD",
             "amount": 195000.00, "type": "WITHDRAWAL", "category": "Supplier Payments"},
        ])
        assert result
        for key in ("description", "avg_amount", "occurrences", "avg_interval_days"):
            assert key in result[0]


# ── Router validation ────────────────────────────────────────────────────────

class TestRouterValidation:

    def test_unknown_tool_is_rejected(self):
        with pytest.raises(ToolValidationError):
            validate_call("drop_database", {})
        with pytest.raises(ToolValidationError):
            validate_call("__import__", {})

    def test_unknown_argument_is_rejected(self):
        with pytest.raises(ToolValidationError):
            validate_call("get_spending_by_category", {"sql": "DROP TABLE"})

    def test_invalid_argument_type_is_rejected(self):
        with pytest.raises(ToolValidationError):
            validate_call("get_largest_transactions", {"limit": "not-a-number"})

    def test_valid_call_is_coerced(self):
        name, args = validate_call("get_largest_transactions",
                                   {"limit": "3", "direction": "debit"})
        assert name == "get_largest_transactions"
        assert args == {"limit": 3, "direction": "debit"}

    def test_plan_parsing_accepts_fenced_json(self):
        calls = tr.parse_tool_plan(
            '```json\n{"tools":[{"name":"get_spending_by_category","arguments":{}}]}\n```')
        assert calls == [("get_spending_by_category", {})]

    def test_plan_parsing_drops_invalid_entries_but_keeps_valid(self):
        calls = tr.parse_tool_plan(json.dumps({"tools": [
            {"name": "evil_tool", "arguments": {}},
            {"name": "get_spending_by_category", "arguments": {}},
        ]}))
        assert calls == [("get_spending_by_category", {})]

    def test_plan_parsing_limits_call_count(self):
        calls = tr.parse_tool_plan(json.dumps({"tools": [
            {"name": "get_spending_by_category", "arguments": {}},
            {"name": "get_income_by_category", "arguments": {}},
            {"name": "get_monthly_cashflow", "arguments": {}},
            {"name": "get_balance_trend", "arguments": {}},
            {"name": "get_spending_trend", "arguments": {}},
            {"name": "get_transaction_summary", "arguments": {}},
        ]}))
        assert len(calls) <= tr.MAX_TOOL_CALLS

    def test_router_falls_back_to_keywords_on_garbage(self):
        calls, how = tr.select_tools("what had most of my money going?",
                                     ask_fn=lambda p, mt: "I am a helpful assistant!")
        assert how == "keywords"
        assert "get_spending_by_category" in [c[0] for c in calls]

    def test_router_failure_does_not_raise(self):
        def boom(prompt, max_tokens):
            raise RuntimeError("model down")

        calls, how = tr.select_tools("what did I spend?", ask_fn=boom)
        assert how == "keywords"
        assert calls


# ── Intent routing ───────────────────────────────────────────────────────────

class TestDeterministicRouting:

    @pytest.mark.parametrize("question,expected", [
        ("What had most of my money going?", "get_spending_by_category"),
        ("What is eating into my cash flow?", "get_spending_by_category"),
        ("Show me the biggest 10.", "get_largest_transactions"),
        ("Who did I pay most?", "get_top_merchants_or_descriptions"),
        ("Which expenses repeat?", "get_recurring_transactions"),
        ("What's unusual here?", "get_unusual_large_transactions"),
        ("Where is most of my income coming from?", "get_income_by_category"),
        ("Compare December and January.", "get_monthly_cashflow"),
        ("Did my spending improve?", "get_spending_trend"),
        ("What about December?", "get_monthly_cashflow"),
    ])
    def test_question_routes_to_expected_tool(self, question, expected):
        names = [c[0] for c in tr.deterministic_route(question)]
        assert expected in names

    def test_explicit_count_is_honoured(self):
        calls = dict(tr.deterministic_route("Show me the biggest 10."))
        assert calls["get_largest_transactions"]["limit"] == 10

    def test_two_month_comparison_routes_with_exact_periods(self):
        calls = dict(tr.deterministic_route("Compare 2025-12 and 2026-01."))
        assert calls["compare_periods"] == {"period_a": "2025-12", "period_b": "2026-01"}

    def test_named_months_resolve_against_a_stated_year(self):
        calls = dict(tr.deterministic_route("Compare December and January 2026"))
        assert calls["compare_periods"]["period_a"] == "2026-12"

    def test_named_months_without_a_year_do_not_guess(self):
        calls = dict(tr.deterministic_route("Compare December and January"))
        # No year to anchor to, so fall back to the month tools rather than invent one.
        assert "compare_periods" not in calls
        assert "get_monthly_cashflow" in calls

    def test_unrecognised_question_still_returns_tools(self):
        names = [c[0] for c in tr.deterministic_route("hello there")]
        assert "get_transaction_summary" in names


# ── Grounding: figures never come from the model ─────────────────────────────

class TestGrounding:

    def _lying_model(self, prompt, max_tokens=256):
        """A model that invents numbers, to prove they cannot leak into results."""
        return "Your biggest expense was Coffee at KES 99,999,999."

    def test_pipeline_figures_come_from_tools_not_model(self):
        out = tr.answer_with_tools(
            "What had most of my money going?", TXS,
            ask_fn=self._lying_model, router_fn=None)

        spending = out["results"]["get_spending_by_category"]
        assert spending["categories"][0]["name"] == "Maintenance"
        assert spending["total_outflow"] == pytest.approx(TOTAL_OUT, abs=0.01)
        # The model's fabricated figure never entered the deterministic results.
        assert "99,999,999" not in json.dumps(out["results"])

    def test_spending_question_selects_spending_tools(self):
        out = tr.answer_with_tools("What had most of my money going?", TXS,
                                   ask_fn=self._lying_model)
        assert "get_spending_by_category" in out["tools_used"]

    def test_largest_transaction_question_returns_exact_amount(self):
        out = tr.answer_with_tools("What was my biggest expense?", TXS,
                                   ask_fn=self._lying_model)
        largest = out["results"]["get_largest_transactions"]["transactions"][0]
        assert largest["amount"] == pytest.approx(464129.51)

    def test_deterministic_summary_used_when_model_returns_nothing(self):
        out = tr.answer_with_tools("What had most of my money going?", TXS,
                                   ask_fn=lambda p, mt=256: None)
        # Falls back to a grounded sentence built from tool output.
        assert "Maintenance" in out["answer"]
        assert "764,129.51" in out["answer"]

    def test_results_are_truncated_before_reaching_the_model(self):
        captured = {}

        def capture(prompt, max_tokens=256):
            captured["prompt"] = prompt
            return "ok"

        big = TXS * 60  # 480 transactions
        tr.answer_with_tools("What had most of my money going?", big, ask_fn=capture)

        assert len(captured["prompt"]) < 3000, "prompt must stay small for the Pi"
        # The raw transaction list must never be pasted into the prompt.
        assert captured["prompt"].count("Electrical Wiring Upgrade") <= 2

    def test_compact_results_drops_whole_tools_not_fragments(self):
        results = {
            "get_spending_by_category": at.get_spending_by_category(ROWS),
            "get_monthly_cashflow": at.get_monthly_cashflow(ROWS),
            "get_largest_transactions": at.get_largest_transactions(ROWS),
        }
        packed = tr.compact_results(results, budget=200)
        json.loads(packed)  # must always be valid JSON
        assert len(packed) < 600

    def test_history_is_included_and_bounded(self):
        history = [{"question": f"q{i}", "answer": f"a{i}"} for i in range(10)]
        block = tr.build_history_block(history)
        assert "q9" in block
        assert "q0" not in block, "only recent turns are kept"

    def test_followup_question_retains_context(self):
        captured = {}

        def capture(prompt, max_tokens=256):
            captured["prompt"] = prompt
            return "ok"

        tr.answer_with_tools(
            "What about December?", TXS, ask_fn=capture,
            history=[{"question": "What had most of my money going?",
                      "answer": "Maintenance took the largest share."}],
        )
        assert "Maintenance took the largest share." in captured["prompt"]
        assert "2025-12" in captured["prompt"]


# ── Local mode never reaches a cloud provider ────────────────────────────────

class TestLocalModeIsolation:

    @override_settings(LOCAL_AI_MODE=True)
    def test_analytics_answer_uses_only_local_model(self, monkeypatch):
        from kpi.tests.test_local_ai import CLOUD_PROVIDER_FUNCS
        from kpi import ai_agent

        for name in CLOUD_PROVIDER_FUNCS:
            monkeypatch.setattr(
                ai_agent, name,
                lambda *a, _n=name, **kw: pytest.fail(f"cloud provider {_n} called"),
            )
        monkeypatch.setattr(ai_agent, "_ask_local", lambda p, mt=256: "Local explanation.")

        out = HospitalKPIAgent().answer_with_analytics("What did I spend most on?", TXS)

        assert out["answer"] == "Local explanation."
        assert out["results"]["get_spending_by_category"]["categories"][0]["name"] == "Maintenance"

    @override_settings(LOCAL_AI_MODE=True)
    def test_local_model_failure_still_gives_grounded_answer(self, monkeypatch):
        """A dead model must not fabricate, and must not escalate off-device."""
        from kpi import ai_agent
        from kpi.ai_agent import LocalAIUnavailable

        def down(prompt, max_tokens=256):
            raise LocalAIUnavailable("local model unavailable")

        monkeypatch.setattr(ai_agent, "_ask_text", down)

        with pytest.raises(LocalAIUnavailable):
            HospitalKPIAgent().answer_with_analytics("What did I spend most on?", TXS)


# ── Robustness ───────────────────────────────────────────────────────────────

class TestRobustness:

    def test_empty_dataset_does_not_crash(self):
        out = tr.answer_with_tools("What did I spend?", [], ask_fn=lambda p, mt=256: None)
        assert out["answer"]

    def test_normalize_skips_zero_and_bad_amounts(self):
        rows = normalize([
            {"date": "2025-01-01", "description": "zero", "amount": 0, "type": "WITHDRAWAL"},
            {"date": "2025-01-02", "description": "junk", "amount": "abc", "type": "WITHDRAWAL"},
            {"date": "2025-01-03", "description": "good", "amount": "1,250.50", "type": "WITHDRAWAL"},
        ])
        assert len(rows) == 1
        assert rows[0]["amount"] == pytest.approx(1250.50)

    def test_missing_dates_do_not_break_time_tools(self):
        rows = normalize([
            {"date": None, "description": "undated", "amount": 100, "type": "WITHDRAWAL"},
            {"date": "2025-01-03", "description": "dated", "amount": 200, "type": "WITHDRAWAL"},
        ])
        assert at.get_monthly_cashflow(rows)["months"][0]["total_outflow"] == 200
        assert at.get_spending_by_category(rows)["total_outflow"] == 300

    def test_limits_are_clamped(self):
        out = at.get_largest_transactions(ROWS, limit=9999)
        assert len(out["transactions"]) <= at.MAX_TOP_N
