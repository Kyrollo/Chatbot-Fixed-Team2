"""
tasks/moderation.py
----------------------
Decides whether an evaluated answer needs a human to look at it.

Kept in its own file (not inline in evaluate_batch.py) because this logic is
likely to grow — e.g. later you might also flag based on keywords, domain, or
repeated low scores from the same user — and isolating it here means
evaluate_batch.py never needs to change when the flagging rules do.
"""


def should_flag_for_moderation(overall_score: float, threshold: float) -> bool:
    """
    Simple threshold check: anything strictly below `threshold` gets queued
    for human review.

    `threshold` is passed in (from db.queries.MODERATION_THRESHOLD, which
    reads MODERATION_THRESHOLD from .env) rather than hardcoded here so it
    can be tuned without touching code.
    """
    return overall_score < threshold