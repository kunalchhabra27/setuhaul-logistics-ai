"""FastAPI-level tests for the driver-chat-eta router.

These use the real app + TestClient (as opposed to unit-testing
get_current_driver / DriverChatService in isolation) specifically to prove
that DriverChatError raised inside a Depends()-resolved function -- not just
inside a route body -- is converted to the intended HTTP status instead of
propagating to a generic 500. That gap (no app-level exception handler for
DriverChatError) previously let an expired/invalid bearer token surface as
"Internal Server Error" instead of 401.
"""

from fastapi.testclient import TestClient

from setuhaul.main import app


def test_missing_bearer_token_is_401_not_500():
    response = TestClient(app).get("/api/v1/driver-chat-eta/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


def test_malformed_auth_scheme_is_401_not_500():
    response = TestClient(app).get(
        "/api/v1/driver-chat-eta/me",
        headers={"Authorization": "Basic not-a-bearer-token"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
