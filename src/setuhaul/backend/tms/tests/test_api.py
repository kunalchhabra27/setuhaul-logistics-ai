from uuid import UUID

from fastapi.testclient import TestClient

from setuhaul.backend.tms.api import get_service
from setuhaul.infrastructure.auth import Principal, get_current_principal, require_admin
from setuhaul.backend.tms.models import TMSRole
from setuhaul.main import app

from setuhaul.backend.tms.tests.conftest import DRIVER_AMBIGUOUS, DRIVER_ONE


def _principal(role: TMSRole) -> Principal:
    return Principal(user_id=str(UUID(int=1)), role=role, access_token="test-token")


def test_health_is_public():
    assert TestClient(app).get("/health").json() == {"status": "ok", "service": "tms"}


def test_unauthenticated_tms_request_is_denied():
    response = TestClient(app).get(f"/api/v1/tms/drivers/{DRIVER_ONE}")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


def test_reader_can_fetch_ambiguous_context(service):
    app.dependency_overrides[get_service] = lambda: service
    try:
        response = TestClient(app).get(f"/api/v1/tms/context/drivers/{DRIVER_AMBIGUOUS}")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    body = response.json()
    assert body["resolution"] == "ambiguous"
    assert body["requires_disambiguation"] is True
    assert len(body["active_shipments"]) == 2
    assert "latest_declared_eta" not in str(body)


def test_agent_reader_cannot_create_driver(service):
    app.dependency_overrides[get_service] = lambda: service
    app.dependency_overrides[get_current_principal] = lambda: _principal(TMSRole.AGENT_READER)
    try:
        response = TestClient(app).post(
            "/api/v1/tms/drivers",
            json={
                "carrier_id": "10000000-0000-0000-0000-000000000001",
                "driver_code": "DRV-099", "name": "No Write", "phone": "+9199",
            },
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 403


def test_admin_can_create_driver(service):
    app.dependency_overrides[get_service] = lambda: service
    app.dependency_overrides[require_admin] = lambda: _principal(TMSRole.ADMIN_1)
    try:
        response = TestClient(app).post(
            "/api/v1/tms/drivers",
            json={
                "carrier_id": "10000000-0000-0000-0000-000000000001",
                "driver_code": "DRV-099", "name": "Admin Create", "phone": "+9199",
            },
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 201
    assert response.json()["driver_code"] == "DRV-099"


def test_malformed_uuid_uses_structured_422(service):
    app.dependency_overrides[get_service] = lambda: service
    try:
        response = TestClient(app).get("/api/v1/tms/drivers/not-a-uuid")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REQUEST_VALIDATION_FAILED"
