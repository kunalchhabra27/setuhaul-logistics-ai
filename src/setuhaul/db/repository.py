from __future__ import annotations

import sqlite3
from datetime import datetime

ACTIVE_APPOINTMENT_STATUSES = ("PENDING_CONFIRMATION", "CONFIRMED", "IN_PROGRESS")
PRIORITY_WEIGHT = {"LOW": 1, "NORMAL": 2, "HIGH": 3, "CRITICAL": 4}


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


class OperationsRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def shipment(self, shipment_id: str) -> sqlite3.Row:
        row = self.connection.execute(
            "SELECT * FROM v_inbound_operational_state WHERE shipment_id = ?",
            (shipment_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown shipment: {shipment_id}")
        return row

    def facility(self, facility_id: str) -> sqlite3.Row:
        row = self.connection.execute(
            "SELECT * FROM facilities WHERE facility_id = ?", (facility_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown facility: {facility_id}")
        return row

    def compatible_slots(self, shipment_id: str) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT
                va.*,
                d.dock_id,
                d.dock_status,
                d.supports_refrigerated,
                d.max_vehicle_weight_kg,
                occ.priority_code AS occupied_priority,
                occ.expected_unload_min AS occupied_unload_min
            FROM v_slot_availability va
            JOIN docks d ON d.dock_code = va.dock_code AND d.facility_id = va.facility_id
            JOIN shipments target ON target.shipment_id = ?
            LEFT JOIN shipments occ ON occ.shipment_id = va.shipment_id
            JOIN vehicles v ON v.vehicle_id = target.vehicle_id
            WHERE va.facility_id = target.destination_facility_id
              AND d.dock_status = 'ACTIVE'
              AND (target.required_dock_type = 'ANY' OR d.dock_type = target.required_dock_type)
              AND (target.temperature_control_required = 0 OR d.supports_refrigerated = 1)
              AND (d.max_vehicle_weight_kg IS NULL OR d.max_vehicle_weight_kg >= target.load_weight_kg)
            ORDER BY va.slot_start_ts, va.dock_code
            """,
            (shipment_id,),
        ).fetchall()

    def current_appointment(self, shipment_id: str) -> sqlite3.Row | None:
        return self.connection.execute(
            """
            SELECT a.*, sl.slot_start_ts, sl.slot_end_ts, d.dock_code
            FROM appointments a
            JOIN appointment_slots sl ON sl.slot_id = a.slot_id
            JOIN docks d ON d.dock_id = sl.dock_id
            WHERE a.shipment_id = ? AND a.is_current = 1
              AND a.appointment_status IN ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS')
            """,
            (shipment_id,),
        ).fetchone()

    def book_after_acceptance(self, shipment_id: str, slot_id: str, accepted: bool) -> str:
        if not accepted:
            raise ValueError("Explicit driver acceptance is required before booking")

        now = datetime.now().astimezone().isoformat(timespec="seconds")
        appointment_id = f"APT-{shipment_id}-{slot_id}"
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            slot = self.connection.execute(
                "SELECT availability_status FROM v_slot_availability WHERE slot_id = ?",
                (slot_id,),
            ).fetchone()
            if slot is None or slot["availability_status"] != "AVAILABLE":
                raise ValueError("Selected slot is no longer available")

            previous = self.current_appointment(shipment_id)
            if previous:
                self.connection.execute(
                    """
                    UPDATE appointments
                    SET is_current = 0, appointment_status = 'CANCELLED', cancelled_at = ?
                    WHERE appointment_id = ?
                    """,
                    (now, previous["appointment_id"]),
                )

            self.connection.execute(
                """
                INSERT INTO appointments (
                    appointment_id, shipment_id, slot_id, appointment_status,
                    is_current, booked_at, confirmed_at, cancelled_at,
                    replaced_appointment_id, booking_source, notes
                ) VALUES (?, ?, ?, 'CONFIRMED', 1, ?, ?, NULL, ?, 'DRIVER_CHAT', ?)
                """,
                (
                    appointment_id,
                    shipment_id,
                    slot_id,
                    now,
                    now,
                    previous["appointment_id"] if previous else None,
                    "Confirmed only after explicit driver acceptance",
                ),
            )
            self.connection.commit()
            return appointment_id
        except Exception:
            self.connection.rollback()
            raise
