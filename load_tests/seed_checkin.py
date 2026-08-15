"""Prepare dedicated LT shipments and confirmed appointments through SetuHaul APIs."""

from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

API_PREFIX = "/api/v1"


def _request(client: httpx.Client, method: str, path: str, **kwargs: Any) -> Any:
    response = client.request(method, path, **kwargs)
    if not response.is_success:
        raise RuntimeError(f"{method} {path} failed ({response.status_code}): {response.text[:500]}")
    return response.json()


def _reference_pairs(
    client: httpx.Client,
    count: int,
    *,
    allow_reuse: bool = False,
) -> list[tuple[dict, dict]]:
    drivers = _request(client, "GET", f"{API_PREFIX}/tms/drivers?limit=500")
    vehicles = _request(client, "GET", f"{API_PREFIX}/tms/vehicles?limit=500")
    active_drivers = [row for row in drivers if row.get("driver_status") == "ACTIVE" and row.get("carrier_id")]
    active_vehicles = [row for row in vehicles if row.get("active_flag") and row.get("carrier_id")]
    vehicles_by_carrier: dict[str, list[dict]] = {}
    for vehicle in active_vehicles:
        vehicles_by_carrier.setdefault(vehicle["carrier_id"], []).append(vehicle)

    pairs: list[tuple[dict, dict]] = []
    used_vehicles: set[str] = set()
    for driver in active_drivers:
        vehicle = next(
            (
                item
                for item in vehicles_by_carrier.get(driver["carrier_id"], [])
                if item["vehicle_id"] not in used_vehicles
            ),
            None,
        )
        if vehicle is None:
            continue
        used_vehicles.add(vehicle["vehicle_id"])
        pairs.append((driver, vehicle))
        if len(pairs) == count:
            return pairs
    if not pairs:
        raise RuntimeError("No active same-carrier driver/vehicle pair is available.")
    if not allow_reuse:
        raise RuntimeError(
            f"Only {len(pairs)} unique active driver/vehicle pairs are available; {count} are required. "
            "Set CHECKIN_ALLOW_REFERENCE_REUSE=true only after explicitly approving read-only reference reuse."
        )
    return [pairs[index % len(pairs)] for index in range(count)]


def _gate_time(slot_start: str, timing_group: str) -> str:
    start = datetime.fromisoformat(slot_start)
    offset = {"EARLY": -30, "ON_TIME": 0, "DELAYED": 30}[timing_group]
    return (start + timedelta(minutes=offset)).isoformat()


def _prepare_shipment(
    client: httpx.Client,
    *,
    index: int,
    driver: dict,
    vehicle: dict,
    facility_id: str,
    run_id: str,
    now: datetime,
) -> dict[str, Any]:
    shipment_id = f"LT-CI-{run_id}-{index:03d}"
    eta = now + timedelta(hours=2)
    payload = {
        "shipment_id": shipment_id,
        "order_reference": shipment_id,
        "carrier_id": driver["carrier_id"],
        "driver_id": driver["driver_id"],
        "vehicle_id": vehicle["vehicle_id"],
        "origin_name": "LT Harness Origin",
        "origin_city": "Test City",
        "destination_facility_id": facility_id,
        "customer_name": "LT Harness",
        "product_category": "FMCG",
        "load_weight_kg": min(1000, int(vehicle.get("capacity_kg") or 1000)),
        "required_dock_type": "STANDARD",
        "temperature_control_required": False,
        "priority_code": "NORMAL",
        "planned_departure_ts": (now - timedelta(hours=1)).isoformat(),
        "original_eta_ts": eta.isoformat(),
        "latest_eta_ts": eta.isoformat(),
        "expected_unload_min": 30,
        "current_status": "IN_TRANSIT",
    }
    _request(client, "POST", f"{API_PREFIX}/tms/shipments", json=payload)

    # Concurrent workers can rank the same slot before either hold commits.
    # Retry through the scheduler so every retry is freshly revalidated.
    last_error: RuntimeError | None = None
    for attempt in range(1, 11):
        suggestions = _request(
            client,
            "POST",
            f"{API_PREFIX}/dock-scheduler/suggest",
            json={"shipment_id": shipment_id, "limit": 10},
        )
        available = next((row for row in suggestions if row.get("suggestion_type") == "ASSIGN_AVAILABLE"), None)
        if available is None:
            last_error = RuntimeError(f"No available deterministic slot was returned for {shipment_id}.")
            time.sleep(min(0.1 * attempt, 0.5))
            continue
        slot_id = available["slot_id"]
        try:
            _request(
                client,
                "POST",
                f"{API_PREFIX}/dock-scheduler/hold",
                json={"shipment_id": shipment_id, "slot_id": slot_id, "ttl_minutes": 15},
            )
        except RuntimeError as exc:
            last_error = exc
            time.sleep(min(0.1 * attempt, 0.5))
            continue
        _request(
            client,
            "POST",
            f"{API_PREFIX}/dock-scheduler/request-confirmation",
            json={"shipment_id": shipment_id, "slot_id": slot_id, "ttl_minutes": 15},
        )
        confirmed = _request(
            client,
            "POST",
            f"{API_PREFIX}/dock-scheduler/confirm",
            json={"shipment_id": shipment_id, "slot_id": slot_id, "accepted": True},
        )
        timing_group = ("EARLY", "ON_TIME", "DELAYED")[(index - 1) % 3]
        return {
            "shipment_id": shipment_id,
            "driver_id": driver["driver_id"],
            "vehicle_id": vehicle["vehicle_id"],
            "appointment_id": confirmed["appointment_id"],
            "slot_id": slot_id,
            "slot_start": available["start"],
            "timing_group": timing_group,
            "gate_in_at": _gate_time(available["start"], timing_group),
        }
    raise last_error or RuntimeError(f"Unable to prepare {shipment_id}.")


