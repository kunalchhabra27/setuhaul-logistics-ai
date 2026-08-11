from __future__ import annotations

from setuhaul.backend._testing.fake_supabase import FakeSupabaseClient
from setuhaul.backend.tms.repository import TMSRepository


def test_create_driver_generates_sequential_id_from_existing_rows() -> None:
    repo = TMSRepository(
        FakeSupabaseClient(
            {
                "drivers": [
                    {"driver_id": "DRV001", "carrier_id": "CAR001", "driver_name": "A"},
                    {"driver_id": "DRV002", "carrier_id": "CAR001", "driver_name": "B"},
                ]
            }
        )
    )
    row = repo.create_driver({"carrier_id": "CAR001", "driver_name": "New Driver"})
    assert row["driver_id"] == "DRV003"


def test_create_driver_starts_at_one_on_an_empty_table() -> None:
    repo = TMSRepository(FakeSupabaseClient({"drivers": []}))
    row = repo.create_driver({"carrier_id": "CAR001", "driver_name": "First Driver"})
    assert row["driver_id"] == "DRV001"


def test_create_driver_ignores_non_matching_id_formats() -> None:
    repo = TMSRepository(
        FakeSupabaseClient({"drivers": [{"driver_id": "LEGACY-ID", "carrier_id": "CAR001"}]})
    )
    row = repo.create_driver({"carrier_id": "CAR001", "driver_name": "New Driver"})
    assert row["driver_id"] == "DRV001"


def test_create_vehicle_generates_sequential_id() -> None:
    repo = TMSRepository(
        FakeSupabaseClient({"vehicles": [{"vehicle_id": "VEH001", "carrier_id": "CAR001"}]})
    )
    row = repo.create_vehicle({"carrier_id": "CAR001", "registration_number": "RJ01AA0009"})
    assert row["vehicle_id"] == "VEH002"


def test_generate_shipment_id_continues_seed_style_numbering() -> None:
    repo = TMSRepository(
        FakeSupabaseClient(
            {
                "shipments": [
                    {"shipment_id": "SHP1001"},
                    {"shipment_id": "SHP1002"},
                ]
            }
        )
    )
    assert repo.generate_shipment_id() == "SHP1003"


def test_generate_shipment_id_preserves_wider_digit_widths() -> None:
    repo = TMSRepository(FakeSupabaseClient({"shipments": [{"shipment_id": "SHP12345"}]}))
    assert repo.generate_shipment_id() == "SHP12346"


def test_get_staff_facility_returns_none_when_missing() -> None:
    repo = TMSRepository(FakeSupabaseClient({"staff_facility_assignments": []}))
    assert repo.get_staff_facility("auth-user-1") is None


def test_register_staff_facility_then_get_staff_facility_round_trip() -> None:
    repo = TMSRepository(FakeSupabaseClient({"staff_facility_assignments": []}))
    created = repo.register_staff_facility("auth-user-1", "FAC-1")
    assert created["staff_user_id"] == "auth-user-1"
    assert created["facility_id"] == "FAC-1"

    fetched = repo.get_staff_facility("auth-user-1")
    assert fetched["facility_id"] == "FAC-1"


def test_list_shipment_reference_data_deduplicates_origins_and_categories() -> None:
    repo = TMSRepository(
        FakeSupabaseClient(
            {
                "shipments": [
                    {"origin_name": "Jaipur Depot", "origin_city": "Jaipur", "product_category": "Auto components"},
                    {"origin_name": "Jaipur Depot", "origin_city": "Jaipur", "product_category": "Auto components"},
                    {"origin_name": "Delhi Depot", "origin_city": "Delhi", "product_category": "FMCG"},
                    {"origin_name": None, "origin_city": None, "product_category": None},
                ]
            }
        )
    )
    data = repo.list_shipment_reference_data()
    assert data["origins"] == [
        {"origin_name": "Delhi Depot", "origin_city": "Delhi"},
        {"origin_name": "Jaipur Depot", "origin_city": "Jaipur"},
    ]
    assert data["product_categories"] == ["Auto components", "FMCG"]


def test_register_staff_facility_upserts_on_re_registration() -> None:
    repo = TMSRepository(
        FakeSupabaseClient(
            {"staff_facility_assignments": [{"staff_user_id": "auth-user-1", "facility_id": "FAC-1"}]}
        )
    )
    repo.register_staff_facility("auth-user-1", "FAC-2")
    fetched = repo.get_staff_facility("auth-user-1")
    assert fetched["facility_id"] == "FAC-2"
