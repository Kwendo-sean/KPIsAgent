"""Tests for document extraction and credit-control aggregates.

Same rule as elsewhere: every figure is produced by Python. These assert exact
values, and that absent information stays absent rather than being guessed.
"""
import datetime as dt
from decimal import Decimal

import pytest

from kpi import document_analysis as da

INVOICE = """
ACME MEDICAL SUPPLIES LTD
TAX INVOICE

Invoice No: INV-2026-0481
Invoice Date: 12/01/2026
Due Date: 11/02/2026

Bill To: Nairobi General Hospital

Description                 Qty      Amount
Surgical gloves             200    45,000.00
Sterile dressings            80    28,500.00

Subtotal                          73,500.00
VAT 16%                           11,760.00
Total Amount Due     KES          85,260.00
"""

RECEIPT = """
PAYMENT RECEIPT
Receipt No: RCP-9912
Date: 03/03/2026
Received From: Westlands Clinic
Total: KES 12,000.00
Thank you for your payment.
"""

NET_TERMS = """
INVOICE
Invoice No: X-77
Invoice Date: 01/06/2026
Terms: Net 30
Bill To: Karen Medical Centre
Total Amount Due: KES 50,000.00
"""


class _FakeDoc:
    """Stands in for the Document model so these stay database-free."""

    def __init__(self, amount, due_date=None, status="PENDING", counterparty=""):
        self.amount = Decimal(str(amount)) if amount is not None else None
        self.due_date = due_date
        self.status = status
        self.counterparty = counterparty

    @property
    def is_outstanding(self):
        return self.status in ("PENDING", "IN_REVIEW", "PROCESSED")

    def days_overdue(self, today=None):
        if not self.due_date or not self.is_outstanding:
            return None
        today = today or dt.date.today()
        delta = (today - self.due_date).days
        return delta if delta > 0 else 0


class TestFieldExtraction:

    def test_invoice_fields_are_exact(self):
        fields = da.extract_document_fields(INVOICE, "invoice.pdf")
        assert fields["doc_type"] == "INVOICE"
        # The labelled total wins over the subtotal and the VAT line.
        assert fields["amount"] == Decimal("85260.00")
        assert fields["currency"] == "KES"
        assert fields["reference"] == "INV-2026-0481"
        assert fields["doc_date"] == dt.date(2026, 1, 12)
        assert fields["due_date"] == dt.date(2026, 2, 11)
        assert "Nairobi General Hospital" in fields["counterparty"]

    def test_receipt_is_classified_and_parsed(self):
        fields = da.extract_document_fields(RECEIPT, "receipt.pdf")
        assert fields["doc_type"] == "RECEIPT"
        assert fields["amount"] == Decimal("12000.00")
        assert "Westlands Clinic" in fields["counterparty"]

    def test_net_terms_derive_a_due_date_from_the_document_date(self):
        fields = da.extract_document_fields(NET_TERMS, "x.pdf")
        assert fields["doc_date"] == dt.date(2026, 6, 1)
        assert fields["due_date"] == dt.date(2026, 7, 1)

    def test_missing_fields_are_none_not_guessed(self):
        fields = da.extract_document_fields("Just some prose with no figures.", "note.txt")
        assert fields["amount"] is None
        assert fields["doc_date"] is None
        assert fields["due_date"] is None
        assert fields["reference"] == ""

    def test_unrecognised_document_stays_other(self):
        assert da.detect_document_type("lorem ipsum dolor sit amet", "x.bin") == "OTHER"

    def test_statement_text_is_recognised(self):
        assert da.detect_document_type(
            "MPESA STATEMENT\nOpening balance 100.00", "Jan.pdf") == "STATEMENT"

    def test_empty_input_does_not_crash(self):
        fields = da.extract_document_fields("", "")
        assert fields["amount"] is None
        assert fields["doc_type"] == "OTHER"


