"""Generate deterministic TMS-only SQL fixtures for development demos."""

from __future__ import annotations

import argparse
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid5

NAMESPACE = UUID("74a1844d-57ea-4be2-8faf-934b3ba7c5b4")
DEFAULT_CARRIERS = [
    UUID("10000000-0000-0000-0000-000000000001"),
    UUID("10000000-0000-0000-0000-000000000002"),
]
DEFAULT_FACILITIES = [
    UUID("20000000-0000-0000-0000-000000000001"),
    UUID("20000000-0000-0000-0000-000000000002"),
    UUID("20000000-0000-0000-0000-000000000003"),
]


def stable_uuid(kind: str, index: int, seed: int) -> UUID:
    """Return a repeatable UUID for one generated business entity."""
    return uuid5(NAMESPACE, f"{seed}:{kind}:{index}")


def sql_text(value: str) -> str:
    """Quote a trusted generated text value for a SQL fixture."""
    return "'" + value.replace("'", "''") + "'"


def generate_dataset(
    *,
    driver_count: int = 100,
    vehicle_count: int = 120,
    shipment_count: int = 800,
    seed: int = 20260808,
    carriers: list[UUID] | None = None,
    facilities: list[UUID] | None = None,
) -> str:
    """Return deterministic SQL for drivers, vehicles and seven days of shipments."""
    if min(driver_count, vehicle_count, shipment_count) < 1:
        raise ValueError("All generated record counts must be positive.")
    carrier_ids = carriers or DEFAULT_CARRIERS
    facility_ids = facilities or DEFAULT_FACILITIES
    if not carrier_ids or not facility_ids:
        raise ValueError("At least one carrier and facility UUID are required.")

    rng = random.Random(seed)
    driver_rows: list[str] = []
    vehicle_rows: list[str] = []
    shipment_rows: list[str] = []
    vehicles_by_carrier: dict[UUID, list[UUID]] = {item: [] for item in carrier_ids}
    active_drivers: list[tuple[UUID, UUID]] = []

    for index in range(1, driver_count + 1):
        driver_id = stable_uuid("driver", index, seed)
        carrier_id = carrier_ids[(index - 1) % len(carrier_ids)]
        status = "inactive" if index % 29 == 0 else "active"
        if status == "active":
            active_drivers.append((carrier_id, driver_id))
        driver_rows.append(
            f"('{driver_id}', '{carrier_id}', 'GEN-DRV-{index:04d}', "
            f"'Generated Driver {index:04d}', '+91-8{index:09d}', "
            f"'Generated Base {index % 12 + 1}', {str(status == 'active').lower()}, '{status}')"
        )

    vehicle_types = [("dry_van", 32, False), ("reefer", 32, True), ("closed_body", 24, False)]
    for index in range(1, vehicle_count + 1):
        vehicle_id = stable_uuid("vehicle", index, seed)
        carrier_id = carrier_ids[(index - 1) % len(carrier_ids)]
        vehicle_type, length_ft, refrigerated = vehicle_types[index % len(vehicle_types)]
        status = "maintenance" if index % 31 == 0 else "active"
        if status == "active":
            vehicles_by_carrier[carrier_id].append(vehicle_id)
        vehicle_rows.append(
            f"('{vehicle_id}', '{carrier_id}', 'GEN-VEH-{index:04d}', '{vehicle_type}', "
            f"{length_ft}, {9000 + index * 75}, {str(refrigerated).lower()}, "
            f"{str(status == 'active').lower()}, '{status}')"
        )

    statuses = ["planned", "in_transit", "arrived", "waiting", "unloading", "completed", "cancelled", "exception"]
    products = ["dry_freight", "produce", "frozen_food", "auto_parts", "consumer_goods"]
    start = datetime(2026, 8, 8, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    for index in range(1, shipment_count + 1):
        shipment_id = stable_uuid("shipment", index, seed)
        carrier_id, driver_id = active_drivers[(index - 1) % len(active_drivers)]
        vehicle_id = rng.choice(vehicles_by_carrier[carrier_id])
        origin = rng.choice(facility_ids)
        destination = rng.choice(facility_ids)
        eta = start + timedelta(minutes=rng.randrange(0, 7 * 24 * 60))
        status = statuses[index % len(statuses)]
        shipment_rows.append(
            f"('{shipment_id}', '{driver_id}', '{vehicle_id}', '{origin}', '{destination}', "
            f"{sql_text(rng.choice(products))}, {rng.randint(1, 5)}, '{eta.isoformat()}', "
            f"{rng.choice([30, 40, 45, 60, 75, 90])}, '{status}')"
        )

    return "\n".join(
        [
            "-- Generated TMS-only fixture. Carrier/facility UUIDs must already exist.",
            "begin;",
            "insert into public.drivers (driver_id, carrier_id, driver_code, name, phone, home_base, active_flag, status) values",
            ",\n".join(driver_rows) + ";",
            "insert into public.vehicles (vehicle_id, carrier_id, vehicle_number, vehicle_type, length_ft, capacity_weight_kg, refrigeration_required, active_flag, status) values",
            ",\n".join(vehicle_rows) + ";",
            "insert into public.shipments (shipment_id, driver_id, vehicle_id, origin_id, destination_id, product_class, priority, planned_eta, expected_unload_minutes, status) values",
            ",\n".join(shipment_rows) + ";",
            "commit;",
            "",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--drivers", type=int, default=100)
    parser.add_argument("--vehicles", type=int, default=120)
    parser.add_argument("--shipments", type=int, default=800)
    parser.add_argument("--seed", type=int, default=20260808)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    sql = generate_dataset(
        driver_count=args.drivers,
        vehicle_count=args.vehicles,
        shipment_count=args.shipments,
        seed=args.seed,
    )
    if args.output:
        args.output.write_text(sql, encoding="utf-8")
    else:
        print(sql, end="")


if __name__ == "__main__":
    main()
