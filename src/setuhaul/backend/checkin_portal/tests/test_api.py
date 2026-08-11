from fastapi.testclient import TestClient

from setuhaul.backend.checkin_portal.api import get_service
from setuhaul.backend.tms.models import TMSRole
from setuhaul.infrastructure.auth import Principal, get_current_principal, require_admin
from setuhaul.main import app

from setuhaul.backend.checkin_portal.tests.test_service import _service


def _principal(role: TMSRole, facility_id: str | None = "FAC-1") -> Principal:
    return Principal(user_id="checkin-user", role=role, access_token="test-token", facility_id=facility_id)


def test_list_shipments_returns_checkin_read_model() -> None:
    service = _service()
    app.dependency_overrides[get_service] = lambda: service
    app.dependency_overrides[get_current_principal] = lambda: _principal(TMSRole.ADMIN_1)
    try:
        response = TestClient(app).get("/api/v1/checkins/shipments")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    body = response.json()
    assert body[0]["shipment_id"] == "SHP1006"
    assert body[0]["driver_name"] == "Rajesh Kumar"
    assert body[0]["can_gate_in"] is True


def test_list_shipments_returns_empty_without_assigned_facility() -> None:
    service = _service()
    app.dependency_overrides[get_service] = lambda: service
    app.dependency_overrides[get_current_principal] = lambda: _principal(TMSRole.ADMIN_1, facility_id=None)
    try:
        response = TestClient(app).get("/api/v1/checkins/shipments")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == []


def test_list_facility_options_returns_active_warehouses(monkeypatch) -> None:
    service = _service(
        {
            "facilities": [
                {"facility_id": "FAC-JAI-01", "facility_name": "Jaipur DC", "city": "Jaipur", "state": "Rajasthan", "active_flag": 1},
                {"facility_id": "FAC-GGN-01", "facility_name": "Gurugram Cross-Dock", "city": "Gurugram", "state": "Haryana", "active_flag": 1},
                {"facility_id": "FAC-OLD-01", "facility_name": "Old DC", "city": "Old", "state": "Old", "active_flag": 0},
            ]
        }
    )
    monkeypatch.setattr("setuhaul.backend.checkin_portal.api.get_public_service", lambda: service)

    response = TestClient(app).get("/api/v1/checkins/facilities/options")

    assert response.status_code == 200
    assert [row["facility_id"] for row in response.json()] == ["FAC-GGN-01", "FAC-JAI-01"]


def test_admin_can_gate_check_in() -> None:
    service = _service()
    app.dependency_overrides[get_service] = lambda: service
    app.dependency_overrides[require_admin] = lambda: _principal(TMSRole.ADMIN_1)
    try:
        response = TestClient(app).post(
            "/api/v1/checkins/gate",
            json={
                "shipment_id": "SHP1006",
                "facility_id": "FAC-1",
                "gate_in_at": "2026-08-08T10:00:00",
            },
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["arrival_status"] == "GATE_IN"


def test_gate_checkin_invalid_facility_returns_400() -> None:
    service = _service()
    app.dependency_overrides[get_service] = lambda: service
    app.dependency_overrides[require_admin] = lambda: _principal(TMSRole.ADMIN_1)
    try:
        response = TestClient(app).post(
            "/api/v1/checkins/gate",
            json={
                "shipment_id": "SHP1006",
                "facility_id": "FAC-OTHER",
                "gate_in_at": "2026-08-08T10:00:00",
            },
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 400
    assert "destination facility" in response.json()["detail"]
