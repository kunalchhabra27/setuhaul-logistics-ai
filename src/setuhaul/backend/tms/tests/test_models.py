from datetime import datetime
from pathlib import Path
from runpy import run_path
from uuid import UUID

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
        (ShipmentStatus.IN_TRANSIT, True),
        (ShipmentStatus.ARRIVED, True),
        (ShipmentStatus.WAITING, True),
        (ShipmentStatus.UNLOADING, True),
        (ShipmentStatus.EXCEPTION, True),
        (ShipmentStatus.COMPLETED, False),
        (ShipmentStatus.CANCELLED, False),
    ],
)
def test_every_shipment_status_has_explicit_active_policy(status, expected):
    assert (status in ACTIVE_CONTEXT_STATUSES) is expected


def test_naive_planned_eta_is_rejected():
    with pytest.raises(ValidationError, match="timezone"):
        ShipmentCreate(
            driver_id=UUID(int=1), vehicle_id=UUID(int=2), destination_id=UUID(int=3),
            product_class="dry", priority=1, planned_eta=datetime(2026, 8, 8),
            expected_unload_minutes=40,
        )


def test_empty_patch_is_rejected():
    with pytest.raises(ValidationError, match="At least one field"):
        ShipmentUpdate()


def test_dataset_generator_is_repeatable_and_tms_only():
    root = Path(__file__).resolve().parents[5]
    generate_dataset = run_path(root / "scripts" / "generate_tms_dataset.py")["generate_dataset"]
    first = generate_dataset(driver_count=3, vehicle_count=4, shipment_count=5, seed=7)
    second = generate_dataset(driver_count=3, vehicle_count=4, shipment_count=5, seed=7)
    assert first == second
    assert first.count("GEN-DRV-") == 3
    assert first.count("GEN-VEH-") == 4
    assert first.count("50000000-") == 0
    for forbidden in ("appointments", "eta_updates", "facility_checkins", "chat_messages"):
        assert forbidden not in first
