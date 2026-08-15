"""Driver portal read and ETA/chat workload using an existing test account."""

from __future__ import annotations

from locust import HttpUser, between, task

from .common import API_PREFIX, bearer_headers, env


class DriverUser(HttpUser):
    host = env("LOCUST_HOST", "http://127.0.0.1:8000")
    wait_time = between(1, 3)
    weight = 3

    def on_start(self) -> None:
        self.headers = bearer_headers(role="driver")
        self.shipment_id = env("DRIVER_TEST_SHIPMENT_ID")

    @task(4)
    def snapshot_and_slots(self) -> None:
        # /snapshot is the existing driver-scoped source of shipment and
        # deterministic slot-option data; it does not mutate an appointment.
        self.client.get(f"{API_PREFIX}/driver-chat-eta/snapshot", headers=self.headers, name="driver.snapshot")

    @task(2)
    def query_existing_delay_flow(self) -> None:
        # No driver message content is captured by telemetry. Keep it a fixed
        # harmless query so this works with the existing LLM or regex fallback.
        self.client.post(
            f"{API_PREFIX}/driver-chat-eta/chat",
            headers=self.headers,
            json={"message": "Please show my current feasible dock slot options."},
            name="driver.chat.slot_options",
        )

    @task(1)
    def current_profile(self) -> None:
        self.client.get(f"{API_PREFIX}/driver-chat-eta/me", headers=self.headers, name="driver.profile")
