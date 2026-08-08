from __future__ import annotations

"""Backward-compatible re-exports for the shared operations repository."""

from setuhaul.backend.dock_scheduler.repository import (
    ACTIVE_APPOINTMENT_STATUSES,
    PRIORITY_WEIGHT,
    DockSchedulerRepository,
    parse_ts,
)

OperationsRepository = DockSchedulerRepository

__all__ = [
    "ACTIVE_APPOINTMENT_STATUSES",
    "DockSchedulerRepository",
    "OperationsRepository",
    "PRIORITY_WEIGHT",
    "parse_ts",
]
