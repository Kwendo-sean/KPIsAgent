"""Fully local OCR for scanned bank statements.

RapidOCR + ONNX Runtime (CPU) using the PP-OCR models bundled inside the
installed ``rapidocr`` package. No PaddlePaddle, no PaddleOCR runtime, no
Baidu Cloud, no external OCR API of any kind.

Why explicit model paths matter
───────────────────────────────
RapidOCR's ONNX engine reaches its model-download branch only when
``model_path`` is None (see ``rapidocr/inference_engine/onnxruntime/main.py``:
``model_path = cfg.get("model_path", None); if model_path is None: ...
DownloadFile.run(...)``). By always resolving Det/Cls/Rec to explicit files on
disk, that branch becomes structurally unreachable — there is no code path from
this module to a network fetch. A missing model file raises
LocalOCRUnavailable rather than triggering a download.

Role in the pipeline
────────────────────
OCR is a *text extraction mechanism only*. It hands text to the existing
deterministic parsers and never computes totals, balances, KPIs, health
scores, alerts, or any other financial figure.
"""
import base64
import gc
import logging
import threading
from pathlib import Path

from .ai_agent import _local_conf, _local_int, local_ai_enabled

logger = logging.getLogger("kpi.local_ocr")

# Models bundled inside the rapidocr wheel (rapidocr/models/). These are the
# PP-OCR "small"/mobile variants — the smallest set appropriate for English
# bank statements on a 4 GB Pi.
_BUNDLED_DET = "PP-OCRv6_det_small.onnx"
_BUNDLED_REC = "PP-OCRv6_rec_small.onnx"
_BUNDLED_CLS = "ch_ppocr_mobile_v2.0_cls_mobile.onnx"

# Only one OCR job may run at a time: a second concurrent engine would double
# peak RAM on a 4 GB device already hosting llama-server.
_OCR_LOCK = threading.Lock()


class LocalOCRUnavailable(RuntimeError):
    """Raised when local OCR is required but cannot run.

    Always explicit — local OCR never degrades to a cloud vision API.
    """


def local_ocr_enabled() -> bool:
    """True only when local AI mode is on AND local OCR is switched on.

    OCR is gated behind LOCAL_AI_MODE deliberately: this code path exists for
    the offline edge deployment, not for cloud installs.
    """
    if not local_ai_enabled():
        return False
    val = _local_conf("LOCAL_OCR_ENABLED", False)
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def _bundled_model_dir() -> Path | None:
    """Locate rapidocr/models/ inside the installed package, without importing
    the heavy engine modules."""
    try:
        import importlib.util

        spec = importlib.util.find_spec("rapidocr")
        if spec is None or not spec.origin:
            return None
        return Path(spec.origin).resolve().parent / "models"
    except Exception as e:
        logger.warning("Could not locate the rapidocr package: %s", type(e).__name__)
        return None


def resolve_model_paths() -> dict[str, str]:
    """Resolve det/cls/rec to explicit local files, or fail clearly.

    LOCAL_OCR_MODEL_DIR overrides the bundled directory (useful if the models
    are staged outside site-packages). Never downloads anything.
    """
    configured = str(_local_conf("LOCAL_OCR_MODEL_DIR", "") or "").strip()
    model_dir = Path(configured) if configured else _bundled_model_dir()

    if model_dir is None:
        raise LocalOCRUnavailable(
            "Local OCR is enabled but the rapidocr package could not be found. "
            "Install the Pi OCR dependencies (see requirements-pi.txt) or set "
            "LOCAL_OCR_MODEL_DIR to a directory containing the PP-OCR ONNX models."
        )

    if not model_dir.is_dir():
        raise LocalOCRUnavailable(
            f"Local OCR model directory does not exist: {model_dir}. "
            "Models are never downloaded automatically — stage them on the device."
        )

    paths = {
        "det": model_dir / _BUNDLED_DET,
        "cls": model_dir / _BUNDLED_CLS,
        "rec": model_dir / _BUNDLED_REC,
    }
    missing = [f"{k}={v.name}" for k, v in paths.items() if not v.is_file()]
    if missing:
        raise LocalOCRUnavailable(
            f"Local OCR model file(s) missing from {model_dir}: {', '.join(missing)}. "
            "They are NOT downloaded automatically. Reinstall rapidocr (models ship "
            "inside the wheel) or point LOCAL_OCR_MODEL_DIR at a directory holding them."
        )
    return {k: str(v) for k, v in paths.items()}


