"""Tests for the AI agent's core logic — KPI calculation, health score, recurring detection, and local fallbacks."""
import pytest
from decimal import Decimal
from kpi.ai_agent import HospitalKPIAgent


class TestKpiCalculation:
    """Verify the offline KPI math (no API calls)."""

    def _transactions(self, deposits=None, withdrawals=None):
        """Build a sample transaction list."""
        txs = []
        for i, amt in enumerate(deposits or []):
            txs.append({
                "date": f"2025-0{(i % 9) + 1}-0{(i % 28) + 1:02d}",
                "description": f"Deposit {i}",
                "amount": float(amt),
                "type": "DEPOSIT",
                "category": "Customer Payment",
            })
        for i, amt in enumerate(withdrawals or []):
            txs.append({
                "date": f"2025-0{(i % 9) + 1}-0{(i % 28) + 1:02d}",
                "description": f"Withdrawal {i}",
                "amount": float(amt),
                "type": "WITHDRAWAL",
                "category": "Staff Salaries",
            })
        return txs

    def test_revenue_and_expense_totals(self):
        agent = HospitalKPIAgent()
        txs = self._transactions(deposits=[1000, 2000, 1500], withdrawals=[500, 300])
        kpis = agent.calculate_kpis(txs, 0, 4200, period_days=30)
        assert kpis["Total_Revenue"]["value"] == 4500.0
        assert kpis["Total_Expenses"]["value"] == 800.0
        assert kpis["Net_Income"]["value"] == 3700.0
        assert kpis["Profit_Margin"]["value"] == pytest.approx(82.22, rel=0.1)

    def test_expense_ratio(self):
        agent = HospitalKPIAgent()
        txs = self._transactions(deposits=[5000], withdrawals=[4000])
        kpis = agent.calculate_kpis(txs, 0, 1000, period_days=30)
        assert kpis["Expense_to_Revenue_Ratio"]["value"] == 80.0

    def test_liquidity_days(self):
        agent = HospitalKPIAgent()
        txs = self._transactions(deposits=[10000], withdrawals=[1000])
        kpis = agent.calculate_kpis(txs, 0, 9000, period_days=30)
        # 9000 / (1000/30) = 270 days
        assert kpis["Liquidity_Days"]["value"] == pytest.approx(270.0, rel=0.1)

    def test_cash_runway(self):
        agent = HospitalKPIAgent()
        txs = self._transactions(deposits=[3000], withdrawals=[1000])
        kpis = agent.calculate_kpis(txs, 0, 2000, period_days=30)
        assert kpis["Cash_Runway_Months"]["value"] == pytest.approx(2.0, rel=0.1)

    def test_anomaly_detection(self):
        agent = HospitalKPIAgent()
        # 19 normal deposits (~100 each) + 1 extreme outlier (10000)
        # With enough normal values, the outlier becomes >2.5 std from mean
        normal_deps = [100] * 19
        txs = self._transactions(deposits=normal_deps + [10000], withdrawals=[50, 50, 50, 50, 50])
        kpis = agent.calculate_kpis(txs, 0, 10500, period_days=30)
        assert kpis["Anomaly_Count"]["value"] >= 1

    def test_empty_transactions(self):
        agent = HospitalKPIAgent()
        kpis = agent.calculate_kpis([], 0, 0, period_days=30)
        assert kpis["Total_Revenue"]["value"] == 0.0
        assert kpis["Total_Expenses"]["value"] == 0.0
        assert kpis["Transaction_Count"]["value"] == 0

    def test_mixed_credit_debit_types(self):
        agent = HospitalKPIAgent()
        txs = [
            {"date": "2025-01-01", "description": "CREDIT", "amount": 500, "type": "CREDIT", "category": "Income"},
            {"date": "2025-01-02", "description": "DEBIT", "amount": 200, "type": "DEBIT", "category": "Expense"},
            {"date": "2025-01-03", "description": "CR", "amount": 300, "type": "CR", "category": "Income"},
            {"date": "2025-01-04", "description": "DR", "amount": 100, "type": "DR", "category": "Expense"},
        ]
        kpis = agent.calculate_kpis(txs, 0, 500, period_days=30)
        assert kpis["Total_Revenue"]["value"] == 800.0
        assert kpis["Total_Expenses"]["value"] == 300.0


