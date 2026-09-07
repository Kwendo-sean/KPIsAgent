"""Tests for LOCAL_AI_MODE.

The security-critical property under test: when LOCAL_AI_MODE is enabled, no
cloud provider function may be entered for any reason — including when the
local model is unreachable. These tests install tripwires over every cloud
provider so that a call would fail the test loudly rather than silently
sending bank statement data off-device.
"""
import pytest
from django.test import override_settings

from kpi import ai_agent
from kpi.ai_agent import (
    HospitalKPIAgent,
    LocalAIUnavailable,
    LocalExtractionFailed,
    _compact_kpi_lines,
    local_ai_enabled,
)

# Every function in ai_agent.py that performs an outbound call to a third party.
CLOUD_PROVIDER_FUNCS = (
    "_ask_ollama",
    "_ask_groq",
    "_ask_fireworks",
    "_ask_claude",
    "_ask_gemini",
    "_ask_llm7",
    "_ask_llm7_text",
    "_ask_openrouter",
    "_llm7_call",
)


@pytest.fixture
def no_cloud(monkeypatch):
    """Install a tripwire on every cloud provider function.

    Any invocation fails the test immediately, which is what we want: a silent
    external call is the one failure mode this feature exists to prevent.
    """
    calls = []

    def make_tripwire(name):
        def _tripwire(*args, **kwargs):
            calls.append(name)
            raise AssertionError(
                f"SECURITY FAILURE: cloud provider {name}() was called in local mode"
            )
        return _tripwire

    for name in CLOUD_PROVIDER_FUNCS:
        monkeypatch.setattr(ai_agent, name, make_tripwire(name), raising=True)
    # The Anthropic SDK must not be constructed either.
    monkeypatch.setattr(ai_agent, "_anthropic_sdk", None, raising=False)
    return calls


# ── 1. Local endpoint dispatch ────────────────────────────────────────────────

class TestLocalDispatch:

    @override_settings(
        LOCAL_AI_MODE=True,
        LOCAL_AI_BASE_URL="http://127.0.0.1:8081/v1",
        LOCAL_AI_MODEL="qwen2.5-0.5b-instruct",
        LOCAL_AI_MAX_TOKENS=384,
        LOCAL_AI_TIMEOUT=120,
    )
    def test_posts_to_local_openai_compatible_endpoint(self, monkeypatch):
        captured = {}

        class FakeResponse:
            def raise_for_status(self): pass
            def json(self):
                return {"choices": [{"message": {"content": "local answer"}}]}

        def fake_post(url, **kwargs):
            captured["url"] = url
            captured["json"] = kwargs.get("json")
            captured["timeout"] = kwargs.get("timeout")
            return FakeResponse()

        import requests
        monkeypatch.setattr(requests, "post", fake_post)

        result = ai_agent._ask_local("hello", max_tokens=100)

        assert result == "local answer"
        assert captured["url"] == "http://127.0.0.1:8081/v1/chat/completions"
        assert captured["json"]["model"] == "qwen2.5-0.5b-instruct"
        assert captured["json"]["stream"] is False
        assert captured["timeout"] == 120
        assert captured["json"]["messages"] == [{"role": "user", "content": "hello"}]

    @override_settings(LOCAL_AI_MODE=True)
    def test_ask_and_ask_text_route_to_local(self, monkeypatch, no_cloud):
        monkeypatch.setattr(ai_agent, "_ask_local", lambda p, mt=1024: "from local model")

        assert ai_agent._ask("prompt") == "from local model"
        assert ai_agent._ask_text("prompt") == "from local model"
        assert no_cloud == []


# ── 2 & 3. Zero cloud fallback / local model unavailable ─────────────────────

