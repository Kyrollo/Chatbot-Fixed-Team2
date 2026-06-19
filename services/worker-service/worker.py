import hf_env  # noqa: F401 — MUST be first: sets HF_HUB_OFFLINE before any HuggingFace import
import io
import logging
import os
import sys
from pathlib import Path

from celery import Celery
from celery.signals import worker_process_init
from dotenv import load_dotenv

load_dotenv(override=False)

# ------------------------------------------------------------------
# UTF-8 stdout/stderr — FIX for:
#   UnicodeDecodeError: 'charmap' codec can't decode byte 0x8f ...
# raised inside run_services.py's stream_output() thread.
#
# Root cause: on Windows, Python's default stdout/stderr encoding is
# cp1252 (the console codepage), not UTF-8. Any subprocess we spawn —
# and any library *we* import that prints unicode (e.g. modelscope's
# progress bars when PaddleX falls back to downloading a model from
# modelscope.cn) — can emit bytes cp1252 can't decode. The parent
# launcher (run_services.py) reads our stdout line-by-line and crashes
# the moment that happens, which looks like the whole worker silently
# froze.
#
# Setting PYTHONIOENCODING=utf-8 from the command line works too (see
# README Troubleshooting), but doing it here means the fix travels
# with the code itself rather than depending on the caller's shell
# environment. Wrapping the existing buffer (rather than reopening the
# stream) keeps this safe even if stdout has already been redirected
# by the parent process.
# ------------------------------------------------------------------
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for _stream_name in ("stdout", "stderr"):
        _stream = getattr(sys, _stream_name)
        if _stream is not None and hasattr(_stream, "buffer"):
            try:
                setattr(
                    sys,
                    _stream_name,
                    io.TextIOWrapper(_stream.buffer, encoding="utf-8", errors="replace"),
                )
            except Exception:
                logging.getLogger(__name__).warning(
                    "Could not force UTF-8 on sys.%s — unicode output "
                    "from this process may still crash a cp1252 reader.",
                    _stream_name,
                )

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# ------------------------------------------------------------------
# Celery app
# broker  = Redis (where jobs come from)
# backend = Redis (where results/status are stored)
# ------------------------------------------------------------------
celery_app = Celery(
    "worker",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["tasks.process"],   # tells Celery where to find the tasks
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    task_default_queue="ingestion",
    task_routes={
        "worker.tasks.process_document": {"queue": "ingestion"},
        "tasks.process.process_document": {"queue": "ingestion"},
    },
    worker_prefetch_multiplier=1,            # one job at a time per worker (CPU-heavy)
    task_acks_late=True,                     # only ack after task completes (safe retry)
    broker_connection_retry_on_startup=True, # suppress CPendingDeprecationWarning in Celery 6+
)


# ------------------------------------------------------------------
# OCR warm-up — runs once per worker process, before any task is
# pulled off the "ingestion" queue.
#
# Loads PaddleOCR (ar + en, per OCR_WARMUP_LANGS) and Surya into memory
# up front, so the first real document/image doesn't pay model-load
# latency mid-request. Mirrors the same sys.path bootstrap that
# tasks/extract.py already does for `ocr_service` (see
# _ensure_ocr_service_on_path() there) — required here too since this
# signal handler runs before tasks.process / tasks.extract are imported.
# ------------------------------------------------------------------

def _ensure_ocr_service_on_path() -> None:
    """Same two-layout search as tasks/extract.py's helper of the same name."""
    this_file = Path(__file__).resolve()

    nested_dir  = this_file.parent / "tasks" / "ocr-service"
    sibling_dir = this_file.parents[1] / "ocr-service"

    for candidate in (nested_dir, sibling_dir):
        if candidate.is_dir() and (candidate / "ocr_service").is_dir():
            candidate_str = str(candidate)
            if candidate_str not in sys.path:
                sys.path.insert(0, candidate_str)
            return

    logger.warning(
        "ocr-service package not found for OCR warm-up. Tried:\n"
        "  - %s\n"
        "  - %s\n"
        "OCR engines will still lazy-load on first use inside tasks/extract.py.",
        nested_dir, sibling_dir,
    )


@worker_process_init.connect
def _warm_up_ocr_on_worker_start(**kwargs):
    """
    Fires once when this worker process boots (before it starts consuming
    tasks). Loads PaddleOCR + Surya into the in-memory cache so they're
    already warm by the time the first document/image needing OCR arrives.

    Failure here is non-fatal: it's logged and swallowed so a slow/broken
    OCR warm-up never prevents the worker from starting and serving other
    document types (.docx, .csv, .xlsx, native-text PDFs). OCR engines
    will simply lazy-load on first use instead, exactly as before this
    change.
    """
    try:
        _ensure_ocr_service_on_path()
        from ocr_service.pipeline import warm_up_ocr_pipeline
        warm_up_ocr_pipeline()
    except Exception:
        logger.exception(
            "OCR warm-up failed at worker startup — PaddleOCR/Surya will "
            "lazy-load on first use instead."
        )


if __name__ == "__main__":
    celery_app.start()