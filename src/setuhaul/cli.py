from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from setuhaul.backend.dock_scheduler.models import DriverConstraints
from setuhaul.backend.dock_scheduler.repository import DockSchedulerRepository
from setuhaul.backend.dock_scheduler.service import DockSchedulerService
from setuhaul.db.connection import build_database, connect


def main() -> None:
    parser = argparse.ArgumentParser(description="SetuHaul deterministic rescheduling demo")
    parser.add_argument("shipment_id", nargs="?", default="SHP1006")
    parser.add_argument("--must-finish-by", help="ISO-8601 timestamp")
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    db_path = root / "data" / "setuhaul_freight_operations.db"
    sql_path = root / "data" / "setuhaul_schema_and_seed.sql"
    if args.rebuild or not db_path.exists():
        build_database(sql_path, db_path)

    constraints = DriverConstraints(
        must_finish_by=datetime.fromisoformat(args.must_finish_by)
        if args.must_finish_by
        else None
    )
    with connect(db_path) as connection:
        service = DockSchedulerService(DockSchedulerRepository(connection))
        suggestions = service.suggest_slots(args.shipment_id, constraints)

    if not suggestions:
        print("No feasible slot found. Escalate to human operations.")
        return
    for item in suggestions:
        print(
            f"{item.rank}. {item.suggestion_type.value}: {item.slot_id} "
            f"({item.start:%H:%M}-{item.end:%H:%M}, {item.dock_code}) - {item.reason}"
        )


if __name__ == "__main__":
    main()
