#!/usr/bin/env python3
"""
Full Database and Vector Store Reset Script.

Deletes all tables from the PostgreSQL database and removes all vector
collections/chunks from the embedded Qdrant instance.

Works on any team member's laptop (Windows, macOS, Linux).

Usage:
    python delete_chunks.py          # interactive confirmation prompt
    python delete_chunks.py --yes    # skip confirmation (CI/automation)
    python delete_chunks.py -y       # same as --yes
    python delete_chunks.py --help   # show help
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

# ─── Constants ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
SCRIPTS_DIR = ROOT / "scripts"

# ANSI colours (disabled on terminals that don't support them)
_NO_COLOR = os.environ.get("NO_COLOR") or not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty()
RED = "" if _NO_COLOR else "\033[91m"
GREEN = "" if _NO_COLOR else "\033[92m"
YELLOW = "" if _NO_COLOR else "\033[93m"
BOLD = "" if _NO_COLOR else "\033[1m"
RESET = "" if _NO_COLOR else "\033[0m"


# ─── Helpers ──────────────────────────────────────────────────────────────────
def ok(msg: str) -> None:
    print(f"  {GREEN}✓{RESET}  {msg}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}!{RESET}  {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}✗{RESET}  {msg}")


def _check_venv() -> None:
    """Warn (but don't block) if the user is not inside the project venv."""
    if os.name == "nt":
        venv_python = ROOT / ".venv" / "Scripts" / "python.exe"
    else:
        venv_python = ROOT / ".venv" / "bin" / "python"

    if venv_python.exists():
        if Path(sys.executable).resolve() != venv_python.resolve():
            warn("You are NOT running inside the project virtual environment.")
            if os.name == "nt":
                warn(f"  Activate with:  .venv\\Scripts\\activate")
            else:
                warn(f"  Activate with:  source .venv/bin/activate")
            warn("Continuing anyway — some imports may fail.\n")


def _load_env() -> dict[str, str]:
    """Load .env from project root."""
    env_path = ROOT / ".env"

    if not env_path.exists():
        raise FileNotFoundError(f".env file not found at {env_path}")

    try:
        from dotenv import dotenv_values
        raw = dotenv_values(env_path)
        return {k: v for k, v in raw.items() if v is not None}
    except ImportError:
        vals = {}
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            vals[key.strip()] = value.strip()
        return vals


async def _wipe_postgresql(cfg: dict[str, str]) -> int:
    """Truncate all PostgreSQL tables. Returns count of tables wiped."""
    try:
        import asyncpg  # noqa: PLC0415
    except ImportError:
        fail("'asyncpg' is not installed.")
        fail("  Fix: .venv\\Scripts\\pip install asyncpg" if os.name == "nt"
             else "  Fix: pip install asyncpg")
        return 0

    user = cfg.get("POSTGRES_USER", "postgres")
    password = cfg.get("POSTGRES_PASSWORD", "55555")
    db = cfg.get("POSTGRES_DB", "domain_db")
    dsn = f"postgresql://{user}:{55555}@localhost:5432/{db}"

    try:
        print("\n  Connecting to PostgreSQL...")
        conn = await asyncpg.connect(dsn)
    except OSError:
        fail(f"Cannot connect to PostgreSQL at localhost:5432")
        fail("  Is PostgreSQL running? Try:")
        if os.name == "nt":
            fail("    net start postgresql-x64-16")
        else:
            fail("    sudo systemctl start postgresql")
        return 0
    except Exception as e:
        fail(f"PostgreSQL connection error: {e}")
        return 0

    tables = [
        "rag_query_logs",
        "document_chunks",
        "documents",
        "domain_configs",
        "domain_roles",
        "domains",
    ]

    wiped = 0
    try:
        print("  Truncating tables in CASCADE mode...")
        for table in tables:
            exists = await conn.fetchval(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = $1)",
                table,
            )
            if exists:
                result = await conn.execute(f"TRUNCATE TABLE {table} CASCADE")
                ok(f"Table '{table}' wiped: {result}")
                wiped += 1
            else:
                warn(f"Table '{table}' does not exist (skipped)")

        ok("PostgreSQL cleanup completed.")
    except Exception as e:
        fail(f"Error cleaning up PostgreSQL: {e}")
    finally:
        await conn.close()

    return wiped


def _wipe_qdrant(cfg: dict[str, str]) -> int:
    """Delete all Qdrant collections and storage. Returns count of collections deleted."""
    # Import qdrant_client_factory from scripts/
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        from qdrant_client_factory import sync_qdrant_client  # noqa: PLC0415, E402
    except ImportError:
        fail("Cannot import qdrant_client_factory.")
        fail(f"  Ensure '{SCRIPTS_DIR / 'qdrant_client_factory.py'}' exists.")
        fail("  And 'qdrant-client' is installed: pip install qdrant-client")
        return 0

    deleted = 0
    try:
        print("\n  Connecting to Qdrant...")
        client = sync_qdrant_client()
        collections = client.get_collections().collections
        if collections:
            print(f"  Found {len(collections)} Qdrant collection(s). Deleting...")
            for col in collections:
                client.delete_collection(col.name)
                ok(f"Deleted collection: {col.name}")
                deleted += 1
        else:
            warn("No Qdrant collections found.")
    except Exception as e:
        fail(f"Error accessing Qdrant: {e}")

    # Clean Qdrant storage directory
    qdrant_dir = cfg.get("QDRANT_PATH", "data/qdrant")
    path = Path(qdrant_dir)
    if not path.is_absolute():
        path = ROOT / path

    if path.exists():
        try:
            print(f"  Removing Qdrant storage: {path}")
            shutil.rmtree(path, ignore_errors=True)
            ok("Qdrant storage directory removed.")
        except Exception as e:
            fail(f"Error removing Qdrant directory: {e}")
    else:
        warn(f"Qdrant directory does not exist: {path} (nothing to delete)")

    return deleted


async def run(skip_confirm: bool = False) -> None:
    """Main reset routine."""
    print(f"\n{RED}{BOLD}{'=' * 70}{RESET}")
    print(f"{RED}{BOLD}  WARNING: This will WIPE the entire database and vector store!{RESET}")
    print(f"{RED}{BOLD}  All chunks, documents, domains, configs, and logs will be deleted.{RESET}")
    print(f"{RED}{BOLD}{'=' * 70}{RESET}\n")

    if not skip_confirm:
        try:
            answer = input(f"  {YELLOW}Are you sure? Type 'yes' to proceed: {RESET}").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n\n  Aborted.")
            return

        if answer not in ("yes", "y"):
            print("\n  Aborted. No data was deleted.")
            return

    cfg = _load_env()

    # 1. Wipe PostgreSQL
    tables_wiped = await _wipe_postgresql(cfg)

    # 2. Wipe Qdrant
    collections_deleted = _wipe_qdrant(cfg)

    # 3. Summary
    print(f"\n{GREEN}{BOLD}{'=' * 70}{RESET}")
    print(f"{GREEN}{BOLD}  Reset complete!{RESET}")
    print(f"  {GREEN}•{RESET} PostgreSQL tables wiped: {tables_wiped}")
    print(f"  {GREEN}•{RESET} Qdrant collections deleted: {collections_deleted}")
    print(f"{GREEN}{BOLD}{'=' * 70}{RESET}\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Wipe all PostgreSQL tables and Qdrant vector storage.",
        epilog="Run without flags for an interactive confirmation prompt.",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip the confirmation prompt (for CI/automation).",
    )
    args = parser.parse_args()

    _check_venv()

    import asyncio  # noqa: PLC0415
    asyncio.run(run(skip_confirm=args.yes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())