from __future__ import annotations

from load_tests.scenarios import agentcore


class _Response:
    def __init__(self, status_code: int = 200, payload=None, json_error: bool = False) -> None:
        self.status_code = status_code
        self._payload = {"result": "Appointment confirmed."} if payload is None else payload
        self._json_error = json_error
        self.failures: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def json(self):
        if self._json_error:
            raise ValueError("bad json")
        return self._payload

    def failure(self, message: str) -> None:
        self.failures.append(message)


class _Client:
    def __init__(self, responses=None) -> None:
        self.responses = list(responses or [])
        self.calls: list[dict] = []

    def post(self, path: str, **kwargs):
        self.calls.append({"path": path, **kwargs})
        return self.responses.pop(0) if self.responses else _Response()


def _user(client: _Client):
    user = object.__new__(agentcore.AgentCoreDriverUser)
    user.client = client
    user.session_id = "setuhaul-locust-test-session"
    return user


def test_conversation_reuses_session_and_stable_metric_names(monkeypatch) -> None:
    client = _Client()
    user = _user(client)
    monkeypatch.setattr(agentcore.gevent, "sleep", lambda _seconds: None)

    user.read_only_conversation()

    assert [call["name"] for call in client.calls] == [
        "agentcore.driver_chat.status",
        "agentcore.driver_chat.followup",
    ]
    assert [call["headers"][agentcore.SESSION_HEADER] for call in client.calls] == [
        user.session_id,
        user.session_id,
    ]
    assert [call["json"]["prompt"] for call in client.calls] == [
        "What is the status of my dock appointment?",
        "And what should I do next?",
    ]


def test_optional_metadata_is_not_required() -> None:
    response = _Response(payload={"result": "Appointment confirmed."})
    user = _user(_Client([response]))

    user._message("status", "agentcore.driver_chat.status")

    assert response.failures == []


def test_nonempty_result_is_required() -> None:
    response = _Response(payload={"result": "  "})
    user = _user(_Client([response]))

    user._message("status", "agentcore.driver_chat.status")

    assert response.failures == ["AgentCore response did not include a non-empty result"]


def test_http_and_json_failures_are_reported() -> None:
    http_response = _Response(status_code=503)
    json_response = _Response(json_error=True)
    user = _user(_Client([http_response, json_response]))

    user._message("status", "agentcore.driver_chat.status")
    user._message("followup", "agentcore.driver_chat.followup")

    assert http_response.failures == ["AgentCore returned HTTP 503"]
    assert json_response.failures == ["AgentCore returned invalid JSON"]
