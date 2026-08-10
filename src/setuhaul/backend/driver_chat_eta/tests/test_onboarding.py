from __future__ import annotations

import pytest
from pydantic import ValidationError

from setuhaul.backend.driver_chat_eta.models import ProfileCompleteRequest
from setuhaul.backend.driver_chat_eta.service import DriverChatService


class FakeRepo:
    def __init__(self):
        self.drivers = {}
        self.carriers = ["CAR001", "CAR002", "CAR003", "CAR004"]
        self.cities = ["Ahmedabad", "Delhi", "Jaipur"]

    def get_driver(self, driver_id: str):
        return self.drivers.get(driver_id)

    def get_driver_by_auth_user_id(self, auth_user_id: str):
        return next((row for row in self.drivers.values() if row.get("auth_user_id") == auth_user_id), None)

    def list_carrier_ids(self):
        return list(self.carriers)

    def list_home_base_cities(self):
        return list(self.cities)

    def list_driver_ids(self):
        return list(self.drivers)

    def upsert_driver(self, driver_id: str, payload: dict):
        row = {"driver_id": driver_id, **payload}
        self.drivers[driver_id] = row
        return row


def test_complete_profile_saves_requested_driver_id():
    repo = FakeRepo()
    repo.drivers["DRV001"] = {"driver_id": "DRV001", "auth_user_id": "other"}
    service = DriverChatService(repo)  # type: ignore[arg-type]
    request = ProfileCompleteRequest(
        driver_name="Ravi Kumar",
        phone="+91-9000000001",
        licence_number="RJ14-20241234567",
        carrier_id="CAR001",
        home_base_city="Jaipur",
    )

    driver = service.complete_profile(type("P", (), {"user_id": "auth-uuid"})(), request)

    assert driver.driver_id == "DRV002"
    assert repo.drivers["DRV002"]["carrier_id"] == "CAR001"


def test_complete_profile_rejects_invalid_licence():
    repo = FakeRepo()
    service = DriverChatService(repo)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ProfileCompleteRequest(
            driver_name="Ravi Kumar",
            phone="+91-9000000001",
            licence_number="INVALID",
            carrier_id="CAR001",
            home_base_city="Jaipur",
        )


def test_onboarding_options_surface_carriers_and_cities():
    repo = FakeRepo()
    service = DriverChatService(repo)  # type: ignore[arg-type]

    options = service.onboarding_options()

    assert options.carrier_ids == repo.carriers
    assert options.home_base_cities == repo.cities


def test_next_driver_id_skips_deleted_rows():
    repo = FakeRepo()
    repo.drivers["DRV001"] = {"driver_id": "DRV001", "driver_status": "INACTIVE"}
    repo.drivers["DRV015"] = {"driver_id": "DRV015", "driver_status": "INACTIVE"}
    service = DriverChatService(repo)  # type: ignore[arg-type]

    assert service._next_driver_id() == "DRV016"