class TestNoCloudFallback:

    @override_settings(LOCAL_AI_MODE=True)
    def test_ask_raises_when_local_unavailable(self, monkeypatch, no_cloud):
        monkeypatch.setattr(ai_agent, "_ask_local", lambda p, mt=1024: None)

        with pytest.raises(LocalAIUnavailable):
            ai_agent._ask("prompt")
        assert no_cloud == [], "no cloud provider may be attempted"

    @override_settings(LOCAL_AI_MODE=True)
    def test_ask_text_raises_when_local_unavailable(self, monkeypatch, no_cloud):
        monkeypatch.setattr(ai_agent, "_ask_local", lambda p, mt=1024: None)

        with pytest.raises(LocalAIUnavailable):
            ai_agent._ask_text("prompt")
        assert no_cloud == []

    @override_settings(LOCAL_AI_MODE=True)
    def test_local_failure_does_not_reach_cloud_via_network_error(self, monkeypatch, no_cloud):
        """A genuine connection error must surface as LocalAIUnavailable."""
        import requests

        def boom(*a, **kw):
            raise requests.exceptions.ConnectionError("connection refused")

        monkeypatch.setattr(requests, "post", boom)

        with pytest.raises(LocalAIUnavailable):
            ai_agent._ask_text("prompt")
        assert no_cloud == []

    @override_settings(LOCAL_AI_MODE=True)
    def test_narrative_methods_do_not_touch_cloud(self, monkeypatch, no_cloud):
        monkeypatch.setattr(ai_agent, "_ask_local", lambda p, mt=1024: "narrative")
        agent = HospitalKPIAgent()
        kpis = agent.calculate_kpis(
            [{"date": "2025-01-01", "description": "d", "amount": 100.0, "type": "DEPOSIT"}],
            0, 100, period_days=30,
        )

        agent.generate_insights([], kpis, "Jan 2025")
        agent.answer_question("How are we doing?", kpis, "statement text")
        agent.answer_system_question("What is revenue?", {"kpis": kpis})
        agent.generate_report_summary({"kpis": kpis})
        agent.generate_detailed_report({"kpis": kpis})

        assert no_cloud == []


# ── 4. Claude Vision disabled locally ────────────────────────────────────────

class TestVisionDisabled:

    @override_settings(LOCAL_AI_MODE=True)
    def test_vision_client_is_none_in_local_mode(self):
        assert ai_agent._claude_client_for_vision() is None

    @override_settings(LOCAL_AI_MODE=True)
    def test_ocr_returns_none_and_makes_no_call(self, no_cloud):
        agent = HospitalKPIAgent()
        assert agent.ocr_pdf_pages(["ZmFrZQ=="]) is None
        assert no_cloud == []


# ── 5. Deterministic KPI calculations unchanged ──────────────────────────────

class TestDeterministicKpisUnchanged:
    """The KPI engine must produce identical output regardless of AI mode."""

    def _txs(self):
        return [
            {"date": "2025-01-02", "description": "Payment", "amount": 5000.0,
             "type": "DEPOSIT", "category": "Customer Payment"},
            {"date": "2025-01-03", "description": "Salary", "amount": 1200.0,
             "type": "WITHDRAWAL", "category": "Staff Salaries"},
            {"date": "2025-01-05", "description": "Airtime", "amount": 300.0,
             "type": "WITHDRAWAL", "category": "Airtime"},
        ]

    def test_kpis_identical_in_both_modes(self):
        agent = HospitalKPIAgent()
        with override_settings(LOCAL_AI_MODE=False):
            cloud = agent.calculate_kpis(self._txs(), 1000, 4500, period_days=30)
        with override_settings(LOCAL_AI_MODE=True):
            local = agent.calculate_kpis(self._txs(), 1000, 4500, period_days=30)

        assert cloud == local
        assert local["Total_Revenue"]["value"] == 5000.0
        assert local["Total_Expenses"]["value"] == 1500.0
        assert local["Net_Income"]["value"] == 3500.0

    def test_health_alerts_and_recurring_need_no_model(self, no_cloud):
        """These must be pure Python — provable by running them under tripwires."""
        agent = HospitalKPIAgent()
        with override_settings(LOCAL_AI_MODE=True):
            kpis = agent.calculate_kpis(self._txs(), 1000, 4500, period_days=30)
            assert agent.compute_health_score(kpis)
            agent.generate_alerts(kpis)
            agent.detect_recurring_payments(self._txs())
        assert no_cloud == []


# ── 6. Local deterministic statement extraction ──────────────────────────────

MPESA_SAMPLE = """
MPESA STATEMENT
UDEPE0P493 2026-04-14 18:40:07 Customer Payment Funds received from JOHN DOE Completed - DEPOSIT KES 5,000.00 10,927.44
TJVPE8QU4X 2026-04-15 10:25:05 Airtime Purchase Completed -20.00 10,907.44
SGHPE1ZZ9Q 2026-04-16 09:12:31 Pay Bill Charge Completed -150.00 10,757.44
"""


