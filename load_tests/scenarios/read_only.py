"""Single-token, non-destructive baseline workload for local testing."""

from __future__ import annotations

import logging

from locust import HttpUser, between, task

from load_tests.auth import LoadTestAuthenticationError, authentication_configured, bearer_headers

from .common import API_PREFIX, env

logger = logging.getLogger(__name__)
_auth_skip_logged = False


class ReadOnlyUser(HttpUser):
    host = env("LOCUST_HOST", "http://127.0.0.1:8000")
    wait_time = between(0.2, 1.0)

    def on_start(self) -> None:
        global _auth_skip_logged
        self.headers = None
        if authentication_configured():
            try:
                self.headers = bearer_headers()
            except LoadTestAuthenticationError:
                self.headers = None
        if self.headers is None and not _auth_skip_logged:
            logger.warning("Authenticated scenarios skipped: TEST_ACCESS_TOKEN not configured")
            _auth_skip_logged = True
        self.shipment_id: str | None = None

    @task(2)
    def health(self) -> None:
        self.client.get("/health", name="system.health")

    @task(2)
    def driver_health(self) -> None:
        self.client.get(
            f"{API_PREFIX}/driver-chat-eta/health",
            name="driver.health",
        )

    @task(1)
    def tms_health(self) -> None:
        self.client.get(
            f"{API_PREFIX}/tms/health",
            name="tms.health",
        )

    @task(4)
    def list_shipments(self) -> None:
        if self.headers is None:
            return
        response = self.client.get(
            f"{API_PREFIX}/tms/shipments?limit=25",
            headers=self.headers,
            name="tms.shipments.list",
        )
        if response.ok:
            rows = response.json()
            if rows:
                self.shipment_id = rows[0].get("shipment_id")

    @task(1)
    def list_drivers(self) -> None:
        if self.headers is None:
            return
        self.client.get(
            f"{API_PREFIX}/tms/drivers?limit=25",
            headers=self.headers,
            name="tms.drivers.list",
        )

    @task(1)
    def list_vehicles(self) -> None:
        if self.headers is None:
            return
        self.client.get(
            f"{API_PREFIX}/tms/vehicles?limit=25",
            headers=self.headers,
            name="tms.vehicles.list",
        )

    @task(1)
    def list_facilities(self) -> None:
        if self.headers is None:
            return
        self.client.get(
            f"{API_PREFIX}/tms/facilities?limit=25",
            headers=self.headers,
            name="tms.facilities.list",
        )

    @task(2)
    def driver_profile(self) -> None:
        if self.headers is None:
            return
        self.client.get(
            f"{API_PREFIX}/driver-chat-eta/me",
            headers=self.headers,
            name="driver.profile",
        )

    @task(2)
    def shipment_context(self) -> None:
        if self.headers is None or not self.shipment_id:
            return
        self.client.get(
            f"{API_PREFIX}/tms/shipments/{self.shipment_id}",
            headers=self.headers,
            name="tms.shipment.read",
        )
