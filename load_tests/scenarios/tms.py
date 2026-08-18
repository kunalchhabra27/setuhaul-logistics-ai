"""TMS read workload with an opt-in dedicated-test shipment create path."""

from __future__ import annotations

from locust import HttpUser, between, task

from .common import API_PREFIX, bearer_headers, dedicated_shipment_id, env, json_env, mutations_allowed


class TmsUser(HttpUser):
    host = env("LOCUST_HOST", "http://127.0.0.1:8000")
    wait_time = between(1, 2)
    weight = 2

    def on_start(self) -> None:
        self.headers = bearer_headers(role="tms")
        self.shipment_id = env("TMS_TEST_SHIPMENT_ID")
        self.assign_driver_id = env("TMS_ASSIGN_DRIVER_ID")

    @task(5)
    def list_shipments(self) -> None:
        self.client.get(f"{API_PREFIX}/tms/shipments?limit=50", headers=self.headers, name="tms.shipments.list")

    @task(2)
    def read_shipment(self) -> None:
        if self.shipment_id:
            self.client.get(
                f"{API_PREFIX}/tms/shipments/{self.shipment_id}", headers=self.headers, name="tms.shipment.read"
            )

    @task(1)
    def read_assignment_data(self) -> None:
        self.client.get(f"{API_PREFIX}/tms/drivers?limit=50", headers=self.headers, name="tms.drivers.list")
        self.client.get(f"{API_PREFIX}/tms/vehicles?limit=50", headers=self.headers, name="tms.vehicles.list")

    @task(1)
    def create_dedicated_test_shipment(self) -> None:
        payload = json_env("TMS_CREATE_SHIPMENT_JSON")
        if not mutations_allowed() or payload is None:
            return
        dedicated_shipment_id(str(payload.get("shipment_id", "")))
        self.client.post(f"{API_PREFIX}/tms/shipments", headers=self.headers, json=payload, name="tms.shipment.create")

    @task(1)
    def assign_dedicated_test_shipment(self) -> None:
        if not mutations_allowed() or not self.shipment_id or not self.assign_driver_id:
            return
        dedicated_shipment_id(self.shipment_id)
        self.client.post(
            f"{API_PREFIX}/tms/shipments/{self.shipment_id}/assign",
            headers=self.headers,
            json={"driver_id": self.assign_driver_id},
            name="tms.shipment.assign",
        )
