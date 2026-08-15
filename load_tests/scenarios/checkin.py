"""One complete Check-in lifecycle per pre-seeded dedicated test shipment."""

from __future__ import annotations

import itertools

from locust import HttpUser, between, task
from locust.exception import StopUser

from .common import API_PREFIX, bearer_headers, dedicated_shipment_id, env, event_time, mutations_allowed

_shipment_index = itertools.count()


class CheckinUser(HttpUser):
    # Stagger stage calls while still making a 100-user run practical.
    wait_time = between(0.2, 1.2)
    weight = 1

    def on_start(self) -> None:
        if not mutations_allowed():
            raise StopUser()
        self.headers = bearer_headers()
        self.facility_id = env("CHECKIN_TEST_FACILITY_ID")
        shipment_ids = [item.strip() for item in env("CHECKIN_SHIPMENT_IDS").split(",") if item.strip()]
        if not self.facility_id or not shipment_ids:
            raise RuntimeError("CHECKIN_TEST_FACILITY_ID and CHECKIN_SHIPMENT_IDS are required for this scenario.")
        shipment_index = next(_shipment_index)
        if shipment_index >= len(shipment_ids):
            # A 100-user run needs 100 explicitly seeded rows, never reuse a
            # completed shipment merely to keep a virtual user alive.
            raise StopUser()
        self.shipment_id = dedicated_shipment_id(shipment_ids[shipment_index])
        self.stage = 0

    @task
    def lifecycle(self) -> None:
        if self.stage == 0:
            self.client.post(
                f"{API_PREFIX}/checkins/gate",
                headers=self.headers,
                json={"shipment_id": self.shipment_id, "facility_id": self.facility_id, "gate_in_at": event_time()},
                name="checkin.gate",
            )
        elif self.stage == 1:
            self.client.patch(
                f"{API_PREFIX}/checkins/queue",
                headers=self.headers,
                json={"shipment_id": self.shipment_id, "queue_status": "YARD_QUEUE"},
                name="checkin.queue",
            )
        elif self.stage == 2:
            self.client.patch(
                f"{API_PREFIX}/checkins/dock",
                headers=self.headers,
                json={"shipment_id": self.shipment_id, "dock_in_at": event_time()},
                name="checkin.dock",
            )
        elif self.stage == 3:
            self.client.patch(
                f"{API_PREFIX}/checkins/complete",
                headers=self.headers,
                json={"shipment_id": self.shipment_id, "completed_at": event_time()},
                name="checkin.complete",
            )
        else:
            raise StopUser()
        self.stage += 1
