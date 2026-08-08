from uuid import UUID

import pytest

from setuhaul.backend.tms.exceptions import BusinessValidationError, DriverNotFoundError
from setuhaul.backend.tms.models import (
    ContextResolution,
    ShipmentCreate,
    ShipmentStatus,
)
from setuhaul.backend.tms.tests.conftest import (
    CARRIER_B,
    DRIVER_AMBIGUOUS,
    DRIVER_EMPTY,
    DRIVER_INACTIVE,
    DRIVER_ONE,
    FACILITY,
    VEHICLE_MAINTENANCE,
    VEHICLE_ONE,
)


def test_single_active_shipment_resolves(service):
    context = service.driver_context(DRIVER_ONE)
    assert context.resolution is ContextResolution.RESOLVED
    assert context.requires_disambiguation is False
    assert len(context.active_shipments) == 1


def test_multiple_active_shipments_require_disambiguation(service):
    context = service.driver_context(DRIVER_AMBIGUOUS)
    assert context.resolution is ContextResolution.AMBIGUOUS
    assert context.requires_disambiguation is True
    assert len(context.active_shipments) == 2


def test_driver_without_shipments_returns_not_found_resolution(service):
    assert service.driver_context(DRIVER_EMPTY).resolution is ContextResolution.NOT_FOUND


def test_inactive_driver_returns_no_context(service):
    context = service.driver_context(DRIVER_INACTIVE)
    assert context.resolution is ContextResolution.NOT_FOUND
    assert context.active_shipments == []


def test_unknown_driver_raises_404_domain_error(service):
    with pytest.raises(DriverNotFoundError):
        service.driver_context(UUID(int=999))


def test_maintenance_vehicle_cannot_receive_active_shipment(service):
    request = ShipmentCreate(
        driver_id=DRIVER_ONE, vehicle_id=VEHICLE_MAINTENANCE,
        destination_id=FACILITY, product_class="dry", priority=1,
        expected_unload_minutes=40, status=ShipmentStatus.PLANNED,
    )
    with pytest.raises(BusinessValidationError, match="maintenance"):
        service.create_shipment(request)


def test_mismatched_carriers_are_rejected(service, repository):
    repository.vehicles[VEHICLE_ONE]["carrier_id"] = str(CARRIER_B)
    request = ShipmentCreate(
        driver_id=DRIVER_ONE, vehicle_id=VEHICLE_ONE,
        destination_id=FACILITY, product_class="dry", priority=1,
        expected_unload_minutes=40, status=ShipmentStatus.IN_TRANSIT,
    )
    with pytest.raises(BusinessValidationError, match="same carrier"):
        service.create_shipment(request)


def test_context_does_not_cross_system_boundary(service):
    payload = service.driver_context(DRIVER_ONE).model_dump()
    forbidden = {"latest_declared_eta", "appointment_slot", "dock_id", "gate_in_at", "queue_status"}
    assert forbidden.isdisjoint(str(payload))
