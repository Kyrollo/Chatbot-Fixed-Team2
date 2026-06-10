"""Run document processing synchronously (local dev without Celery/Redis)."""

from __future__ import annotations

import sys

from tasks.process import process_document_sync


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python -m tasks.run_document <document_id>")
        return 1
    process_document_sync(sys.argv[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