class TestAging:

    TODAY = dt.date(2026, 3, 1)

    def _book(self):
        return [
            _FakeDoc(10000, dt.date(2026, 4, 1)),    # not yet due -> Current
            _FakeDoc(5000, dt.date(2026, 2, 20)),    # 9 days late -> 1-30
            _FakeDoc(2500, dt.date(2026, 1, 20)),    # 40 days     -> 31-60
            _FakeDoc(1500, dt.date(2025, 12, 20)),   # 71 days     -> 61-90
            _FakeDoc(800, dt.date(2025, 10, 1)),     # 151 days    -> 90+
        ]

    def test_documents_land_in_the_right_buckets(self):
        report = da.build_aging_report(self._book(), today=self.TODAY)
        buckets = {b["bucket"]: b for b in report["buckets"]}
        assert buckets["Current"]["total"] == 10000
        assert buckets["1-30 days"]["total"] == 5000
        assert buckets["31-60 days"]["total"] == 2500
        assert buckets["61-90 days"]["total"] == 1500
        assert buckets["90+ days"]["total"] == 800

    def test_totals_are_exact(self):
        report = da.build_aging_report(self._book(), today=self.TODAY)
        assert report["total_outstanding"] == 19800
        assert report["overdue_total"] == 9800   # everything except Current
        assert report["overdue_count"] == 4

    def test_cleared_documents_are_excluded(self):
        docs = self._book() + [_FakeDoc(999999, dt.date(2020, 1, 1), status="CLEARED")]
        report = da.build_aging_report(docs, today=self.TODAY)
        assert report["total_outstanding"] == 19800

    def test_documents_without_an_amount_are_excluded(self):
        docs = self._book() + [_FakeDoc(None, dt.date(2020, 1, 1))]
        report = da.build_aging_report(docs, today=self.TODAY)
        assert report["total_outstanding"] == 19800

    def test_percentages_sum_to_about_one_hundred(self):
        report = da.build_aging_report(self._book(), today=self.TODAY)
        assert sum(b["pct"] for b in report["buckets"]) == pytest.approx(100, abs=0.5)

    def test_empty_book_is_safe(self):
        report = da.build_aging_report([], today=self.TODAY)
        assert report["total_outstanding"] == 0
        assert report["overdue_count"] == 0


class TestExposure:

    TODAY = dt.date(2026, 3, 1)

    def test_exposure_groups_and_ranks_by_counterparty(self):
        docs = [
            _FakeDoc(8000, dt.date(2026, 1, 1), counterparty="ACME Ltd"),
            _FakeDoc(2000, dt.date(2026, 4, 1), counterparty="ACME Ltd"),
            _FakeDoc(5000, dt.date(2026, 2, 1), counterparty="Beta Clinic"),
        ]
        rows = da.build_counterparty_exposure(docs, today=self.TODAY)
        assert rows[0]["counterparty"] == "ACME Ltd"
        assert rows[0]["total"] == 10000
        # Only the overdue one counts toward overdue exposure.
        assert rows[0]["overdue"] == 8000
        assert rows[0]["max_days_overdue"] == 59

    def test_missing_counterparty_is_labelled_not_dropped(self):
        rows = da.build_counterparty_exposure(
            [_FakeDoc(700, dt.date(2026, 1, 1))], today=self.TODAY)
        assert rows[0]["counterparty"] == "Unattributed"
        assert rows[0]["total"] == 700


class TestQueueSummary:

    def test_counts_by_status_and_flags_incomplete_records(self):
        docs = [
            _FakeDoc(100, counterparty="A"),
            _FakeDoc(None, counterparty="B"),
            _FakeDoc(300, counterparty=""),
        ]
        for d in docs:
            d.department = "CREDIT_CONTROL"
        summary = da.summarize_queue(docs)
        assert summary["total"] == 3
        assert summary["by_status"]["PENDING"] == 3
        # One missing an amount, one missing a counterparty.
        assert summary["needs_attention"] == 2
