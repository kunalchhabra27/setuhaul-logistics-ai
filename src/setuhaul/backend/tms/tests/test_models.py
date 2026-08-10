import pytest
from pydantic import ValidationError

from setuhaul.backend.tms.models import (
    ACTIVE_CONTEXT_STATUSES,
    ShipmentCreate,
    ShipmentStatus,
    ShipmentUpdate,
)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (ShipmentStatus.PLANNED, True),
        (ShipmentStatus.ASSIGNED, True),
        (ShipmentStatus.IN_TRANSIT, True),
        (ShipmentStatus.AT_GATE, True),
        (ShipmentStatus.WAITING, True),
        (ShipmentStatus.IN_DOCK, True),
        (ShipmentStatus.COMPLETED, False),
        (ShipmentStatus.CANCELLED, False),
    ],
)
def test_every_shipment_status_has_explicit_active_policy(status, expected):
    assert (status in ACTIVE_CONTEXT_STATUSES) is expected


def test_shipment_create_requires_destination():
    with pytest.raises(ValidationError):
        ShipmentCreate()


def test_empty_patch_is_rejected():
    with pytest.raises(ValidationError, match="At least one field"):
        ShipmentUpdate()


def test_shipment_create_defaults_dock_type_and_status():
    request = ShipmentCreate(
        order_reference="ORD-1", carrier_id="CAR001", driver_id="DRV001",
        vehicle_id="VEH001", origin_name="Depot", destination_facility_id="FAC-JAI-01",
    )
    assert request.required_dock_type == "STANDARD"
    assert request.current_status is ShipmentStatus.PLANNED