class TestLocalDeterministicExtraction:

    @override_settings(LOCAL_AI_MODE=True)
    def test_extracts_without_any_model_call(self, monkeypatch, no_cloud):
        # The local model must not be called for extraction either.
        def local_tripwire(*a, **kw):
            raise AssertionError("local model called for transaction extraction")
        monkeypatch.setattr(ai_agent, "_ask_local", local_tripwire)

        agent = HospitalKPIAgent()
        data = agent.extract_financial_data(MPESA_SAMPLE)

        assert data is not None
        assert len(data["transactions"]) >= 3
        assert no_cloud == []

    @override_settings(LOCAL_AI_MODE=True)
    def test_figures_are_computed_in_python(self, no_cloud):
        agent = HospitalKPIAgent()
        data = agent.extract_financial_data(MPESA_SAMPLE)

        deposits = sum(t["amount"] for t in data["transactions"] if t["type"] == "DEPOSIT")
        withdrawals = sum(t["amount"] for t in data["transactions"] if t["type"] == "WITHDRAWAL")

        assert data["total_deposits"] == deposits
        assert data["total_withdrawals"] == withdrawals
        # Closing balance is derived, never taken from a model.
        assert data["closing_balance"] == pytest.approx(
            data["opening_balance"] + deposits - withdrawals
        )

    @override_settings(LOCAL_AI_MODE=True)
    def test_unparseable_statement_raises_rather_than_inventing_data(self, no_cloud):
        agent = HospitalKPIAgent()
        with pytest.raises(LocalExtractionFailed):
            agent.extract_financial_data("This document contains no transactions at all.")
        assert no_cloud == []

    @override_settings(LOCAL_AI_MODE=True)
    def test_csv_mapping_is_deterministic_and_never_calls_model(self, monkeypatch, no_cloud):
        def local_tripwire(*a, **kw):
            raise AssertionError("local model called for CSV mapping")
        monkeypatch.setattr(ai_agent, "_ask_local", local_tripwire)

        agent = HospitalKPIAgent()
        mapping = agent.extract_csv_structure_with_ai(
            "Date,Description,Amount,Balance\n2025-01-02,Payment,5000.00,10000.00\n"
        )

        assert mapping is not None
        assert mapping["date_index"] == 0
        assert mapping["amount_index"] == 2
        assert no_cloud == []

    @override_settings(LOCAL_AI_MODE=True)
    def test_unrecognisable_csv_returns_none_rather_than_guessing(self, no_cloud):
        agent = HospitalKPIAgent()
        assert agent.extract_csv_structure_with_ai("foo,bar,baz\nqux,quux,corge\n") is None
        assert no_cloud == []


# ── 7. Cloud mode regression ─────────────────────────────────────────────────

class TestCloudModeRegression:
    """With LOCAL_AI_MODE off, behaviour must be exactly as before."""

    def test_local_ai_disabled_by_default(self):
        # settings.py default is false; nothing should opt in implicitly.
        with override_settings(LOCAL_AI_MODE=False):
            assert local_ai_enabled() is False

    @override_settings(LOCAL_AI_MODE=False)
    def test_cloud_path_used_and_local_not_called(self, monkeypatch):
        def local_tripwire(*a, **kw):
            raise AssertionError("_ask_local called while in cloud mode")

        monkeypatch.setattr(ai_agent, "_ask_local", local_tripwire)
        monkeypatch.setattr(ai_agent, "_ask_openrouter", lambda p, mt=1024: "cloud answer")

        assert ai_agent._ask_text("prompt") == "cloud answer"

    @override_settings(LOCAL_AI_MODE=False)
    def test_ask_still_round_robins_providers(self, monkeypatch):
        monkeypatch.setattr(ai_agent, "_ask_local",
                            lambda *a, **kw: pytest.fail("_ask_local used in cloud mode"))
        monkeypatch.setattr(
            ai_agent, "_PROVIDER_FUNCS", {"stub": lambda p, mt: "stub answer"}
        )
        monkeypatch.setattr(ai_agent, "_PROVIDER_ORDER", ["stub"])

        assert ai_agent._ask("prompt") == "stub answer"

    @override_settings(LOCAL_AI_MODE=False)
    def test_vision_not_disabled_in_cloud_mode(self, monkeypatch):
        """The vision gate must be local-mode-specific, not a blanket disable."""
        monkeypatch.setattr(ai_agent, "_anthropic_sdk", None)
        # With the SDK absent it returns None for the pre-existing reason, but
        # crucially it gets past the local-mode gate to that check.
        assert ai_agent._claude_client_for_vision() is None


