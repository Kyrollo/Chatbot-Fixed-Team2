import sys
import importlib.util
import time
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[2]
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))

def purge_local_modules():
    for name in list(sys.modules.keys()):
        if name in ("config", "router", "dependencies", "database", "models", "schemas", "service", "routes"):
            del sys.modules[name]
        elif name.startswith("routes.") or name.startswith("services."):
            del sys.modules[name]

def load_service(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

def load_service_app(service_name, dir_name):
    service_dir = str(ROOT / "services" / dir_name)
    sys.path.insert(0, service_dir)
    purge_local_modules()
    try:
        module = load_service(service_name, str(ROOT / "services" / dir_name / "main.py"))
        return module.app
    finally:
        if service_dir in sys.path:
            sys.path.remove(service_dir)

# Load each app individually in its own sys.path environment
domain_app = load_service_app("domain_main", "domain-service")
ingestion_app = load_service_app("ingestion_main", "ingestion-service")
retrieval_app = load_service_app("retrieval_main", "retrieval-service")
generation_app = load_service_app("generation_main", "generation-service")
evaluation_app = load_service_app("evaluation_main", "evaluation-service")

@asynccontextmanager
async def monolith_lifespan(app: FastAPI):
    # --- STARTUP ---
    print("\n[Monolith] Starting services lifespan...")
    app.state.models_ready = False
    
    # 1. Domain Startup
    sys.path.insert(0, str(ROOT / "services" / "domain-service"))
    purge_local_modules()
    try:
        from database import init_db
        print("  [Domain] Initializing DB...")
        await init_db()
    finally:
        sys.path.remove(str(ROOT / "services" / "domain-service"))

    # 2. Ingestion Startup
    sys.path.insert(0, str(ROOT / "services" / "ingestion-service"))
    purge_local_modules()
    try:
        from storage import create_tables
        print("  [Ingestion] Creating tables...")
        await create_tables()
    finally:
        sys.path.remove(str(ROOT / "services" / "ingestion-service"))

    # 3. Retrieval Startup
    sys.path.insert(0, str(ROOT / "services" / "retrieval-service"))
    purge_local_modules()
    try:
        from config import settings as ret_settings
        if ret_settings.RETRIEVAL_WARMUP_ON_START:
            from services.embedding import get_embedding_service
            from services.reranker import get_reranker_service
            
            if ret_settings.WARMUP_EMBEDDING:
                print("  [Retrieval] Loading embedding model (may take 10-30s on CPU)...")
                t0 = time.perf_counter()
                try:
                    await get_embedding_service().warmup()
                    print(f"  [Retrieval] Embedding model loaded in {time.perf_counter() - t0:.1f}s")
                except Exception as e:
                    print(f"  [Retrieval] ERROR: Embedding warmup failed: {e}")
                    
            if ret_settings.WARMUP_RERANKER and ret_settings.ENABLE_RERANKER:
                print("  [Retrieval] Loading reranker model (may take 10-30s on CPU)...")
                t0 = time.perf_counter()
                try:
                    await get_reranker_service().warmup()
                    print(f"  [Retrieval] Reranker model loaded in {time.perf_counter() - t0:.1f}s")
                except Exception as e:
                    print(f"  [Retrieval] ERROR: Reranker warmup failed: {e}")
    except Exception as e:
        print(f"  [Retrieval] ERROR: Warmup configuration/loading failed: {e}")
    finally:
        sys.path.remove(str(ROOT / "services" / "retrieval-service"))

    # 4. Generation Startup
    sys.path.insert(0, str(ROOT / "services" / "generation-service"))
    purge_local_modules()
    try:
        from router import ensure_query_log_table
        print("  [Generation] Ensuring query log tables...")
        await ensure_query_log_table()
    finally:
        sys.path.remove(str(ROOT / "services" / "generation-service"))

    # 5. Evaluation Startup
    sys.path.insert(0, str(ROOT / "services" / "evaluation-service"))
    purge_local_modules()
    try:
        from db.queries import ensure_tables_exist
        print("  [Evaluation] Bootstrapping tables...")
        ensure_tables_exist()
    except Exception as exc:
        print(f"  [Evaluation] Warning: bootstrap tables failed: {exc}")
    finally:
        sys.path.remove(str(ROOT / "services" / "evaluation-service"))

    app.state.models_ready = True
    print("[Monolith] Startup complete.\n")
    yield

    # --- SHUTDOWN ---
    print("\n[Monolith] Shutting down services lifespan...")

    # 1. Retrieval Shutdown
    sys.path.insert(0, str(ROOT / "services" / "retrieval-service"))
    purge_local_modules()
    try:
        from services.bm25_search import get_bm25_search_service
        from services.cache import get_retrieval_cache
        from services.qdrant_search import get_qdrant_search_service
        await get_retrieval_cache().close()
        await get_bm25_search_service().close()
        await get_qdrant_search_service().close()
    except Exception:
        pass
    finally:
        sys.path.remove(str(ROOT / "services" / "retrieval-service"))

    # 2. Generation Shutdown
    sys.path.insert(0, str(ROOT / "services" / "generation-service"))
    purge_local_modules()
    try:
        from router import close_router_resources as close_gen_resources
        await close_gen_resources()
    except Exception:
        pass
    finally:
        sys.path.remove(str(ROOT / "services" / "generation-service"))

    # 3. Evaluation Shutdown
    sys.path.insert(0, str(ROOT / "services" / "evaluation-service"))
    purge_local_modules()
    try:
        from router import close_router_resources as close_eval_resources
        await close_eval_resources()
    except Exception:
        pass
    finally:
        sys.path.remove(str(ROOT / "services" / "evaluation-service"))

    # 4. Domain Shutdown
    sys.path.insert(0, str(ROOT / "services" / "domain-service"))
    purge_local_modules()
    try:
        from database import dispose_engine
        await dispose_engine()
    except Exception:
        pass
    finally:
        sys.path.remove(str(ROOT / "services" / "domain-service"))

    print("[Monolith] Shutdown complete.\n")

# Main Monolith FastAPI App
app = FastAPI(
    title="RAG System Monolith Gateway",
    description="Unified API gateway hosting all RAG services on a single process.",
    version="1.0.0",
    lifespan=monolith_lifespan,
)

# Merge all routes from loaded sub-apps
for sub_app in [domain_app, ingestion_app, retrieval_app, generation_app, evaluation_app]:
    for route in sub_app.routes:
        # Exclude default OpenAPI and health routes of each sub-app to prevent collision
        if route.path in ("/openapi.json", "/docs", "/redoc", "/health", "/metrics"):
            continue
        app.routes.append(route)

# ── Setup unified metrics ──────────────────────────────────────────────────────
# Uses service_metrics (shared) to instrument ALL requests passing through the
# monolith and expose a single /metrics endpoint for Prometheus to scrape.
# Also merges evaluation-service's own Prometheus metrics (counters, gauges,
# histograms) into the same /metrics response via the shared REGISTRY.
shared_dir = str(ROOT / "services" / "shared")
sys.path.insert(0, shared_dir)
purge_local_modules()
try:
    from service_metrics import instrument_app, metrics_router
    instrument_app(app, service_name="monolith")
    app.include_router(metrics_router)
    print("  [Metrics] Monolith /metrics endpoint ready")
except Exception as exc:
    print(f"  [Metrics] Setup failed: {exc}")
finally:
    if shared_dir in sys.path:
        sys.path.remove(shared_dir)

@app.get("/health", tags=["health"])
async def root_health():
    status = "ok"
    if hasattr(app.state, "models_ready") and not app.state.models_ready:
        status = "warming_up"
    return {"status": status, "service": "monolith-service"}