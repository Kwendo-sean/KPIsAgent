"""Tests for referential follow-up routing.

The bug these cover: the assistant identified an anomaly, the user asked "tell
me the specific transactions where they happen", and the reply was a generic
transaction summary. History reached the explanation stage but not tool
selection, so the follow-up lost its subject.
"""
import json

import pytest

from kpi import analytics_tools as at
from kpi import tool_router as tr

# Deterministic fixture: three obvious outliers against small routine debits.
TXS = [
    {"date": "2020-08-14", "description": "Equipment Supplier", "amount": 84500.00,
     "type": "WITHDRAWAL", "category": "Maintenance"},
    {"date": "2020-10-02", "description": "Electrical Upgrade", "amount": 76200.00,
     "type": "WITHDRAWAL", "category": "Maintenance"},
    {"date": "2020-11-11", "description": "Generator Overhaul", "amount": 69000.00,
     "type": "WITHDRAWAL", "category": "Maintenance"},
]
# Routine small debits so the outlier threshold sits well below the big three.
TXS += [
    {"date": f"2020-09-{d:02d}", "description": f"Airtime Purchase {d}", "amount": 500.00,
     "type": "WITHDRAWAL", "category": "Airtime"}
    for d in range(1, 26)
]
TXS += [
    {"date": "2020-09-30", "description": "Customer Payment CLINIC A", "amount": 500000.00,
     "type": "DEPOSIT", "category": "Customer Payment"},
]

ANOMALY_HISTORY = [{
    "question": "What's unusual here?",
    "answer": ("There are discrepancies in the data. Several debit transactions are "
               "unusually high compared with the rest."),
}]


def _model(text):
    return lambda prompt, max_tokens=256: text


# ── Routing ──────────────────────────────────────────────────────────────────

class TestFollowupRouting:

    @pytest.mark.parametrize("question", [
        "Tell me the specific transactions where they happen",
        "Which transactions?",
        "which ones?",
        "show me those",
        "show me them",
        "What transactions?",
        "where did they happen?",
        "Which transactions caused that?",
        "give me the exact transactions",
    ])
    def test_anomaly_followup_routes_to_unusual_transactions(self, question):
        calls = tr.resolve_followup_route(question, ANOMALY_HISTORY)
        assert calls is not None, f"{question!r} was not recognised as a follow-up"
        names = [c[0] for c in calls]
        assert "get_unusual_large_transactions" in names

    def test_specific_transactions_followup_does_not_use_summary(self):
        names = [c[0] for c in tr.deterministic_route(
            "Tell me the specific transactions where they happen", ANOMALY_HISTORY)]
        assert "get_transaction_summary" not in names
        assert "get_unusual_large_transactions" in names

    def test_followup_history_is_used_during_routing(self):
        """The same question routes differently with and without history."""
        without = [c[0] for c in tr.deterministic_route("which ones?", None)]
        with_history = [c[0] for c in tr.deterministic_route("which ones?", ANOMALY_HISTORY)]

        assert "get_unusual_large_transactions" not in without
        assert "get_unusual_large_transactions" in with_history

    def test_select_tools_reports_followup_routing(self):
        calls, how = tr.select_tools(
            "which ones?",
            ask_fn=_model('{"tools":[{"name":"get_transaction_summary","arguments":{}}]}'),
            history=ANOMALY_HISTORY,
        )
        # A referential follow-up is resolved in Python, not handed to the model.
        assert how == "followup"
        assert "get_unusual_large_transactions" in [c[0] for c in calls]

    def test_standalone_question_is_not_treated_as_followup(self):
        assert tr.resolve_followup_route(
            "What had most of my money going?", ANOMALY_HISTORY) is None

    def test_followup_without_history_returns_none(self):
        assert tr.resolve_followup_route("which ones?", []) is None

    def test_only_the_latest_turn_sets_the_topic(self):
        history = [
            {"question": "What's unusual here?", "answer": "Some outliers exist."},
            {"question": "Which expenses repeat?", "answer": "Cleaning Service repeats monthly."},
        ]
        names = [c[0] for c in tr.resolve_followup_route("show me those", history)]
        assert "get_recurring_transactions" in names
        assert "get_unusual_large_transactions" not in names

    def test_max_tool_calls_respected(self):
        calls = tr.deterministic_route("show me those", ANOMALY_HISTORY)
        assert len(calls) <= tr.MAX_TOOL_CALLS


# ── Chains B, C, D ───────────────────────────────────────────────────────────