def _build_engine():
    """Construct a RapidOCR engine bound to explicit local model paths.

    Built lazily per job and released afterwards — never held at module scope,
    which would pin its RAM for the lifetime of every worker process.
    """
    try:
        from rapidocr import RapidOCR
    except ImportError as e:
        raise LocalOCRUnavailable(
            "Local OCR is enabled but rapidocr is not installed. "
            "Install the Pi OCR dependencies (see requirements-pi.txt)."
        ) from e

    paths = resolve_model_paths()
    threads = _local_int("LOCAL_OCR_THREADS", 2)

    params = {
        # Explicit paths — this is what makes the download branch unreachable.
        "Det.model_path": paths["det"],
        "Cls.model_path": paths["cls"],
        "Rec.model_path": paths["rec"],
        # CPU only. The Pi has no CUDA, and we do not pretend otherwise.
        "EngineConfig.onnxruntime.use_cuda": False,
        "EngineConfig.onnxruntime.intra_op_num_threads": threads,
        "EngineConfig.onnxruntime.inter_op_num_threads": 1,
        "Global.log_level": "error",
    }
    logger.info("Building local OCR engine (threads=%d, models=%s)",
                threads, Path(paths["det"]).parent)
    return RapidOCR(params=params)


def _decode_page(image_b64: str):
    """Base64 PNG → BGR ndarray. Returns None if undecodable."""
    import cv2
    import numpy as np

    try:
        raw = base64.b64decode(image_b64)
        arr = np.frombuffer(raw, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        del raw, arr
        return img
    except Exception as e:
        logger.warning("Failed to decode a rendered page: %s", type(e).__name__)
        return None


def ocr_pages_to_text(page_images_b64: list, consume: bool = True) -> str | None:
    """OCR rendered pages and return their combined text.

    Pages are processed one at a time and each image is released immediately
    afterwards. With consume=True (the default) the caller's list slots are
    cleared as we go, so the full set of rendered pages is never held in
    memory alongside the OCR engine — the single largest allocation on a 4 GB
    device.

    Returns None if nothing legible was found. Raises LocalOCRUnavailable if
    OCR cannot run at all. Never falls back to a cloud provider.
    """
    if not page_images_b64:
        return None

    max_pages = _local_int("LOCAL_OCR_MAX_PAGES", 20)
    total = len(page_images_b64)
    if total > max_pages:
        logger.warning("Local OCR: %d pages exceeds LOCAL_OCR_MAX_PAGES=%d — "
                       "processing the first %d only", total, max_pages, max_pages)
        total = max_pages

    # Serialise OCR jobs. Local narrative inference happens later in the
    # pipeline, so OCR and Qwen are not intended to run at the same time.
    with _OCR_LOCK:
        engine = _build_engine()
        parts: list[str] = []
        try:
            for idx in range(total):
                image_b64 = page_images_b64[idx]
                if consume:
                    # Drop our reference to the base64 string as we go.
                    page_images_b64[idx] = None
                if not image_b64:
                    continue

                img = _decode_page(image_b64)
                del image_b64
                if img is None:
                    continue

                try:
                    result = engine(img)
                except Exception as e:
                    logger.error("Local OCR failed on page %d: %s: %s",
                                 idx + 1, type(e).__name__, e)
                    raise LocalOCRUnavailable(
                        f"Local OCR failed while processing page {idx + 1}: "
                        f"{type(e).__name__}. The document was not sent anywhere."
                    ) from e
                finally:
                    del img

                txts = getattr(result, "txts", None)
                if txts:
                    parts.append("\n".join(txts))
                    logger.info("Local OCR: page %d/%d → %d text segments",
                                idx + 1, total, len(txts))
                else:
                    logger.info("Local OCR: page %d/%d → no text detected", idx + 1, total)
                del result
        finally:
            # Release the ONNX sessions before returning. On 4 GB this matters.
            del engine
            gc.collect()
            logger.info("Local OCR engine released")

    text = "\n".join(p for p in parts if p).strip()
    return text or None
