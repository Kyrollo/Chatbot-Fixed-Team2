import os
from celery import Celery
from dotenv import load_dotenv

load_dotenv()

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
    task_routes={
        "worker.tasks.process_document": {"queue": "ingestion"},
    },
    worker_prefetch_multiplier=1,   # one job at a time per worker (CPU-heavy)
    task_acks_late=True,            # only ack after task completes (safe retry)
)


if __name__ == "__main__":
    celery_app.start()