class TestFollowupChains:

    def test_spending_followup_targets_the_dominant_category(self):
        history = [{
            "question": "What had most of my money going?",
            "answer": "Most outflow went to Maintenance: KES 229,700.00 (94.8% of total).",
        }]
        calls = dict(tr.resolve_followup_route("Show me the transactions", history))
        assert calls["get_category_transactions"]["category"] == "Maintenance"

    def test_spending_followup_uses_recorded_category_when_present(self):
        history = [{"question": "What had most of my money going?",
                    "answer": "Most outflow went there.", "top_category": "Supplier Payments"}]
        calls = dict(tr.resolve_followup_route("show me those", history))
        assert calls["get_category_transactions"]["category"] == "Supplier Payments"

    def test_top_10_followup_retains_previous_largest_transaction_context(self):
        history = [{"question": "What was my biggest expense?",
                    "answer": "The largest single debit was Equipment Supplier at KES 84,500.00."}]
        calls = dict(tr.resolve_followup_route("Show me the top 10", history))
        assert calls["get_largest_transactions"] == {"direction": "debit", "limit": 10}

    def test_recurring_followup_returns_actual_payments(self):
        history = [{"question": "Which expenses repeat?",
                    "answer": "Airtime Purchase repeats monthly."}]
        names = [c[0] for c in tr.resolve_followup_route("Show me the actual payments", history)]
        assert "get_recurring_transactions" in names

    def test_income_followup_uses_credit_direction(self):
        history = [{"question": "Where is most of my income coming from?",
                    "answer": "Most income came from Customer Payment."}]
        calls = dict(tr.resolve_followup_route("show me those", history))
        assert calls["get_largest_transactions"]["direction"] == "credit"


# ── Exact rows ───────────────────────────────────────────────────────────────

class TestExactTransactionOutput:

    def test_anomaly_rows_include_date_description_amount_category(self):
        out = at.get_unusual_large_transactions(at.normalize(TXS), limit=10)
        assert out["available"] is True
        assert out["transactions"]
        for row in out["transactions"]:
            assert row["date"]
            assert row["description"]
            assert isinstance(row["amount"], float)
            assert row["direction"] == "debit"
            assert row["category"]
            assert row["reason"] == "amount_above_outlier_threshold"

    def test_exact_transaction_values_come_from_tools(self):
        out = tr.answer_with_tools(
            "Tell me the specific transactions where they happen", TXS,
            ask_fn=_model("There are several irregular transactions."),
            history=ANOMALY_HISTORY,
        )
        flagged = out["results"]["get_unusual_large_transactions"]["transactions"]
        amounts = {r["amount"] for r in flagged}
        assert 84500.00 in amounts
        # Every figure in the answer must exist in the tool output.
        for row in flagged:
            assert f"{row['amount']:,.2f}" in out["answer"]
            assert row["date"] in out["answer"]

    def test_model_cannot_replace_requested_transaction_list_with_generic_prose(self):
        out = tr.answer_with_tools(
            "Which transactions?", TXS,
            ask_fn=_model("There are several irregular transactions requiring review."),
            history=ANOMALY_HISTORY,
        )
        # The vague sentence must not stand in for the rows.
        assert "1." in out["answer"]
        assert "Equipment Supplier" in out["answer"]
        assert "84,500.00" in out["answer"]

    def test_model_invented_figures_never_survive(self):
        out = tr.answer_with_tools(
            "Which transactions?", TXS,
            ask_fn=_model("The unusual ones total KES 99,999,999.00 across 42 transactions."),
            history=ANOMALY_HISTORY,
        )
        assert "99,999,999" not in out["answer"]
        assert "42 transactions" not in out["answer"]

    def test_deterministic_list_used_when_model_is_silent(self):
        out = tr.answer_with_tools(
            "Which transactions?", TXS,
            ask_fn=lambda p, mt=256: None, history=ANOMALY_HISTORY)
        assert "Equipment Supplier" in out["answer"]
        assert "84,500.00" in out["answer"]

    def test_format_transaction_list_returns_none_without_rows(self):
        assert tr.format_transaction_list({"get_transaction_summary": {"total_income": 1}}) is None

    def test_listing_states_the_rule_that_flagged_the_rows(self):
        results = {"get_unusual_large_transactions":
                   at.get_unusual_large_transactions(at.normalize(TXS), limit=10)}
        listing = tr.format_transaction_list(results)
        assert "outlier threshold" in listing
        assert "standard deviations" in listing


# ── Language safety ──────────────────────────────────────────────────────────

