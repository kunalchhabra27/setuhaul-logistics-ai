"""Centralized transition rules for the check-in portal."""

from __future__ import annotations

from setuhaul.backend.checkin_portal.exceptions import InvalidCheckInTransition

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "GATE_IN": {"WAITING", "DOCKED"},
    "WAITING": {"DOCKED"},
    "DOCKED": {"COMPLETED"},
    "COMPLETED": set(),
}


def validate_transition(current_status: str, target_status: str) -> None:
    """Raise when a requested transition is not allowed."""
    allowed = ALLOWED_TRANSITIONS.get(current_status, set())
    if target_status not in allowed:
        raise InvalidCheckInTransition(
            f"Invalid transition: {current_status} -> {target_status}"
        )
