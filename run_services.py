#!/usr/bin/env python3
"""
Launch full RAG stack locally (no Docker).

Startup order:
  1. Keycloak (auth) on http://localhost:8180
  2. Redis on localhost:6379
  3. FastAPI services on ports 8001-8004 (+ optional 8005)
  4. Celery worker — only when --worker flag is given

Usage:
    python run_services.py                 # APIs + infra only (no worker)
    python run_services.py --worker        # also start Celery ingestion worker
    python run_services.py --evaluation    # also start evaluation-service on 8005
    python run_services.py --no-reload     # disable uvicorn --reload
    python run_services.py --skip-infra    # if Redis/Keycloak already running
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(SCRIPTS))
import network_bootstrap  # noqa: E402, F401
from infra_manager import (  # noqa: E402
    keycloak_ready,
    redis_ping,
    start_all_infra,
)


def resolve_python() -> str:
    if os.name == "nt":
        venv_python = ROOT / ".venv" / "Scripts" / "python.exe"
    else:
        venv_python = ROOT / ".venv" / "bin" / "python"
    if venv_python.exists() and Path(sys.executable).resolve() != venv_python.resolve():
        return str(venv_python)
    return sys.executable


PYTHON = resolve_python()


def ensure_python_runtime() -> None:
    check = subprocess.run([PYTHON, "-c", "import uvicorn"], capture_output=True, text=True)
    if check.returncode != 0:
        print("ERROR: uvicorn is not installed.")
        print(f"  Python: {PYTHON}")
        raise SystemExit(1)


def apply_local_env(env: dict[str, str], *, use_keycloak: bool, use_redis: bool) -> dict[str, str]:
    out = dict(env)

    user     = out.get("POSTGRES_USER", "postgres")
    password = quote(out.get("POSTGRES_PASSWORD", "postgres"), safe="")
    db       = out.get("POSTGRES_DB", "domain_db")
    out["DATABASE_URL"]      = f"postgresql+asyncpg://{user}:{password}@localhost:5432/{db}"
    out["SYNC_DATABASE_URL"] = f"postgresql://{user}:{password}@localhost:5432/{db}"

    if use_redis:
        out["REDIS_URL"] = "redis://localhost:6379/0"
        out.pop("SYNC_INGESTION", None)
    else:
        out["REDIS_URL"]        = "memory://"
        out["SYNC_INGESTION"]   = "1"

    # Use Qdrant Docker server instead of embedded file storage
    out["QDRANT_URL"] = "http://localhost:6333"
    out.pop("QDRANT_PATH", None)

    out["DOMAIN_SERVICE_URL"]     = "http://localhost:8001"
    out["INGESTION_SERVICE_URL"]  = "http://localhost:8002"
    out["RETRIEVAL_SERVICE_URL"]  = "http://localhost:8003"
    out["GENERATION_SERVICE_URL"] = "http://localhost:8004"
    out["EVALUATION_SERVICE_URL"] = "http://localhost:8005"
    out["UPLOAD_DIR"]             = str(ROOT / "data" / "uploads")
    out.setdefault("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    out["PYTHONPATH"] = os.pathsep.join(
        p for p in [str(SCRIPTS), out.get("PYTHONPATH", "")] if p
    )
    out.setdefault("PYTHONIOENCODING", "utf-8")

    from dev_auth import DEV_ISSUER, get_public_key_body  # noqa: PLC0415

    out["KEYCLOAK_ISSUER"]     = DEV_ISSUER
    out["KEYCLOAK_REALM_URL"]  = DEV_ISSUER
    out["KEYCLOAK_PUBLIC_KEY"] = get_public_key_body()

    out.setdefault("INTERNAL_API_KEY",    "rag-internal-dev-key-change-in-prod")
    out.setdefault("OPENBLAS_NUM_THREADS", "1")
    out.setdefault("OMP_NUM_THREADS",      "1")
    out.setdefault("MKL_NUM_THREADS",      "1")
    out.setdefault("NUMEXPR_NUM_THREADS",  "1")
    # Prevent PyTorch from probing/loading CUDA DLLs — saves ~200 MB per process.
    out.setdefault("CUDA_VISIBLE_DEVICES", "")
    out.setdefault("PYTORCH_JIT",          "0")
    return out


API_SERVICES = [
    {"name": "domain-service",     "dir": ROOT / "services" / "domain-service",     "port": 8001, "app": "main:app"},
    {"name": "ingestion-service",  "dir": ROOT / "services" / "ingestion-service",  "port": 8002, "app": "main:app"},
    {"name": "retrieval-service",  "dir": ROOT / "services" / "retrieval-service",  "port": 8003, "app": "main:app"},
    {"name": "generation-service", "dir": ROOT / "services" / "generation-service", "port": 8004, "app": "main:app"},
]

EVALUATION_SERVICE = {
    "name": "evaluation-service",
    "dir":  ROOT / "services" / "evaluation-service",
    "port": 8005,
    "app":  "main:app",
}

WORKER = {"name": "worker-service", "dir": ROOT / "services" / "worker-service"}


def worker_cmd() -> list[str]:
    cmd = [
        PYTHON, "-m", "celery", "-A", "worker", "worker",
        "--loglevel=info", "-Q", "ingestion", "--concurrency=1",
    ]
    if os.name == "nt":
        cmd.extend(["--pool=solo"])
    return cmd


def load_root_env(use_keycloak: bool, use_redis: bool) -> dict[str, str]:
    env = os.environ.copy()
    dotenv_path = ROOT / ".env"
    if dotenv_path.exists():
        for line in dotenv_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() not in env:
                env[key.strip()] = value.strip()
    return apply_local_env(env, use_keycloak=use_keycloak, use_redis=use_redis)


def start_uvicorn(service: dict, env: dict[str, str], reload: bool) -> subprocess.Popen:
    Path(env["UPLOAD_DIR"]).mkdir(parents=True, exist_ok=True)
    cmd = [PYTHON, "-m", "uvicorn", service["app"], "--host", "0.0.0.0", "--port", str(service["port"])]
    if reload:
        cmd.append("--reload")
    print(f"  -> {service['name']} on http://localhost:{service['port']}")
    return subprocess.Popen(
        cmd, cwd=service["dir"], env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )


def start_worker(env: dict[str, str]) -> subprocess.Popen:
    print("  -> worker-service (Celery, queue: ingestion)")
    return subprocess.Popen(
        worker_cmd(), cwd=WORKER["dir"], env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )


def purge_ingestion_queue() -> None:
    """Remove stale Celery tasks from the Redis ingestion queue."""
    try:
        import redis as redis_lib  # noqa: PLC0415

        r = redis_lib.Redis(host="localhost", port=6379, db=0, socket_timeout=5)
        count = r.llen("ingestion")
        r.delete("ingestion", "unacked", "unacked_index", "unacked_mutex")
        if count:
            print(f"  Purged {count} stale task(s) from ingestion queue")
        else:
            print("  Ingestion queue is clean")
        r.close()
    except Exception as exc:
        print(f"  Warning: could not purge ingestion queue: {exc}")


def stream_output(proc: subprocess.Popen, prefix: str) -> None:
    if proc.stdout is None:
        return
    for line in proc.stdout:
        print(f"[{prefix}] {line}", end="", flush=True)


def attach_output_logger(name: str, proc: subprocess.Popen) -> None:
    threading.Thread(target=stream_output, args=(proc, name), daemon=True).start()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run full RAG stack locally (no Docker).")
    parser.add_argument(
        "--worker",
        action="store_true",
        help="Also start the Celery ingestion worker (off by default — loads PyTorch/~3 GB DLLs)",
    )
    parser.add_argument("--evaluation", action="store_true", help="Also start evaluation-service on port 8005")
    parser.add_argument("--no-reload",  action="store_true", help="Disable uvicorn --reload")
    parser.add_argument("--skip-infra", action="store_true", help="Skip starting Redis/Keycloak")
    args = parser.parse_args()
    ensure_python_runtime()

    infra_processes: list[tuple[str, subprocess.Popen]] = []
    use_keycloak = keycloak_ready()
    use_redis    = redis_ping()

    print("=" * 60)
    print("  RAG System — Full Local Stack Launcher")
    print("=" * 60)

    if not args.skip_infra:
        print("\n[1/3] Starting infrastructure (auth + Redis)...")
        infra_processes, redis_started, keycloak_started = start_all_infra()
        use_redis    = use_redis    or redis_started
        use_keycloak = use_keycloak or keycloak_started
        attach_output_logger("keycloak", infra_processes[0][1])
        if len(infra_processes) > 1 and infra_processes[1][0] == "redis":
            attach_output_logger("redis", infra_processes[1][1])
    else:
        use_keycloak = keycloak_ready()
        use_redis    = redis_ping()

    env      = load_root_env(use_keycloak=use_keycloak, use_redis=use_redis)
    services = list(API_SERVICES)
    if args.evaluation:
        services.append(EVALUATION_SERVICE)

    print(f"\n[2/3] Configuration")
    print(f"  Python:     {PYTHON}")
    print(f"  PostgreSQL: localhost:5432")
    print(f"  Qdrant:     server at {env['QDRANT_URL']}")
    print(f"  Auth:       {'Keycloak http://localhost:8180' if use_keycloak else 'dev JWT fallback'}")
    print(f"  Redis:      {'localhost:6379' if use_redis else 'unavailable (sync ingestion + memory cache)'}")
    if not args.worker:
        print(f"  Worker:     disabled (pass --worker to enable Celery ingestion)")

    print(f"\n[3/3] Starting API services{' + worker' if args.worker else ''}...")

    processes: list[tuple[str, subprocess.Popen]] = list(infra_processes)
    try:
        for svc in services:
            proc = start_uvicorn(svc, env, reload=not args.no_reload)
            attach_output_logger(svc["name"], proc)
            processes.append((svc["name"], proc))
            time.sleep(5)

        if args.worker and use_redis:
            purge_ingestion_queue()
            time.sleep(10)
            worker_proc = start_worker(env)
            attach_output_logger(WORKER["name"], worker_proc)
            processes.append((WORKER["name"], worker_proc))

        print("\n" + "=" * 60)
        print("  All processes started. Press Ctrl+C to stop.")
        print("=" * 60)
        if use_keycloak:
            print("  Keycloak:  http://localhost:8180")
        for svc in services:
            print(f"  API docs:  http://localhost:{svc['port']}/docs")
        if args.worker:
            print("\n  Test: python scripts/full_cycle_test.py")
        else:
            print("\n  Note: Celery worker not running.")
            print("  Start with:  python run_services.py --worker")
            print("  Or test APIs: python scripts/timed_e2e_test.py")
        print()

        while True:
            for name, proc in processes:
                if proc.poll() is not None:
                    ended = proc.args[-1] if proc.args else ""
                    if ended in {"redis-already-running", "keycloak-already-running"}:
                        continue
                    print(f"\n[ERROR] {name} exited with code {proc.returncode}")
                    return 1
            time.sleep(2)

    except KeyboardInterrupt:
        print("\n\nShutting down...")
    finally:
        for name, proc in reversed(processes):
            if proc.poll() is None:
                proc.terminate()
        for name, proc in reversed(processes):
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                print(f"  Killed {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())