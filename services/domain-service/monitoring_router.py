import os
import time
import logging
import httpx
from fastapi import APIRouter, Depends
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal as async_session
from dependencies import DBSession, SystemAdmin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/monitoring", tags=["monitoring"])

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

async def get_db():
    async with async_session() as session:
        yield session

def get_redis() -> Redis | None:
    if not REDIS_URL or REDIS_URL == "memory://":
        return None
    try:
        return Redis.from_url(REDIS_URL, decode_responses=True, protocol=2)
    except Exception:
        return None

async def _health_check(url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            r = await client.get(url)
            return "healthy" if r.status_code == 200 else "unhealthy"
    except Exception:
        return "offline"

@router.get("/metrics")
async def get_metrics(
    db: DBSession,
    admin: SystemAdmin
):
    """
    Aggregates real-time metrics from Redis, Celery, PostgreSQL,
    and service health endpoints. Requires System Admin privilege.
    """
    redis_client = get_redis()
    
    # 1. Queue depth and workers
    queue_depth = 0
    active_workers = 0
    redis_hits = 0
    redis_misses = 0
    redis_mem_mb = 0.0
    
    vector_latency = 0.0
    bm25_latency = 0.0
    avg_fusion_score = 0.0
    
    llm_api_requests = 0
    llm_local_requests = 0

    if redis_client:
        try:
            # Queue depth
            queue_depth = await redis_client.llen("ingestion") or 0
            
            # Active workers (we can estimate active workers by inspecting celery events
            # or simply via celery inspect. Since domain-service doesn't run celery directly,
            # we can run a celery inspect active worker count or look for worker keys in redis)
            # A robust way is using celery's app inspect:
            try:
                from celery import Celery
                celery_app = Celery("ingestion", broker=REDIS_URL)
                inspect = celery_app.control.inspect(timeout=0.5)
                active = inspect.active()
                if active:
                    active_workers = len(active)
            except Exception:
                active_workers = 0

            # Redis stats
            info_stats = await redis_client.info("stats")
            redis_hits = int(info_stats.get("keyspace_hits", 0))
            redis_misses = int(info_stats.get("keyspace_misses", 0))
            
            info_mem = await redis_client.info("memory")
            redis_mem_mb = float(info_mem.get("used_memory", 0)) / (1024.0 * 1024.0)

            # Retrieval Latencies
            vec_count = int(await redis_client.get("rag:metrics:vector:count") or 0)
            vec_total = float(await redis_client.get("rag:metrics:vector:total_ms") or 0.0)
            vector_latency = (vec_total / vec_count) if vec_count > 0 else 0.0

            bm25_count = int(await redis_client.get("rag:metrics:bm25:count") or 0)
            bm25_total = float(await redis_client.get("rag:metrics:bm25:total_ms") or 0.0)
            bm25_latency = (bm25_total / bm25_count) if bm25_count > 0 else 0.0

            # Average Fusion Score
            fusion_count = int(await redis_client.get("rag:metrics:fusion:count") or 0)
            fusion_total = float(await redis_client.get("rag:metrics:fusion:total_score") or 0.0)
            avg_fusion_score = (fusion_total / fusion_count) if fusion_count > 0 else 0.0

            # LLM Distribution
            llm_api_requests = int(await redis_client.get("rag:metrics:llm:api") or 0)
            llm_local_requests = int(await redis_client.get("rag:metrics:llm:local") or 0)
            
        except Exception as e:
            logger.warning("Failed to collect Redis/Celery metrics: %s", e)
        finally:
            await redis_client.aclose()

    # 2. Service health checks
    # Read service URLs from environment or fall back to standard ports
    domain_url = os.getenv("DOMAIN_SERVICE_URL", "http://localhost:8001")
    ingestion_url = os.getenv("INGESTION_SERVICE_URL", "http://localhost:8002")
    retrieval_url = os.getenv("RETRIEVAL_SERVICE_URL", "http://localhost:8003")
    generation_url = os.getenv("GENERATION_SERVICE_URL", "http://localhost:8004")
    evaluation_url = os.getenv("EVALUATION_SERVICE_URL", "http://localhost:8005")

    services_health = {
        "domain": "healthy",  # Since this route is responding, domain service is healthy
        "ingestion": await _health_check(f"{ingestion_url}/health"),
        "retrieval": await _health_check(f"{retrieval_url}/health"),
        "generation": await _health_check(f"{generation_url}/generate/health"),
        "evaluation": await _health_check(f"{evaluation_url}/evaluate/health"),
    }

    # 3. Document statistics from PostgreSQL
    total_docs = 0
    processing_docs = 0
    failed_docs = 0

    try:
        res_total = await db.execute(text("SELECT COUNT(*) FROM documents"))
        total_docs = res_total.scalar_one() or 0

        res_proc = await db.execute(text("SELECT COUNT(*) FROM documents WHERE status = 'processing'"))
        processing_docs = res_proc.scalar_one() or 0

        res_fail = await db.execute(text("SELECT COUNT(*) FROM documents WHERE status = 'failed'"))
        failed_docs = res_fail.scalar_one() or 0
    except Exception as e:
        logger.warning("Failed to query document stats from PostgreSQL: %s", e)

    return {
        "queue": {
            "depth": queue_depth,
            "active_workers": active_workers,
        },
        "retrieval": {
            "vector_latency_ms": round(vector_latency, 2),
            "bm25_latency_ms": round(bm25_latency, 2),
            "avg_fusion_score": round(avg_fusion_score, 4),
        },
        "cache": {
            "hits": redis_hits,
            "misses": redis_misses,
            "memory_mb": round(redis_mem_mb, 2),
        },
        "llm": {
            "api_requests": llm_api_requests,
            "local_requests": llm_local_requests,
        },
        "services": services_health,
        "documents": {
            "total": total_docs,
            "processing": processing_docs,
            "failed": failed_docs,
        }
    }
