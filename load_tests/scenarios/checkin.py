"""One complete Check-in lifecycle per pre-seeded dedicated test shipment."""

from __future__ import annotations

import itertools
import json
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

import gevent
from locust import HttpUser, between, task
from locust.exception import StopUser

from .common import API_PREFIX, bearer_headers, dedicated_shipment_id, env, event_time, mutations_allowed

_shipment_index = itertools.count()


@lru_cache(maxsize=1)
def _manifest() -> dict:
    path = env("CHECKIN_MANIFEST")
    if not path:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("shipments"), list):
        raise RuntimeError("CHECKIN_MANIFEST must contain a JSON object with a shipments list.")
    return payload


def _stage_time(gate_in_at: str | None, minutes: int) -> str:
    if not gate_in_at:
        return event_time()
    return (datetime.fromisoformat(gate_in_at) + timedelta(minutes=minutes)).isoformat()


def validate_checkin_configuration(required_shipments: int = 1) -> dict:
    if not mutations_allowed():
        raise RuntimeError("LOAD_TEST_ALLOW_MUTATIONS=true is required.")
    manifest = _manifest()
    facility_id = str(manifest.get("facility_id") or env("CHECKIN_TEST_FACILITY_ID"))
    manifest_shipments = manifest.get("shipments") or []
    shipment_ids = [item.strip() for item in env("CHECKIN_SHIPMENT_IDS").split(",") if item.strip()]
    available = manifest_shipments or shipment_ids
    if not facility_id or not available:
        raise RuntimeError(
            "CHECKIN_MANIFEST or CHECKIN_TEST_FACILITY_ID plus CHECKIN_SHIPMENT_IDS is required."
        )
    if len(available) < required_shipments:
        raise RuntimeError(
            f"{required_shipments} users require {required_shipments} dedicated shipments; only {len(available)} configured."
        )
    for item in available:
        shipment_id = str(item["shipment_id"]) if isinstance(item, dict) else str(item)
        dedicated_shipment_id(shipment_id)
    return {"facility_id": facility_id, "shipments": available}


class CheckinUser(HttpUser):
    # Stagger stage calls while still making a 100-user run practical.
    wait_time = between(0.2, 1.2)
    weight = 1
    host = env("LOCUST_HOST", "http://127.0.0.1:8000")

    def on_start(self) -> None:
        self.headers = bearer_headers(role="checkin")
        configuration = validate_checkin_configuration()
        self.facility_id = configuration["facility_id"]
        manifest_shipments = _manifest().get("shipments") or []
        shipment_ids = configuration["shipments"] if not manifest_shipments else []
        shipment_index = next(_shipment_index)
        if shipment_index >= len(manifest_shipments or shipment_ids):
            # A 100-user run needs 100 explicitly seeded rows, never reuse a
            # completed shipment merely to keep a virtual user alive.
            raise StopUser()
        if manifest_shipments:
            shipment = manifest_shipments[shipment_index]
            self.shipment_id = dedicated_shipment_id(str(shipment["shipment_id"]))
            self.gate_in_at = shipment.get("gate_in_at")
        else:
            self.shipment_id = dedicated_shipment_id(shipment_ids[shipment_index])
            self.gate_in_at = None
        stagger_seconds = float(env("CHECKIN_STAGGER_SECONDS", "0.05"))
        if stagger_seconds > 0:
            gevent.sleep(shipment_index * stagger_seconds)
        self.stage = 0

    @task
    def lifecycle(self) -> None:
        if self.stage == 0:
            response = self.client.post(
                f"{API_PREFIX}/checkins/gate",
                headers=self.headers,
                json={
                    "shipment_id": self.shipment_id,
                    "facility_id": self.facility_id,
                    "gate_in_at": _stage_time(self.gate_in_at, 0),
                },
                name="checkin.gate",
            )
        elif self.stage == 1:
            response = self.client.patch(
                f"{API_PREFIX}/checkins/queue",
                headers=self.headers,
                json={"shipment_id": self.shipment_id, "queue_status": "YARD_QUEUE"},
                name="checkin.queue",
            )
        elif self.stage == 2:
            response = self.client.patch(
                f"{API_PREFIX}/checkins/dock",
                headers=self.headers,
                json={"shipment_id": self.shipment_id, "dock_in_at": _stage_time(self.gate_in_at, 10)},
                name="checkin.dock",
            )
        elif self.stage == 3:
            response = self.client.patch(
                f"{API_PREFIX}/checkins/complete",
                headers=self.headers,
                json={"shipment_id": self.shipment_id, "completed_at": _stage_time(self.gate_in_at, 30)},
                name="checkin.complete",
            )
        else:
            raise StopUser()
        if not response.ok:
            raise StopUser()
        self.stage += 1
