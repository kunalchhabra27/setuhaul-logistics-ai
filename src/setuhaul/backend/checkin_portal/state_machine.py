"""Centralized transition rules for the check-in portal."""

ALLOWED_TRANSITIONS = {
    "GATE_IN": {"WAITING", "DOCKED"},
    "WAITING": {"DOCKED"},
    "DOCKED": {"COMPLETED"},
    "COMPLETED": set(),
}
