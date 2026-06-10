"""SSL bootstrap for local dev (HuggingFace model downloads on Windows)."""

from __future__ import annotations

try:
    import truststore

    truststore.inject_into_ssl()
except Exception:
    pass
