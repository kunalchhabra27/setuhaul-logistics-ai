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

    def list_drivers_for_phone_lookup(self):
        return [
            {"driver_id": row["driver_id"], "phone": row.get("phone"), "auth_user_id": row.get("auth_user_id")}
            for row in self.drivers.values()
        ]

    def upsert_driver(self, driver_id: str, payload: dict):
        row = {"driver_id": driver_id, **payload}
        self.drivers[driver_id] = row
        return row


def make_principal(user_id: str, *, driver_id: str | None = None, access_token: str = "token"):
    return type("P", (), {"user_id": user_id, "driver_id": driver_id, "access_token": access_token})()


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

    driver = service.complete_profile(make_principal("auth-uuid"), request)

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


def test_complete_profile_links_auth_user_id_so_relogin_finds_the_driver():
    """Regression test: a driver completing onboarding must be findable by
    ``get_my_profile`` afterwards (i.e. on their next login), not treated as
    a brand-new user every time."""
    repo = FakeRepo()
    service = DriverChatService(repo)  # type: ignore[arg-type]
    principal = make_principal("auth-uuid-123")
    request = ProfileCompleteRequest(
        driver_name="Ravi Kumar",
        phone="+91-9000000001",
        licence_number="RJ14-20241234567",
        carrier_id="CAR001",
        home_base_city="Jaipur",
    )

    created = service.complete_profile(principal, request)

    assert repo.drivers[created.driver_id]["auth_user_id"] == "auth-uuid-123"

    fetched = service.get_my_profile(principal)
    assert fetched.driver_id == created.driver_id


def test_get_my_profile_resolves_instantly_via_linked_driver_id():
    """Once a Supabase user_metadata link exists (principal.driver_id), the
    profile is fetched directly -- no auth_user_id column dependency at all,
    so this works even before that migration is applied."""
    repo = FakeRepo()
    repo.drivers["DRV020"] = {"driver_id": "DRV020", "driver_name": "Shiva", "phone": "9876543456"}
    service = DriverChatService(repo)  # type: ignore[arg-type]

    profile = service.get_my_profile(make_principal("auth-uuid-shiva", driver_id="DRV020"))

    assert profile.driver_id == "DRV020"


def test_complete_profile_claims_existing_unlinked_driver_by_phone_instead_of_duplicating():
    """Regression test for the 'already has a valid driver profile' bug:
    dispatch (via TMS) pre-created a driver row with a phone number but no
    auth link. When that same person completes onboarding, they must be
    linked to their existing row -- not given a second, shipment-less one."""
    repo = FakeRepo()
    repo.drivers["DRV020"] = {
        "driver_id": "DRV020",
        "driver_name": "Shiva",
        "phone": "9876543456",
        "carrier_id": "CAR003",
        "home_base_city": "Jaipur",
    }
    service = DriverChatService(repo)  # type: ignore[arg-type]
    request = ProfileCompleteRequest(
        driver_name="Shiva",
        phone="9876543456",
        licence_number="RJ14 2022 001594".replace(" ", "-"),
        carrier_id="CAR003",
        home_base_city="Jaipur",
    )

    result = service.complete_profile(make_principal("auth-uuid-shiva"), request)

    assert result.driver_id == "DRV020"
    assert len(repo.drivers) == 1
    assert repo.drivers["DRV020"]["auth_user_id"] == "auth-uuid-shiva"


def test_complete_profile_does_not_claim_a_phone_already_linked_to_someone_else():
    repo = FakeRepo()
    repo.drivers["DRV020"] = {
        "driver_id": "DRV020",
        "phone": "9876543456",
        "auth_user_id": "auth-uuid-original-owner",
    }
    service = DriverChatService(repo)  # type: ignore[arg-type]
    request = ProfileCompleteRequest(
        driver_name="Impersonator",
        phone="9876543456",
        licence_number="RJ14-20241234567",
        carrier_id="CAR001",
        home_base_city="Jaipur",
    )

    result = service.complete_profile(make_principal("auth-uuid-attacker"), request)

    assert result.driver_id != "DRV020"
    assert repo.drivers["DRV020"]["auth_user_id"] == "auth-uuid-original-owner"
