"""
scripts/secrets_check.py
────────────────────────
Validates that all required secrets are present and not weak before
any service is started.  Called from run_services.py near the top of main().

Usage (from run_services.py):
    from scripts.secrets_check import check_secrets
    check_secrets(dict(os.environ))
"""

from __future__ import annotations

import sys


# ── Required variables ────────────────────────────────────────────────────────
REQUIRED: list[str] = [
    "POSTGRES_PASSWORD",
    "GROQ_API_KEY",
    "INTERNAL_API_KEY",
]

# ── Values that are obviously placeholders or too weak ───────────────────────
WEAK_VALUES: set[str] = {
    "",
    "changeme",
    "password",
    "postgres",
    "secret",
    "rag-internal-dev-key-change-in-prod",
    "CHANGE_ME_generate_a_32_byte_hex_token",
    "CHANGE_ME_strong_password_here",
    "CHANGE_ME_gsk_...",
}

# ── Per-variable minimum length requirements ──────────────────────────────────
MIN_LENGTH: dict[str, int] = {
    "INTERNAL_API_KEY": 32,
    "POSTGRES_PASSWORD": 4,
}


def check_secrets(env: dict[str, str], *, strict: bool = False) -> None:
    """
    Validate secrets in *env*.

    Parameters
    ----------
    env:    Mapping of environment variable names → values.
    strict: If True, treat warnings as errors (recommended for CI / production).

    Raises SystemExit(1) if any required secret is missing or weak.
    """
    errors: list[str] = []
    warnings: list[str] = []

    for key in REQUIRED:
        val = env.get(key, "").strip()

        if not val:
            errors.append(f"  [FAIL] {key} is not set")
            continue

        if val in WEAK_VALUES:
            errors.append(
                f"  [FAIL] {key} is set to a placeholder/weak value — update your .env"
            )
            continue

        min_len = MIN_LENGTH.get(key, 0)
        if len(val) < min_len:
            errors.append(
                f"  [FAIL] {key} is too short ({len(val)} chars, minimum {min_len})\n"
                f"    Generate one with:  python -c \"import secrets; print(secrets.token_hex({min_len // 2}))\""
            )

    # ── Soft warnings ─────────────────────────────────────────────────────────
    groq_key = env.get("GROQ_API_KEY", "").strip()
    if groq_key and not groq_key.startswith("gsk_"):
        warnings.append(
            "  [WARN] GROQ_API_KEY doesn't look like a Groq key (expected prefix: gsk_)"
        )

    internal_key = env.get("INTERNAL_API_KEY", "").strip()
    if internal_key and len(internal_key) < 64:
        warnings.append(
            "  [WARN] INTERNAL_API_KEY is short — recommend at least 64 chars for production"
        )

    # ── Report ────────────────────────────────────────────────────────────────
    if warnings:
        print("\nSecrets warnings:")
        for w in warnings:
            print(w)
        if strict:
            errors.extend(warnings)

    if errors:
        print("\nSecrets validation FAILED — fix these issues before starting services:")
        for e in errors:
            print(e)
        print(
            "\n  ->  Copy .env.example to .env and fill in all CHANGE_ME placeholders.\n"
            "  ->  Generate a strong INTERNAL_API_KEY with:\n"
            "       python -c \"import secrets; print(secrets.token_hex(32))\"\n"
        )
        sys.exit(1)

    print("Secrets validation passed")


# ── CLI helper ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import os
    from pathlib import Path

    # Load .env if present
    env_file = Path(__file__).parent.parent / ".env"
    env: dict[str, str] = dict(os.environ)

    if env_file.exists():
        file_env = {}
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                file_env[k.strip()] = v.strip()
        for k, v in file_env.items():
            env.setdefault(k, v)

    strict = "--strict" in sys.argv
    check_secrets(env, strict=strict)
