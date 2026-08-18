"""Read-only, same-session conversation workload for local AgentCore."""

from __future__ import annotations

from uuid import uuid4

import gevent
from locust import HttpUser, between, task

from .common import env


SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"


class AgentCoreDriverUser(HttpUser):
    host = env("AGENTCORE_HOST", "http://localhost:8090")
    wait_time = between(2, 4)

    def on_start(self) -> None:
        self.session_id = f"setuhaul-locust-{uuid4()}"

    def _message(self, prompt: str, metric_name: str) -> None:
        with self.client.post(
            "/invocations",
            headers={SESSION_HEADER: self.session_id, "Content-Type": "application/json"},
            json={"prompt": prompt},
            name=metric_name,
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"AgentCore returned HTTP {response.status_code}")
                return
            try:
                payload = response.json()
            except ValueError:
                response.failure("AgentCore returned invalid JSON")
                return
            if not isinstance(payload, dict) or not isinstance(payload.get("result"), str) or not payload["result"].strip():
                response.failure("AgentCore response did not include a non-empty result")

    @task
    def read_only_conversation(self) -> None:
        self._message(
            "What is the status of my dock appointment?",
            "agentcore.driver_chat.status",
        )
        gevent.sleep(0.5)
        self._message(
            "And what should I do next?",
            "agentcore.driver_chat.followup",
        )