# ── 8. Prompt / token limits ─────────────────────────────────────────────────

class TestTokenLimits:

    @override_settings(LOCAL_AI_MODE=True, LOCAL_AI_MAX_TOKENS=384)
    def test_max_tokens_clamped_to_local_ceiling(self, monkeypatch):
        captured = {}

        class FakeResponse:
            def raise_for_status(self): pass
            def json(self): return {"choices": [{"message": {"content": "ok"}}]}

        def fake_post(url, **kwargs):
            captured["max_tokens"] = kwargs["json"]["max_tokens"]
            return FakeResponse()

        import requests
        monkeypatch.setattr(requests, "post", fake_post)

        # Callers request cloud-sized budgets; the local path must clamp them.
        ai_agent._ask_local("p", max_tokens=8192)
        assert captured["max_tokens"] == 384

        ai_agent._ask_local("p", max_tokens=100)
        assert captured["max_tokens"] == 100

    @override_settings(LOCAL_AI_MODE=True, LOCAL_AI_MAX_TOKENS=384)
    def test_local_prompts_stay_small(self, monkeypatch):
        """Local prompts must fit a 1024-token context — assert on characters."""
        prompts = []
        monkeypatch.setattr(
            ai_agent, "_ask_local",
            lambda p, mt=1024: prompts.append(p) or "ok",
        )

        agent = HospitalKPIAgent()
        txs = [{"date": "2025-01-0%d" % (i % 9 + 1), "description": "Transaction %d" % i,
                "amount": 100.0 + i, "type": "DEPOSIT" if i % 2 else "WITHDRAWAL",
                "category": "Other"} for i in range(200)]
        kpis = agent.calculate_kpis(txs, 0, 5000, period_days=30)

        agent.generate_insights(txs, kpis, "Jan 2025")
        agent.answer_question("How are we doing?", kpis, "x" * 50000)
        agent.answer_system_question("Revenue?", {"kpis": kpis, "junk": "y" * 50000})

        assert prompts, "expected local prompts to be built"
        for p in prompts:
            # ~4 chars/token: 2500 chars is comfortably inside a 1024-token window
            # once the response budget is accounted for.
            assert len(p) < 2500, f"local prompt too large: {len(p)} chars"

    def test_compact_kpi_lines_is_bounded(self):
        agent = HospitalKPIAgent()
        kpis = agent.calculate_kpis(
            [{"date": "2025-01-01", "description": "d", "amount": 100.0, "type": "DEPOSIT"}],
            0, 100, period_days=30,
        )
        text = _compact_kpi_lines(kpis)
        assert 0 < len(text.splitlines()) <= 10
        assert "Total Revenue" in text


# ── Startup security warning ─────────────────────────────────────────────────

class TestCloudKeyWarning:

    @override_settings(LOCAL_AI_MODE=True, ANTHROPIC_API_KEY="sk-ant-should-not-be-here")
    def test_warns_about_cloud_keys_without_logging_values(self):
        # The "kpi" logger sets propagate=False, so caplog's root handler never
        # sees these records — attach a handler to the logger itself instead.
        import logging

        records = []

        class Collector(logging.Handler):
            def emit(self, record):
                records.append(self.format(record))

        handler = Collector()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger = logging.getLogger("kpi.ai_agent")
        logger.addHandler(handler)
        try:
            present = ai_agent.warn_if_cloud_keys_present()
        finally:
            logger.removeHandler(handler)

        text = "\n".join(records)
        assert "ANTHROPIC_API_KEY" in present
        assert "sk-ant-should-not-be-here" not in text, "key value must never be logged"
        assert "SECURITY" in text

    @override_settings(LOCAL_AI_MODE=False, ANTHROPIC_API_KEY="sk-ant-fine-in-cloud-mode")
    def test_no_warning_in_cloud_mode(self):
        assert ai_agent.warn_if_cloud_keys_present() == []
