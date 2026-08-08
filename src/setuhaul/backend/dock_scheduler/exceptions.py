from __future__ import annotations


class DockSchedulerError(Exception):
    """Base exception for dock scheduler failures."""


class UnknownShipmentError(DockSchedulerError):
    """Raised when the requested shipment is not known."""


class SlotUnavailableError(DockSchedulerError):
    """Raised when a slot is no longer available for booking."""


class InvalidBookingError(DockSchedulerError):
    """Raised when a booking request does not satisfy scheduler rules."""
