"""WMS dock-board workload and opt-in hold/confirm revalidation exercise."""

from __future__ import annotations

from locust import HttpUser, between, task

from .common import API_PREFIX, bearer_headers, dedicated_shipment_id, env, mutations_allowed


class WmsUser(HttpUser):
    wait_time = between(1, 3)
    weight = 2

    def on_start(self) -> None:
        self.headers = bearer_headers()
        self.shipment_id = env("WMS_TEST_SHIPMENT_ID")
        self.slot_id = env("WMS_TEST_SLOT_ID")
        self.change_slot_id = env("WMS_CHANGE_SLOT_ID")

    @task(5)
    def dock_board(self) -> None:
        if self.shipment_id:
            self.client.get(
                f"{API_PREFIX}/dock-scheduler/board?shipment_id={self.shipment_id}",
                headers=self.headers,
                name="wms.dock_board",
            )

    @task(2)
    def deterministic_suggestions(self) -> None:
        if self.shipment_id:
            self.client.post(
                f"{API_PREFIX}/dock-scheduler/suggest",
                headers=self.headers,
                json={"shipment_id": self.shipment_id, "limit": 3},
                name="wms.slot_suggest",
            )

    @task(1)
    def hold_then_confirm(self) -> None:
        if not mutations_allowed() or not self.shipment_id or not self.slot_id:
            return
        dedicated_shipment_id(self.shipment_id)
        self.client.post(
            f"{API_PREFIX}/dock-scheduler/hold",
            headers=self.headers,
            json={"shipment_id": self.shipment_id, "slot_id": self.slot_id, "ttl_minutes": 5},
            name="wms.slot_hold",
        )
        self.client.post(
            f"{API_PREFIX}/dock-scheduler/confirm",
            headers=self.headers,
            json={"shipment_id": self.shipment_id, "slot_id": self.slot_id, "accepted": True},
            name="wms.slot_confirm",
        )

    @task(1)
    def request_slot_change(self) -> None:
        if not mutations_allowed() or not self.shipment_id or not self.change_slot_id:
            return
        dedicated_shipment_id(self.shipment_id)
        self.client.post(
            f"{API_PREFIX}/dock-scheduler/change-requests",
            headers=self.headers,
            json={
                "shipment_id": self.shipment_id,
                "requested_slot_id": self.change_slot_id,
                "requested_by_role": "TMS",
                "reason": "Locust harness revalidation exercise",
            },
            name="wms.slot_change_request",
        )
