"""
celery_app.py
--------------
Celery app for evaluation-service, WITH Celery Beat scheduling.

This is a SEPARATE Celery app from worker-service/worker.py — different
queue name, different process, started independently. Keeping it separate
means a slow or broken evaluation run can never block or interfere with
document ingestion, and vice versa.

Beat fires evaluate_recent_answers on a timer (default: every 30 minutes)
instead of listening on a live queue — evaluation doesn't need to react
instantly to every answer the way OCR needs to react to every uploaded
document, so a scheduler is the right tool here, not a queue consumer.
"""
import os

from celery import Celery
from celery.schedules import crontab
from dotenv import load_dotenv

load_dotenv(override=False)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


# How often Beat fires the evaluation task, in minutes.
EVAL_SCHEDULE_MINUTES = int(os.getenv("EVAL_SCHEDULE_MINUTES", "30"))

celery_app = Celery(
    "evaluation",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["tasks.evaluate_batch"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    task_default_queue="evaluation",   # separate queue from worker-service's "ingestion"
    task_routes={
        "tasks.evaluate_batch.evaluate_recent_answers": {"queue": "evaluation"},
    },
    broker_connection_retry_on_startup=True,
    worker_max_tasks_per_child=1,      # recycle evaluation worker after each batch run to reclaim RAM
    worker_enable_remote_control=False,   # Don't monitor other workers' heartbeats
    worker_timer_precision=10.0,          # Less aggressive timer checking
    worker_hijack_root_logger=False,      # Prevent logger conflicts
    worker_gossip=False,                  # Disable peer-to-peer worker gossip to stop heartbeat checks
)

# ------------------------------------------------------------------
# Celery Beat schedule
# ------------------------------------------------------------------
celery_app.conf.beat_schedule = {
    "evaluate-recent-answers-every-30min": {
        "task": "tasks.evaluate_batch.evaluate_recent_answers",
        "schedule": EVAL_SCHEDULE_MINUTES * 60, # seconds — set to 300 for testing
    },
}

if __name__ == "__main__":
    celery_app.start()
