from __future__ import annotations

import pytest

from setuhaul.backend.driver_chat_eta.exceptions import BusinessValidationError
from setuhaul.backend.driver_chat_eta.models import ProfileCompleteRequest


def test_list_carriers_returns_existing_carriers_only(service):
    carriers = service.list_carriers()
    ids = {c.carrier_id for c in carriers}
    assert ids == {"CAR001", "CAR002"}


def test_list_carriers_flags_carriers_with_no_active_vehicle(service):
    # Fixture: VEH001 belongs to CAR001 and is active; CAR002 has no vehicle
    # at all -- this is exactly the gap that must be surfaced up front when a
    # driver is about to register under a carrier, not only later when TMS
    # tries to create a shipment for them.
    by_id = {c.carrier_id: c for c in service.list_carriers()}
    assert by_id["CAR001"].has_active_vehicle is True
    assert by_id["CAR002"].has_active_vehicle is False


def test_list_home_base_cities_returns_distinct_existing_cities(service, tables):
    tables["drivers"].append(
        {"driver_id": "DRV002", "carrier_id": "CAR001", "driver_name": "Other", "home_base_city": "Jaipur"}
    )
    tables["drivers"].append(
        {"driver_id": "DRV003", "carrier_id": "CAR001", "driver_name": "Third", "home_base_city": "Delhi"}
    )
    cities = service.list_home_base_cities()
    assert cities == ["Delhi", "Jaipur"]


def test_complete_profile_with_known_carrier_id_succeeds(service, principal, tables):
    request = ProfileCompleteRequest(
        driver_name="New Driver",
        phone="+91-9000099999",
        licence_number="RJ14XX9999",
        home_base_city="Jaipur",
        carrier_id="CAR002",
    )
    profile = service.complete_profile(principal, request)
    assert profile.carrier_id == "CAR002"
    assert profile.driver_name == "New Driver"


def test_complete_profile_rejects_unknown_carrier_id(service, principal):
    request = ProfileCompleteRequest(
        driver_name="New Driver",
        phone="+91-9000099999",
        licence_number="RJ14XX9999",
        home_base_city="Jaipur",
        carrier_id="CAR-DOES-NOT-EXIST",
    )
    with pytest.raises(BusinessValidationError):
        service.complete_profile(principal, request)
