"""Tests for local OCR (RapidOCR + ONNX Runtime, bundled PP-OCR models).

These run without rapidocr/onnxruntime/opencv installed: the engine and image
decoder are injected as fakes. That is deliberate — the properties under test
are about control flow and offline guarantees, not about ONNX numerics.

The central guarantees proved here:
  * a PDF that already has text is never OCR'd
  * OCR never runs unless explicitly enabled
  * no download function is reachable during OCR
  * Claude Vision is never constructed in local mode
  * missing model files fail loudly instead of downloading
  * OCR feeds the deterministic parser and computes no financial figure
"""
import sys
import types

import pytest
from django.test import override_settings

from kpi import ai_agent, local_ocr
from kpi.ai_agent import HospitalKPIAgent
from kpi.local_ocr import LocalOCRUnavailable, local_ocr_enabled, resolve_model_paths
from kpi.pdf_extractor import BankStatementPDFExtractor

# Text a scanned M-PESA statement would yield from OCR.
OCR_TEXT = (
    "UDEPE0P493 2026-04-14 18:40:07 Customer Payment Funds received from JOHN DOE "
    "Completed - DEPOSIT KES 5,000.00 10,927.44\n"
    "TJVPE8QU4X 2026-04-15 10:25:05 Airtime Purchase Completed -20.00 10,907.44\n"
    "SGHPE1ZZ9Q 2026-04-16 09:12:31 Pay Bill Charge Completed -150.00 10,757.44\n"
)


@pytest.fixture
def fake_models(tmp_path, monkeypatch):
    """A directory containing correctly named (empty) model files."""
    for name in (local_ocr._BUNDLED_DET, local_ocr._BUNDLED_REC, local_ocr._BUNDLED_CLS):
        (tmp_path / name).write_bytes(b"")
    monkeypatch.setattr(local_ocr, "_bundled_model_dir", lambda: tmp_path)
    return tmp_path


class FakeEngine:
    """Stands in for RapidOCR. Records construction/release and pages seen."""

    instances = []

    def __init__(self, params=None):
        self.params = params or {}
        self.pages = 0
        self.released = False
        FakeEngine.instances.append(self)

    def __call__(self, img):
        self.pages += 1
        line = OCR_TEXT.splitlines()[min(self.pages - 1, len(OCR_TEXT.splitlines()) - 1)]
        return types.SimpleNamespace(txts=(line,))

    def __del__(self):
        self.released = True


@pytest.fixture
def fake_engine(monkeypatch, fake_models):
    FakeEngine.instances = []
    built = {}

    def build():
        paths = resolve_model_paths()          # keep real path resolution under test
        built["paths"] = paths
        return FakeEngine(params={
            "Det.model_path": paths["det"],
            "Cls.model_path": paths["cls"],
            "Rec.model_path": paths["rec"],
        })

    monkeypatch.setattr(local_ocr, "_build_engine", build)
    monkeypatch.setattr(local_ocr, "_decode_page", lambda b64: object())
    return built


@pytest.fixture
def no_network(monkeypatch):
    """Trip on any outbound HTTP from any layer during OCR."""
    calls = []

    def tripwire(*a, **kw):
        calls.append(a[0] if a else "?")
        raise AssertionError(f"SECURITY FAILURE: network call attempted during OCR: {a[:1]}")

    import requests
    monkeypatch.setattr(requests, "get", tripwire)
    monkeypatch.setattr(requests, "post", tripwire)
    monkeypatch.setattr(requests.Session, "request", tripwire)
    import socket
    monkeypatch.setattr(socket.socket, "connect", tripwire)
    return calls


# ── 3 & 1. OCR gating: disabled by default; digital PDFs bypass OCR ──────────

class TestOcrGating:

    def test_disabled_by_default(self):
        with override_settings(LOCAL_AI_MODE=True):
            assert local_ocr_enabled() is False

    @override_settings(LOCAL_AI_MODE=False, LOCAL_OCR_ENABLED=True)
    def test_never_enabled_in_cloud_mode(self):
        """OCR is an edge-deployment feature; cloud installs keep Claude Vision."""
        assert local_ocr_enabled() is False

    @override_settings(LOCAL_AI_MODE=True, LOCAL_OCR_ENABLED=False)
    def test_ocr_not_invoked_when_disabled(self, monkeypatch):
        monkeypatch.setattr(
            local_ocr, "ocr_pages_to_text",
            lambda *a, **kw: pytest.fail("OCR ran while LOCAL_OCR_ENABLED=false"),
        )
        assert HospitalKPIAgent().ocr_pdf_pages(["ZmFrZQ=="]) is None

    def test_digital_pdf_does_not_need_ocr(self):
        """needs_ocr() is the gate: a text-layer PDF must never reach OCR."""
        assert BankStatementPDFExtractor.needs_ocr(OCR_TEXT) is False
        assert BankStatementPDFExtractor.needs_ocr("") is True


# ── 2 & 5. Scanned PDFs use local OCR, with no network reachable ─────────────

