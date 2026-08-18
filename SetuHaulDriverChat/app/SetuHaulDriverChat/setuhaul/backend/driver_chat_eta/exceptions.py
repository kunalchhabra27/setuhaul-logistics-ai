"""Domain exceptions for the driver chat & ETA backend."""

from __future__ import annotations


class DriverChatError(Exception):
    """Base error carrying an HTTP status and stable machine code."""

    status_code = 400
    code = "DRIVER_CHAT_ERROR"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class AuthenticationError(DriverChatError):
    status_code = 401
    code = "AUTHENTICATION_REQUIRED"


class AuthorizationError(DriverChatError):
    status_code = 403
    code = "FORBIDDEN"


class DriverProfileNotFoundError(DriverChatError):
    status_code = 404
    code = "DRIVER_PROFILE_NOT_FOUND"


class ShipmentNotFoundError(DriverChatError):
    status_code = 404
    code = "SHIPMENT_NOT_FOUND"


class MultipleActiveShipmentsError(DriverChatError):
    """Raised instead of silently picking one when a driver has more than
    one active shipment and an action/state-changing method needs exactly
    one to act on (report a delay, book/cancel a dock slot, check request
    status, update arrival check-in, etc). Callers -- both the regex
    fallback and the LLM tool wrappers -- turn this into a clarification
    question for the driver rather than treating it like any other error."""

    status_code = 409
    code = "MULTIPLE_ACTIVE_SHIPMENTS"


class SlotNotFoundError(DriverChatError):
    status_code = 404
    code = "SLOT_NOT_FOUND"


class ConflictError(DriverChatError):
    status_code = 409
    code = "RESOURCE_CONFLICT"


class SlotConflictError(ConflictError):
    code = "SLOT_CONFLICT"


class BusinessValidationError(DriverChatError):
    status_code = 422
    code = "BUSINESS_VALIDATION_FAILED"


class PersistenceError(DriverChatError):
    status_code = 500
    code = "PERSISTENCE_ERROR"
