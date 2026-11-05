"""Effective band selection for analytics (distinct from official IELTS overall)."""
from __future__ import annotations

from typing import Optional

from app.ielts_bands import is_valid_ielts_band


def sanitize_band(value: Optional[float]) -> Optional[float]:
    """Drop null / invalid legacy bands so they never become 0.0 in averages."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not is_valid_ielts_band(v):
        return None
    return v


def effective_result_band(
    *,
    teacher_band: Optional[float] = None,
    final_band: Optional[float] = None,
    estimated_band: Optional[float] = None,
) -> Optional[float]:
    """
    One attempt → one effective score for progress.
    Prefer teacher / final; fall back to AI estimate. Never invent 0.
    """
    for candidate in (teacher_band, final_band, estimated_band):
        cleaned = sanitize_band(candidate)
        if cleaned is not None:
            return cleaned
    return None


def average_bands(values: list[Optional[float]]) -> Optional[float]:
    """Display average to 1 decimal; None when no valid samples (not 0)."""
    clean = [sanitize_band(v) for v in values]
    clean = [v for v in clean if v is not None]
    if not clean:
        return None
    return round(sum(clean) / len(clean), 1)