class TestHealthScore:
    """Verify composite financial health scoring."""

    def test_stable_score(self):
        """Max possible score is 85 (25 profit_margin + 25 runway + 20 expense_ratio + 15 frequency)."""
        kpis = {
            "Profit_Margin": {"value": 100.0},
            "Cash_Runway_Months": {"value": 24.0},
            "Expense_to_Revenue_Ratio": {"value": 0.0},
            "Deposit_Frequency": {"value": 100},
            "Anomaly_Count": {"value": 0},
        }
        result = HospitalKPIAgent.compute_health_score(kpis)
        assert result["score"] == 85
        assert result["tier"] == "Stable"

    def test_distressed_score(self):
        kpis = {
            "Profit_Margin": {"value": -5.0},
            "Cash_Runway_Months": {"value": 0.5},
            "Expense_to_Revenue_Ratio": {"value": 110.0},
            "Deposit_Frequency": {"value": 2},
            "Anomaly_Count": {"value": 8},
        }
        result = HospitalKPIAgent.compute_health_score(kpis)
        assert result["score"] < 40
        assert result["tier"] in ("Distressed", "Critical")

    def test_boundary_scores(self):
        """Test edge cases for score calculation."""
        kpis = {
            "Profit_Margin": {"value": 0},
            "Cash_Runway_Months": {"value": 0},
            "Expense_to_Revenue_Ratio": {"value": 100},
            "Deposit_Frequency": {"value": 0},
            "Anomaly_Count": {"value": 0},
        }
        result = HospitalKPIAgent.compute_health_score(kpis)
        assert 0 <= result["score"] <= 100

    def test_missing_kpi_defaults(self):
        result = HospitalKPIAgent.compute_health_score({})
        assert 0 <= result["score"] <= 100


class TestRecurringPayments:
    """Verify recurring payment detection."""

    def test_detects_monthly_payments(self):
        txs = [
            {"date": "2025-01-05", "description": "RENT PAYMENT", "amount": 50000, "type": "WITHDRAWAL"},
            {"date": "2025-02-05", "description": "RENT PAYMENT", "amount": 50000, "type": "WITHDRAWAL"},
            {"date": "2025-03-05", "description": "RENT PAYMENT", "amount": 51000, "type": "WITHDRAWAL"},
        ]
        recurring = HospitalKPIAgent.detect_recurring_payments(txs)
        assert len(recurring) >= 1
        assert recurring[0]["description"] == "RENT PAYMENT"
        assert 25 <= recurring[0]["avg_interval_days"] <= 35
        assert recurring[0]["occurrences"] == 3

    def test_single_payment_not_recurring(self):
        txs = [
            {"date": "2025-01-10", "description": "One-off payment", "amount": 1000, "type": "WITHDRAWAL"},
        ]
        recurring = HospitalKPIAgent.detect_recurring_payments(txs)
        assert len(recurring) == 0

    def test_empty_transactions(self):
        recurring = HospitalKPIAgent.detect_recurring_payments([])
        assert recurring == []


class TestLocalFallbacks:
    """Verify offline fallback responses."""

    def test_local_insights(self):
        agent = HospitalKPIAgent()
        txs = [{"amount": 1000, "type": "DEPOSIT"}, {"amount": 500, "type": "WITHDRAWAL"}]
        kpis = {
            "Total_Revenue": {"value": 1000},
            "Total_Expenses": {"value": 500},
            "Net_Income": {"value": 500},
            "Profit_Margin": {"value": 50.0},
            "Expense_to_Revenue_Ratio": {"value": 50.0},
            "Closing_Balance": {"value": 1500},
            "Liquidity_Days": {"value": 90},
        }
        result = agent._generate_local_insights(txs, kpis, "January 2025")
        assert "January 2025" in result
        assert "KES" in result

    def test_answer_locally(self):
        agent = HospitalKPIAgent()
        kpis = {"Profit_Margin": {"value": 15.5, "unit": "%"}}
        answer = agent._answer_locally("What is my profit margin?", kpis)
        assert "15.50" in answer

    def test_answer_locally_fallback(self):
        agent = HospitalKPIAgent()
        answer = agent._answer_locally("What is the weather?", {})
        assert "Ask about" in answer

    def test_local_report_summary(self):
        agent = HospitalKPIAgent()
        ctx = {
            "aggregate_metrics": {
                "total_revenue": 100000,
                "total_expenses": 60000,
                "net_profit": 40000,
                "profit_margin": 40.0,
            },
            "top_files": [{"file_name": "statement.pdf", "closing_balance": 50000}],
        }
        result = agent._build_local_report_summary(ctx)
        assert "100,000" in result
        assert "40.00" in result
