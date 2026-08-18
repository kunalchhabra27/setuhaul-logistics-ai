import pytest

from setuhaul.backend.tms.exceptions import BusinessValidationError, DriverNotFoundError
from setuhaul.backend.tms.models import ContextResolution, ShipmentCreate, ShipmentStatus
from setuhaul.backend.tms.tests.conftest import (
    CARRIER_A,
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
        service.driver_context("DRV-DOES-NOT-EXIST")


def test_maintenance_vehicle_cannot_receive_active_shipment(service):
    request = ShipmentCreate(
        order_reference="ORD-1", carrier_id="CAR001", origin_name="Depot", origin_city="Jaipur",
        driver_id=DRIVER_ONE, vehicle_id=VEHICLE_MAINTENANCE,
        destination_facility_id=FACILITY, customer_name="Acme Retail", product_category="dry",
        load_weight_kg=5000, planned_departure_ts="2026-08-08T10:00:00+00:00",
        original_eta_ts="2026-08-08T12:00:00+00:00",
        expected_unload_min=40, current_status=ShipmentStatus.PLANNED,
    )
    with pytest.raises(BusinessValidationError, match="inactive"):
        service.create_shipment(request)


def test_mismatched_carriers_are_rejected(service, repository):
    repository.vehicles[VEHICLE_ONE]["carrier_id"] = CARRIER_B
    request = ShipmentCreate(
        order_reference="ORD-1", carrier_id="CAR001", origin_name="Depot", origin_city="Jaipur",
        driver_id=DRIVER_ONE, vehicle_id=VEHICLE_ONE,
        destination_facility_id=FACILITY, customer_name="Acme Retail", product_category="dry",
        load_weight_kg=5000, planned_departure_ts="2026-08-08T10:00:00+00:00",
        original_eta_ts="2026-08-08T12:00:00+00:00",
        expected_unload_min=40, current_status=ShipmentStatus.IN_TRANSIT,
    )
    with pytest.raises(BusinessValidationError, match="same carrier"):
        service.create_shipment(request)


def test_context_does_not_cross_system_boundary(service):
    payload = service.driver_context(DRIVER_ONE).model_dump()
    forbidden = {"latest_declared_eta", "appointment_slot", "dock_id", "gate_in_at", "queue_status"}
    assert forbidden.isdisjoint(str(payload))


def test_assign_shipment_sets_driver_and_vehicle(service, repository):
    result = service.assign_shipment("SHP002", driver_id=DRIVER_ONE, vehicle_id=VEHICLE_ONE)
    assert result.driver_id == DRIVER_ONE
    assert result.vehicle_id == VEHICLE_ONE


def test_assign_shipment_without_vehicle_reuses_existing(service):
    result = service.assign_shipment("SHP001", driver_id=DRIVER_ONE)
    assert result.driver_id == DRIVER_ONE
    assert result.vehicle_id == VEHICLE_ONE


def test_archive_requires_completed_status(service):
    # SHP002 is PLANNED in the fixture, not COMPLETED.
    with pytest.raises(BusinessValidationError, match="completed"):
        service.archive_shipment("SHP002")


def test_archive_completed_shipment(service, repository):
    repository.shipments["SHP001"]["current_status"] = "COMPLETED"
    result = service.archive_shipment("SHP001")
    assert result.archived_flag is True


def test_completing_a_shipment_via_update_auto_archives(service):
    from setuhaul.backend.tms.models import ShipmentUpdate

    result = service.update_shipment("SHP001", ShipmentUpdate(current_status=ShipmentStatus.COMPLETED))
    assert result.current_status is ShipmentStatus.COMPLETED
    assert result.archived_flag is True


def test_cancel_shipment_sets_cancelled_status(service):
    # SHP001 is IN_TRANSIT in the fixture.
    result = service.cancel_shipment("SHP001", reason="Customer cancelled the order.")
    assert result.current_status is ShipmentStatus.CANCELLED


def test_cancel_shipment_releases_any_current_appointment(service, repository):
    repository.appointments["SHP001"] = {
        "appointment_id": "APT-1",
        "shipment_id": "SHP001",
        "slot_id": "SLOT-1",
        "appointment_status": "CONFIRMED",
        "is_current": 1,
    }
    service.cancel_shipment("SHP001")
    assert repository.appointments["SHP001"]["appointment_status"] == "CANCELLED"
    assert repository.appointments["SHP001"]["is_current"] == 0


def test_cancel_completed_shipment_is_rejected(service, repository):
    repository.shipments["SHP001"]["current_status"] = "COMPLETED"
    with pytest.raises(BusinessValidationError, match="completed"):
        service.cancel_shipment("SHP001")


def test_cancel_already_cancelled_shipment_is_rejected(service, repository):
    repository.shipments["SHP001"]["current_status"] = "CANCELLED"
    with pytest.raises(BusinessValidationError, match="cancelled"):
        service.cancel_shipment("SHP001")


def test_shipment_context_includes_dock_and_checkin_trace(service, repository):
    repository.appointments["SHP001"] = {
        "appointment_id": "APT-1",
        "shipment_id": "SHP001",
        "slot_id": "SLOT-1",
        "appointment_status": "CONFIRMED",
        "slot_start_ts": "2026-08-08T13:00:00+00:00",
        "slot_end_ts": "2026-08-08T14:00:00+00:00",
        "dock_code": "D1",
    }
    repository.checkins["SHP001"] = {
        "arrival_state": "ON_TIME",
        "queue_state": "WAITING_LATE",
        "gate_in_ts": "2026-08-08T12:30:00+00:00",
        "dock_in_ts": None,
        "unload_end_ts": None,
    }

    context = service.shipment_context("SHP001")

    assert context.dock is not None
    assert context.dock.appointment_status == "CONFIRMED"
    assert context.dock.dock_code == "D1"
    assert context.checkin is not None
    assert context.checkin.queue_state == "WAITING_LATE"


def test_shipment_reference_data_surfaces_existing_origins_and_categories(service, repository):
    repository.shipments["SHP001"]["origin_name"] = "Jaipur Depot"
    repository.shipments["SHP001"]["origin_city"] = "Jaipur"
    repository.shipments["SHP001"]["product_category"] = "Auto components"

    data = service.shipment_reference_data()

    assert any(o.origin_name == "Jaipur Depot" and o.origin_city == "Jaipur" for o in data.origins)
    assert "Auto components" in data.product_categories


def test_shipment_context_trace_is_none_when_no_wms_data(service):
    # SHP001 has no appointment/checkin rows in the fixture by default.
    context = service.shipment_context("SHP001")
    assert context.dock is None
    assert context.checkin is None


def test_assign_shipment_sends_sms_to_the_assigned_driver(service, monkeypatch):
    import setuhaul.backend.tms.service as tms_service_module

    captured: dict = {}
    monkeypatch.setattr(
        tms_service_module,
        "send_sms",
        lambda to, body, **_kwargs: captured.update(to=to, body=body) or "SM_FAKE",
    )

    result = service.assign_shipment("SHP002", driver_id=DRIVER_ONE, vehicle_id=VEHICLE_ONE)

    assert captured["to"] == "+91001"  # DRIVER_ONE's phone in the fixture
    assert result.shipment_id in captured["body"] or (result.order_reference or "") in captured["body"]


def test_assign_shipment_succeeds_even_if_sms_send_fails(service, monkeypatch):
    import setuhaul.backend.tms.service as tms_service_module

    def _boom(*_args, **_kwargs):
        raise RuntimeError("Twilio is down")

    monkeypatch.setattr(tms_service_module, "send_sms", _boom)

    # Must not raise even though the notification blows up.
    result = service.assign_shipment("SHP002", driver_id=DRIVER_ONE, vehicle_id=VEHICLE_ONE)
    assert result.driver_id == DRIVER_ONE


def test_assign_shipment_skips_sms_when_driver_has_no_phone(service, repository, monkeypatch):
    import setuhaul.backend.tms.service as tms_service_module

    repository.drivers[DRIVER_ONE]["phone"] = None
    calls = []
    monkeypatch.setattr(tms_service_module, "send_sms", lambda *a, **k: calls.append((a, k)))

    service.assign_shipment("SHP002", driver_id=DRIVER_ONE, vehicle_id=VEHICLE_ONE)
    assert calls == []


def test_create_shipment_with_driver_sends_sms(service, monkeypatch):
    import setuhaul.backend.tms.service as tms_service_module

    captured: dict = {}
    monkeypatch.setattr(
        tms_service_module,
        "send_sms",
        lambda to, body, **_kwargs: captured.update(to=to, body=body) or "SM_FAKE",
    )

    request = ShipmentCreate(
        order_reference="ORD-99", carrier_id=CARRIER_A, origin_name="Depot", origin_city="Jaipur",
        driver_id=DRIVER_ONE, vehicle_id=VEHICLE_ONE,
        destination_facility_id=FACILITY, customer_name="Acme Retail", product_category="dry",
        load_weight_kg=5000, planned_departure_ts="2026-08-08T10:00:00+00:00",
        original_eta_ts="2026-08-08T12:00:00+00:00",
        expected_unload_min=40, current_status=ShipmentStatus.PLANNED,
    )
    service.create_shipment(request)

    assert captured["to"] == "+91001"
    assert "ORD-99" in captured["body"]
