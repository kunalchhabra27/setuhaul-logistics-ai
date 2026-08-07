"""Exceptions for the check-in portal domain."""


class InvalidCheckInTransition(Exception):
    """Raised when a check-in transition violates the allowed state flow."""