class TestLocalOcrRuns:

    @override_settings(LOCAL_AI_MODE=True, LOCAL_OCR_ENABLED=True)
    def test_scanned_pages_are_ocred_locally(self, fake_engine, no_network):
        text = HospitalKPIAgent().ocr_pdf_pages(["cGFnZTE=", "cGFnZTI=", "cGFnZTM="])

        assert text is not None
        assert "UDEPE0P493" in text
        assert FakeEngine.instances[0].pages == 3
        assert no_network == []

    @override_settings(LOCAL_AI_MODE=True, LOCAL_OCR_ENABLED=True)
    def test_no_download_function_is_invoked(self, fake_engine, monkeypatch, no_network):
        """Install a fake rapidocr download module and prove it is never touched."""
        called = []
        fake_dl = types.ModuleType("rapidocr.utils.download_file")
        fake_dl.DownloadFile = type(
            "DownloadFile", (),
            {"run": staticmethod(lambda *a, **kw: called.append("download"))},
        )
        monkeypatch.setitem(sys.modules, "rapidocr.utils.download_file", fake_dl)

        HospitalKPIAgent().ocr_pdf_pages(["cGFnZTE="])

        assert called == [], "no model download may occur"
        assert no_network == []

    @override_settings(LOCAL_AI_MODE=True, LOCAL_OCR_ENABLED=True)
    def test_engine_receives_explicit_model_paths(self, fake_engine):
        """Explicit paths are what make rapidocr's download branch unreachable."""
        HospitalKPIAgent().ocr_pdf_pages(["cGFnZTE="])

        params = FakeEngine.instances[0].params
        for key in ("Det.model_path", "Cls.model_path", "Rec.model_path"):
            assert params[key], f"{key} must be an explicit path, never None"
            assert params[key].endswith(".onnx")


# ── 4. Claude Vision never invoked in local mode ─────────────────────────────

class TestVisionNeverUsed:

    @override_settings(LOCAL_AI_MODE=True, LOCAL_OCR_ENABLED=True)
    def test_vision_client_not_constructed(self, fake_engine, monkeypatch):
        monkeypatch.setattr(
            ai_agent, "_claude_client_for_vision",
            lambda: pytest.fail("Claude Vision was constructed in local mode"),
        )
        assert HospitalKPIAgent().ocr_pdf_pages(["cGFnZTE="]) is not None

    @override_settings(LOCAL_AI_MODE=True, LOCAL_OCR_ENABLED=False)
    def test_vision_not_constructed_when_ocr_disabled(self, monkeypatch):
        monkeypatch.setattr(
            ai_agent, "_claude_client_for_vision",
            lambda: pytest.fail("Claude Vision was constructed in local mode"),
        )
        assert HospitalKPIAgent().ocr_pdf_pages(["cGFnZTE="]) is None


# ── 9. Missing models produce an explicit error, never a download ────────────

class TestMissingModels:

    @override_settings(LOCAL_AI_MODE=True, LOCAL_OCR_ENABLED=True, LOCAL_OCR_MODEL_DIR="")
    def test_missing_model_files_raise(self, tmp_path, monkeypatch, no_network):
        (tmp_path / local_ocr._BUNDLED_DET).write_bytes(b"")   # only one of three
        monkeypatch.setattr(local_ocr, "_bundled_model_dir", lambda: tmp_path)

        with pytest.raises(LocalOCRUnavailable) as exc:
            resolve_model_paths()

        msg = str(exc.value)
        assert "missing" in msg.lower()
        assert "NOT downloaded automatically" in msg
        assert no_network == []

    @override_settings(LOCAL_AI_MODE=True, LOCAL_OCR_ENABLED=True)
    def test_missing_model_dir_raises(self, monkeypatch, no_network):
        monkeypatch.setattr(local_ocr, "_bundled_model_dir", lambda: None)
        with pytest.raises(LocalOCRUnavailable):
            resolve_model_paths()
        assert no_network == []

    @override_settings(LOCAL_AI_MODE=True, LOCAL_OCR_ENABLED=True)
    def test_rapidocr_not_installed_raises_clearly(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "rapidocr", None)  # forces ImportError
        with pytest.raises(LocalOCRUnavailable) as exc:
            local_ocr._build_engine()
        assert "not installed" in str(exc.value)

    @override_settings(LOCAL_AI_MODE=True, LOCAL_OCR_ENABLED=True,
                       LOCAL_OCR_MODEL_DIR="/nonexistent/ocr/models")
    def test_configured_dir_missing_raises(self, no_network):
        with pytest.raises(LocalOCRUnavailable) as exc:
            resolve_model_paths()
        assert "does not exist" in str(exc.value)


# ── 6 & 7. OCR feeds the deterministic parser; figures stay deterministic ────

