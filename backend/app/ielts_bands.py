"""Shared IELTS band helpers — validation and half-up rounding."""
from __future__ import annotations

import math
from typing import Optional

from fastapi import HTTPException


def ielts_round_half_up(value: float) -> float:
    """Round to nearest 0.5 using half-up (IELTS-style for positive scores)."""
    if value < 0:
        return 0.0
    return math.floor(value * 2 + 0.5) / 2.0


def is_valid_ielts_band(value: float) -> bool:
    """True for 0–9 inclusive in 0.5 steps."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    if v < 0 or v > 9:
        return False
    # Allow floating noise around half steps.
    return abs(v * 2 - round(v * 2)) < 1e-6


def parse_ielts_band(raw, *, field: str = "band") -> Optional[float]:
    """Parse optional band; raise 400 on invalid non-null values."""
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, f"{field} must be numeric.") from exc
    value = ielts_round_half_up(value)
    if not is_valid_ielts_band(value):
        raise HTTPException(400, f"{field} must be between 0 and 9 in 0.5 steps.")
    return value


def compute_overall_band(
    listening: Optional[float] = None,
    reading: Optional[float] = None,
    writing: Optional[float] = None,
    speaking: Optional[float] = None,
) -> Optional[float]:
    """Average of available skill bands, then round to nearest 0.5."""
    bands = [float(b) for b in (listening, reading, writing, speaking) if b is not None]
    if not bands:
        return None
    return ielts_round_half_up(sum(bands) / len(bands))


def single_skill_result_label(mock_type: str | None) -> str:
    """Human label for single-skill mock results (not four-skill overall IELTS)."""
    mt = (mock_type or "").strip().lower()
    labels = {
        "reading": "Reading band",
        "listening": "Listening band",
        "writing": "Writing band",
        "speaking": "Speaking band",
    }
    return labels.get(mt, "Skill band")