def seed(count: int, host: str, label: str, output: Path) -> dict:
    token = os.getenv("TEST_ACCESS_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TEST_ACCESS_TOKEN is required.")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    with httpx.Client(base_url=host.rstrip("/"), headers=headers, timeout=120) as client:
        facilities = _request(client, "GET", f"{API_PREFIX}/tms/facilities?limit=500")
        facility_id = os.getenv("CHECKIN_TEST_FACILITY_ID", "").strip()
        if not facility_id:
            if not facilities:
                raise RuntimeError("No facility is available for the Check-in test.")
            facility_id = facilities[0]["facility_id"]
        allow_reference_reuse = os.getenv("CHECKIN_ALLOW_REFERENCE_REUSE", "").strip().lower() == "true"
        pairs = _reference_pairs(client, count, allow_reuse=allow_reference_reuse)
        now = datetime.now(timezone.utc)
        run_id = f"{label}-{now:%Y%m%d%H%M%S}"
        shipments: list[dict[str, Any]] = []
        seed_failures: list[dict[str, str]] = []
        concurrency = max(1, min(count, int(os.getenv("CHECKIN_SEED_CONCURRENCY", "1"))))
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {
                executor.submit(
                    _prepare_shipment,
                    client,
                    index=index,
                    driver=driver,
                    vehicle=vehicle,
                    facility_id=facility_id,
                    run_id=run_id,
                    now=now,
                ): index
                for index, (driver, vehicle) in enumerate(pairs, start=1)
            }
            for completed, future in enumerate(as_completed(futures), start=1):
                index = futures[future]
                try:
                    shipment = future.result()
                    shipments.append(shipment)
                    print(f"Prepared {completed}/{count}: {shipment['shipment_id']}", flush=True)
                except Exception as exc:  # noqa: BLE001 - record and continue with independent LT rows
                    shipment_id = f"LT-CI-{run_id}-{index:03d}"
                    seed_failures.append({"shipment_id": shipment_id, "error": str(exc)[:500]})
                    print(f"Failed {completed}/{count}: {shipment_id}", flush=True)

    shipments.sort(key=lambda item: item["shipment_id"])
    seed_failures.sort(key=lambda item: item["shipment_id"])
    manifest = {
        "run_id": run_id,
        "facility_id": facility_id,
        "attempted": count,
        "shipments": shipments,
        "seed_failures": seed_failures,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, required=True, choices=range(1, 101))
    parser.add_argument("--host", default=os.getenv("LOCUST_HOST", "http://127.0.0.1:8000"))
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = seed(args.count, args.host, args.label, args.output)
    print(f"Manifest: {args.output} ({len(manifest['shipments'])} shipments)")


if __name__ == "__main__":
    main()