class TestLanguageSafety:

    def test_no_fraud_claim_is_generated(self):
        out = tr.answer_with_tools(
            "Which transactions?", TXS,
            ask_fn=lambda p, mt=256: None, history=ANOMALY_HISTORY)
        lowered = out["answer"].lower()
        for word in ("fraud", "fraudulent", "suspicious", "criminal", "theft",
                     "requires further investigation", "potential irregularities"):
            assert word not in lowered

    def test_prompt_forbids_fraud_and_vague_claims(self):
        prompt = tr.build_answer_prompt("Which transactions?", {}, ANOMALY_HISTORY)
        assert "Never suggest fraud" in prompt
        assert "requires investigation" in prompt

    def test_outlier_wording_names_the_metric_explicitly(self):
        results = {"get_unusual_large_transactions":
                   at.get_unusual_large_transactions(at.normalize(TXS), limit=10)}
        listing = tr.format_transaction_list(results)
        # "average debit" is named, not an unqualified "mean".
        assert "average debit" in listing
        assert "mean debit" not in listing

    def test_summary_and_anomaly_tools_agree_on_metric_name(self):
        rows = at.normalize(TXS)
        assert (at.get_unusual_large_transactions(rows)["average_debit"]
                == pytest.approx(at.get_transaction_summary(rows)["average_debit"]))


    def test_vague_model_lead_is_stripped(self):
        """A "requires further review" opener must not survive onto the answer."""
        out = tr.answer_with_tools(
            "Which transactions?", TXS,
            ask_fn=_model("There are several irregular transactions that require "
                          "further review."),
            history=ANOMALY_HISTORY,
        )
        assert "further review" not in out["answer"].lower()
        assert "Equipment Supplier" in out["answer"]

    def test_neutral_model_lead_is_kept(self):
        out = tr.answer_with_tools(
            "Which transactions?", TXS,
            ask_fn=_model("Here are the transactions above the threshold."),
            history=ANOMALY_HISTORY,
        )
        assert out["answer"].startswith("Here are the transactions")
        assert "Equipment Supplier" in out["answer"]


# ── Wider database access ────────────────────────────────────────────────────

class TestDatabaseContextTools:
    """The assistant can reach statements and stored KPIs, not just rows."""

    STATEMENTS = [
        {"file_name": "Jan.pdf", "period_start": "2020-01-01", "period_end": "2020-01-31",
         "deposits": 500000.0, "withdrawals": 230000.0, "closing_balance": 270000.0},
        {"file_name": "Feb.pdf", "period_start": "2020-02-01", "period_end": "2020-02-29",
         "deposits": 400000.0, "withdrawals": 210000.0, "closing_balance": 460000.0},
    ]
    KPIS = [
        {"name": "Profit_Margin", "value": 42.5, "unit": "%", "status": "GOOD",
         "type": "PROFITABILITY"},
        {"name": "Liquidity_Days", "value": 61.0, "unit": "Days", "status": "GOOD",
         "type": "CASHFLOW"},
    ]

    def test_statements_tool_reads_supplied_records(self):
        out = at.get_statements(at.normalize(TXS), statements=self.STATEMENTS)
        assert out["statement_count"] == 2
        assert out["statements"][0]["file_name"] == "Jan.pdf"

    def test_kpi_tool_reads_stored_metrics(self):
        out = at.get_kpi_metrics(at.normalize(TXS), kpis=self.KPIS)
        assert out["metric_count"] == 2
        assert out["metrics"][0]["name"] == "Profit_Margin"

    def test_context_flows_through_the_pipeline(self):
        out = tr.answer_with_tools(
            "How many statements do I have?", TXS,
            ask_fn=lambda p, mt=256: None,
            statements=self.STATEMENTS, kpis=self.KPIS,
        )
        assert out["results"]["get_statements"]["statement_count"] == 2

    def test_kpi_question_routes_to_kpi_tool(self):
        out = tr.answer_with_tools(
            "What is my profit margin?", TXS, ask_fn=lambda p, mt=256: None,
            statements=self.STATEMENTS, kpis=self.KPIS)
        assert out["results"]["get_kpi_metrics"]["metrics"][0]["value"] == 42.5

    def test_categories_tool_lists_both_directions(self):
        out = at.list_categories(at.normalize(TXS))
        assert "Maintenance" in out["expense_categories"]
        assert "Customer Payment" in out["income_categories"]

    def test_model_cannot_reach_context_it_was_not_given(self):
        """Context is caller-supplied; a model-named argument cannot inject it."""
        with pytest.raises(at.ToolValidationError):
            at.validate_call("get_statements", {"statements": [{"file_name": "evil"}]})


# ── Pi constraints preserved ─────────────────────────────────────────────────

class TestConstraints:

    def test_followup_prompt_stays_small(self):
        captured = {}

        def capture(prompt, max_tokens=256):
            captured["prompt"] = prompt
            return "ok"

        tr.answer_with_tools("Which transactions?", TXS * 20, ask_fn=capture,
                             history=ANOMALY_HISTORY)
        assert len(captured["prompt"]) < 3000

    def test_followup_results_remain_valid_json(self):
        out = tr.answer_with_tools("Which transactions?", TXS,
                                   ask_fn=lambda p, mt=256: None, history=ANOMALY_HISTORY)
        json.loads(json.dumps(out["results"], default=str))
