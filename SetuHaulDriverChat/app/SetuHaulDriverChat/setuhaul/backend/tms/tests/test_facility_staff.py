from __future__ import annotations

import pytest

from setuhaul.backend.tms.exceptions import BusinessValidationError, FacilityAssignmentNotFoundError
from setuhaul.backend.tms.tests.conftest import FACILITY

STAFF_USER = "auth-user-1"
OTHER_FACILITY = "FAC-OTHER"


def test_register_staff_facility_persists_assignment(service, repository):
    result = service.register_staff_facility(STAFF_USER, FACILITY)
    assert result.staff_user_id == STAFF_USER
    assert result.facility_id == FACILITY
    assert result.facility_name == "Jaipur DC"
    assert repository.staff_facility_assignments[STAFF_USER]["facility_id"] == FACILITY


def test_register_staff_facility_rejects_unknown_facility(service):
    with pytest.raises(BusinessValidationError):
        service.register_staff_facility(STAFF_USER, "FAC-DOES-NOT-EXIST")


def test_get_staff_facility_returns_none_when_unregistered(service):
    assert service.get_staff_facility(STAFF_USER) is None


def test_require_staff_facility_raises_when_unregistered(service):
    with pytest.raises(FacilityAssignmentNotFoundError):
        service.require_staff_facility(STAFF_USER)


def test_list_shipments_for_staff_facility_requires_registration(service):
    with pytest.raises(FacilityAssignmentNotFoundError):
        service.list_shipments_for_staff_facility(STAFF_USER)


def test_list_shipments_for_staff_facility_is_scoped_to_own_facility_only(service, repository):
    # A second facility with its own shipment -- staff registered against
    # the first FACILITY must never see this one back.
    repository.facilities[OTHER_FACILITY] = {
        "facility_id": OTHER_FACILITY, "facility_name": "Delhi DC", "city": "Delhi", "state": "Delhi",
    }
    repository.shipments["SHP-OTHER"] = {
        "shipment_id": "SHP-OTHER", "driver_id": None, "vehicle_id": None,
        "destination_facility_id": OTHER_FACILITY, "product_category": "dry_freight",
        "priority_code": "NORMAL", "original_eta_ts": "2026-08-08T12:00:00+00:00",
        "expected_unload_min": 40, "current_status": "PLANNED",
        "created_at": "2026-08-08T00:00:00+00:00", "updated_at": "2026-08-08T00:00:00+00:00",
    }

    service.register_staff_facility(STAFF_USER, FACILITY)
    results = service.list_shipments_for_staff_facility(STAFF_USER)

    ids = {item.shipment_id for item in results}
    assert "SHP-OTHER" not in ids
    assert ids  # the fixture's FACILITY-scoped shipments are still visible
    assert all(item.destination_facility_id == FACILITY for item in results)
