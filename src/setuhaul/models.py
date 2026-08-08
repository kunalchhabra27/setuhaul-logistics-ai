from __future__ import annotations

"""Shared domain models — canonical definitions live in dock_scheduler."""

from setuhaul.backend.dock_scheduler.models import (
    DriverConstraints,
    SlotLifecycleStage,
    SlotSuggestion,
    SuggestionType,
)

__all__ = [
    "DriverConstraints",
    "SlotLifecycleStage",
    "SlotSuggestion",
    "SuggestionType",
]
