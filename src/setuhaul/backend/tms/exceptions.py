"""Domain exceptions and stable TMS error metadata."""

from __future__ import annotations


class TMSError(Exception):
    """Base error carrying an HTTP status and stable machine code."""

    status_code = 400
    code = "TMS_ERROR"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class AuthenticationError(TMSError):
    status_code = 401
    code = "AUTHENTICATION_REQUIRED"


class AuthorizationError(TMSError):
    status_code = 403
    code = "FORBIDDEN"


class ResourceNotFoundError(TMSError):
    status_code = 404
    code = "RESOURCE_NOT_FOUND"


class DriverNotFoundError(ResourceNotFoundError):
    code = "DRIVER_NOT_FOUND"


class VehicleNotFoundError(ResourceNotFoundError):
    code = "VEHICLE_NOT_FOUND"


class ShipmentNotFoundError(ResourceNotFoundError):
    code = "SHIPMENT_NOT_FOUND"


class FacilityAssignmentNotFoundError(ResourceNotFoundError):
    """Raised when a WMS/Check-in staff account has no registered facility yet."""

    code = "FACILITY_ASSIGNMENT_NOT_FOUND"


class ConflictError(TMSError):
    status_code = 409
    code = "RESOURCE_CONFLICT"


class BusinessValidationError(TMSError):
    status_code = 422
    code = "BUSINESS_VALIDATION_FAILED"


class PersistenceError(TMSError):
    status_code = 500
    code = "PERSISTENCE_ERROR"