class TestOcrFeedsDeterministicParser:

    @override_settings(LOCAL_AI_MODE=True, LOCAL_OCR_ENABLED=True)
    def test_ocr_text_flows_into_deterministic_extraction(self, fake_engine, no_network):
        def local_model_tripwire(*a, **kw):
            raise AssertionError("the local model was asked to extract figures")

        agent = HospitalKPIAgent()
        ocr_text = agent.ocr_pdf_pages(["p1", "p2", "p3"])

        import kpi.ai_agent as m
        original = m._ask_local
        m._ask_local = local_model_tripwire
        try:
            data = agent.extract_financial_data(ocr_text)
        finally:
            m._ask_local = original

        assert len(data["transactions"]) >= 3
        assert data["total_deposits"] == 5000.0
        assert data["total_withdrawals"] == 170.0
        # Derived in Python, never read from OCR or a model.
        assert data["closing_balance"] == pytest.approx(
            data["opening_balance"] + 5000.0 - 170.0
        )
        assert no_network == []

    @override_settings(LOCAL_AI_MODE=True, LOCAL_OCR_ENABLED=True)
    def test_kpis_from_ocr_are_pure_python(self, fake_engine, no_network):
        agent = HospitalKPIAgent()
        data = agent.extract_financial_data(agent.ocr_pdf_pages(["p1", "p2", "p3"]))
        kpis = agent.calculate_kpis(
            data["transactions"], data["opening_balance"], data["closing_balance"],
            period_days=3,
        )

        assert kpis["Total_Revenue"]["value"] == 5000.0
        assert kpis["Total_Expenses"]["value"] == 170.0
        assert kpis["Net_Income"]["value"] == 4830.0
        # These must all run with no model and no network.
        assert agent.compute_health_score(kpis)
        agent.generate_alerts(kpis)
        agent.detect_recurring_payments(data["transactions"])
        assert no_network == []


# ── 8. Resources released after processing ───────────────────────────────────

class TestResourceRelease:

    @override_settings(LOCAL_AI_MODE=True, LOCAL_OCR_ENABLED=True)
    def test_engine_is_not_cached_between_jobs(self, fake_engine):
        agent = HospitalKPIAgent()
        agent.ocr_pdf_pages(["p1"])
        agent.ocr_pdf_pages(["p1"])

        # A fresh engine per job proves nothing is held at module scope.
        assert len(FakeEngine.instances) == 2
        assert not hasattr(local_ocr, "_ENGINE")

    @override_settings(LOCAL_AI_MODE=True, LOCAL_OCR_ENABLED=True)
    def test_engine_released_and_gc_collected(self, fake_engine, monkeypatch):
        collected = []
        monkeypatch.setattr(local_ocr.gc, "collect", lambda: collected.append(1))

        HospitalKPIAgent().ocr_pdf_pages(["p1"])

        assert collected, "gc.collect() must run after OCR"

    @override_settings(LOCAL_AI_MODE=True, LOCAL_OCR_ENABLED=True)
    def test_page_images_released_as_processed(self, fake_engine):
        pages = ["p1", "p2", "p3"]
        local_ocr.ocr_pages_to_text(pages)

        # Slots cleared so the full page set never coexists with the engine.
        assert pages == [None, None, None]

    @override_settings(LOCAL_AI_MODE=True, LOCAL_OCR_ENABLED=True)
    def test_engine_released_even_on_failure(self, monkeypatch, fake_models):
        class Boom(FakeEngine):
            def __call__(self, img):
                raise RuntimeError("onnx blew up")

        FakeEngine.instances = []
        monkeypatch.setattr(local_ocr, "_build_engine", lambda: Boom())
        monkeypatch.setattr(local_ocr, "_decode_page", lambda b64: object())
        collected = []
        monkeypatch.setattr(local_ocr.gc, "collect", lambda: collected.append(1))

        with pytest.raises(LocalOCRUnavailable):
            local_ocr.ocr_pages_to_text(["p1"])

        assert collected, "engine must be released even when OCR fails"

    @override_settings(LOCAL_AI_MODE=True, LOCAL_OCR_ENABLED=True, LOCAL_OCR_MAX_PAGES=2)
    def test_page_cap_is_respected(self, fake_engine):
        local_ocr.ocr_pages_to_text(["p1", "p2", "p3", "p4"])
        assert FakeEngine.instances[0].pages == 2


# ── 10. Cloud behaviour unchanged ────────────────────────────────────────────

class TestCloudUnchanged:

    @override_settings(LOCAL_AI_MODE=False)
    def test_cloud_ocr_path_still_used(self, monkeypatch):
        """With local mode off, ocr_pdf_pages must go to the Claude Vision path."""
        monkeypatch.setattr(
            local_ocr, "ocr_pages_to_text",
            lambda *a, **kw: pytest.fail("local OCR ran in cloud mode"),
        )
        used = []
        monkeypatch.setattr(ai_agent, "_claude_client_for_vision",
                            lambda: used.append("vision") or None)

        assert HospitalKPIAgent().ocr_pdf_pages(["p1"]) is None
        assert used == ["vision"], "cloud mode must still consult the vision client"

    @override_settings(LOCAL_AI_MODE=False)
    def test_local_ocr_module_not_required_in_cloud_mode(self, monkeypatch):
        """Cloud installs have no rapidocr; importing it must not be attempted."""
        monkeypatch.setitem(sys.modules, "rapidocr", None)
        monkeypatch.setattr(ai_agent, "_claude_client_for_vision", lambda: None)
        assert HospitalKPIAgent().ocr_pdf_pages(["p1"]) is None
