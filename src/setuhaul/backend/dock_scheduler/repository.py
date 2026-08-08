from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from setuhaul.backend.dock_scheduler.exceptions import (
    InvalidBookingError,
    SlotUnavailableError,
    UnknownShipmentError,
)
from setuhaul.backend.dock_scheduler.models import HoldResult

ACTIVE_APPOINTMENT_STATUSES = ("PENDING_CONFIRMATION", "CONFIRMED", "IN_PROGRESS")
PRIORITY_WEIGHT = {"LOW": 1, "NORMAL": 2, "HIGH": 3, "CRITICAL": 4}


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


class DockSchedulerRepository:
    """Persistence boundary for facilities, slots, appointments, and holds."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.connection.row_factory = sqlite3.Row

    def shipment(self, shipment_id: str) -> sqlite3.Row:
        row = self.connection.execute(
            "SELECT * FROM v_inbound_operational_state WHERE shipment_id = ?",
            (shipment_id,),
        ).fetchone()
        if row is None:
            raise UnknownShipmentError(f"Unknown shipment: {shipment_id}")
        return row

    def facility(self, facility_id: str) -> sqlite3.Row:
        row = self.connection.execute(
            "SELECT * FROM facilities WHERE facility_id = ?",
            (facility_id,),
        ).fetchone()
        if row is None:
            raise UnknownShipmentError(f"Unknown facility: {facility_id}")
        return row

    def facility_rules(self, facility_id: str) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT *
            FROM facility_rules
            WHERE facility_id = ? AND active_flag = 1
            ORDER BY rule_type
            """,
            (facility_id,),
        ).fetchall()

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

    def slot_availability(self, slot_id: str) -> sqlite3.Row | None:
        self._expire_stale_holds()
        return self.connection.execute(
            "SELECT * FROM v_slot_availability WHERE slot_id = ?",
            (slot_id,),
        ).fetchone()

    def active_hold_for_slot(self, slot_id: str) -> sqlite3.Row | None:
        self._expire_stale_holds()
        return self.connection.execute(
            """
            SELECT *
            FROM slot_holds
            WHERE slot_id = ? AND hold_status = 'HELD'
            ORDER BY held_at DESC
            LIMIT 1
            """,
            (slot_id,),
        ).fetchone()

    def active_hold_for_shipment(self, shipment_id: str, slot_id: str) -> sqlite3.Row | None:
        self._expire_stale_holds()
        return self.connection.execute(
            """
            SELECT *
            FROM slot_holds
            WHERE shipment_id = ? AND slot_id = ? AND hold_status = 'HELD'
            ORDER BY held_at DESC
            LIMIT 1
            """,
            (shipment_id, slot_id),
        ).fetchone()

    def create_hold(self, shipment_id: str, slot_id: str, ttl_minutes: int) -> HoldResult:
        now = datetime.now().astimezone()
        expires_at = now + timedelta(minutes=ttl_minutes)
        hold_id = f"HLD-{uuid4().hex[:8].upper()}"

        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self._expire_stale_holds(within_transaction=True)

            slot = self.connection.execute(
                "SELECT availability_status FROM v_slot_availability WHERE slot_id = ?",
                (slot_id,),
            ).fetchone()
            if slot is None:
                raise SlotUnavailableError(f"Unknown slot: {slot_id}")
            if slot["availability_status"] not in {"AVAILABLE"}:
                raise SlotUnavailableError("Selected slot is no longer available for hold")

            self.connection.execute(
                """
                INSERT INTO slot_holds (
                    hold_id, slot_id, shipment_id, hold_status, held_at, expires_at
                ) VALUES (?, ?, ?, 'HELD', ?, ?)
                """,
                (
                    hold_id,
                    slot_id,
                    shipment_id,
                    now.isoformat(timespec="seconds"),
                    expires_at.isoformat(timespec="seconds"),
                ),
            )
            self.connection.commit()
        except SlotUnavailableError:
            self.connection.rollback()
            raise
        except Exception:
            self.connection.rollback()
            raise

        return HoldResult(
            hold_id=hold_id,
            slot_id=slot_id,
            shipment_id=shipment_id,
            expires_at=expires_at,
        )

    def release_hold(self, hold_id: str) -> None:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        self.connection.execute(
            """
            UPDATE slot_holds
            SET hold_status = 'RELEASED', released_at = ?
            WHERE hold_id = ? AND hold_status = 'HELD'
            """,
            (now, hold_id),
        )
        self.connection.commit()

    def create_pending_appointment(self, shipment_id: str, slot_id: str) -> str:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        appointment_id = f"APT-PND-{uuid4().hex[:8].upper()}"

        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self._expire_stale_holds(within_transaction=True)

            slot = self.slot_availability(slot_id)
            if slot is None:
                raise SlotUnavailableError(f"Unknown slot: {slot_id}")

            hold = self.active_hold_for_shipment(shipment_id, slot_id)
            if slot["availability_status"] == "HELD" and (
                hold is None or hold["hold_id"] != slot["hold_id"]
            ):
                raise SlotUnavailableError("Slot is held by another shipment")

            if slot["availability_status"] not in {"AVAILABLE", "HELD"}:
                raise SlotUnavailableError("Selected slot is no longer available")

            previous = self.current_appointment(shipment_id)
            if previous and previous["slot_id"] != slot_id:
                self.connection.execute(
                    """
                    UPDATE appointments
                    SET is_current = 0,
                        appointment_status = 'CANCELLED',
                        cancelled_at = ?,
                        updated_at = ?
                    WHERE appointment_id = ?
                    """,
                    (now, now, previous["appointment_id"]),
                )

            existing = self.connection.execute(
                """
                SELECT appointment_id
                FROM appointments
                WHERE shipment_id = ? AND slot_id = ? AND is_current = 1
                  AND appointment_status = 'PENDING_CONFIRMATION'
                """,
                (shipment_id, slot_id),
            ).fetchone()
            if existing:
                self.connection.commit()
                return existing["appointment_id"]

            self.connection.execute(
                """
                INSERT INTO appointments (
                    appointment_id, shipment_id, slot_id, appointment_status,
                    booking_source, is_current, booked_at, confirmed_at,
                    cancelled_at, replaced_appointment_id, warehouse_confirmation_ref,
                    updated_at
                ) VALUES (?, ?, ?, 'PENDING_CONFIRMATION', 'SCHEDULING_TOOL', 1, ?, NULL, NULL, ?, NULL, ?)
                """,
                (
                    appointment_id,
                    shipment_id,
                    slot_id,
                    now,
                    previous["appointment_id"] if previous else None,
                    now,
                ),
            )
            self.connection.commit()
            return appointment_id
        except SlotUnavailableError:
            self.connection.rollback()
            raise
        except Exception:
            self.connection.rollback()
            raise

    def book_after_acceptance(self, shipment_id: str, slot_id: str, accepted: bool) -> str:
        if not accepted:
            raise InvalidBookingError("Explicit driver acceptance is required before booking")

        now = datetime.now().astimezone().isoformat(timespec="seconds")
        appointment_id = f"APT-{shipment_id}-{slot_id.split('-')[-1]}"

        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self._expire_stale_holds(within_transaction=True)

            slot = self.slot_availability(slot_id)
            if slot is None:
                raise SlotUnavailableError(f"Unknown slot: {slot_id}")

            hold = self.active_hold_for_shipment(shipment_id, slot_id)
            pending = self.connection.execute(
                """
                SELECT appointment_id
                FROM appointments
                WHERE shipment_id = ? AND slot_id = ? AND is_current = 1
                  AND appointment_status = 'PENDING_CONFIRMATION'
                """,
                (shipment_id, slot_id),
            ).fetchone()

            if slot["availability_status"] == "HELD":
                if hold is None:
                    raise SlotUnavailableError("Selected slot is held by another shipment")
            elif slot["availability_status"] != "AVAILABLE":
                if pending is None:
                    raise SlotUnavailableError("Selected slot is no longer available")

            previous = self.current_appointment(shipment_id)
            if previous and previous["slot_id"] != slot_id:
                self.connection.execute(
                    """
                    UPDATE appointments
                    SET is_current = 0,
                        appointment_status = 'CANCELLED',
                        cancelled_at = ?,
                        updated_at = ?
                    WHERE appointment_id = ?
                    """,
                    (now, now, previous["appointment_id"]),
                )

            if pending:
                self.connection.execute(
                    """
                    UPDATE appointments
                    SET appointment_status = 'CONFIRMED',
                        confirmed_at = ?,
                        updated_at = ?
                    WHERE appointment_id = ?
                    """,
                    (now, now, pending["appointment_id"]),
                )
                confirmed_id = pending["appointment_id"]
            else:
                self.connection.execute(
                    """
                    INSERT INTO appointments (
                        appointment_id, shipment_id, slot_id, appointment_status,
                        booking_source, is_current, booked_at, confirmed_at,
                        cancelled_at, replaced_appointment_id, warehouse_confirmation_ref,
                        updated_at
                    ) VALUES (?, ?, ?, 'CONFIRMED', 'DRIVER_CHAT', 1, ?, ?, NULL, ?, NULL, ?)
                    """,
                    (
                        appointment_id,
                        shipment_id,
                        slot_id,
                        now,
                        now,
                        previous["appointment_id"] if previous else None,
                        now,
                    ),
                )
                confirmed_id = appointment_id

            if hold:
                self.connection.execute(
                    """
                    UPDATE slot_holds
                    SET hold_status = 'CONVERTED', released_at = ?
                    WHERE hold_id = ?
                    """,
                    (now, hold["hold_id"]),
                )

            self.connection.commit()
            return confirmed_id
        except (InvalidBookingError, SlotUnavailableError):
            self.connection.rollback()
            raise
        except Exception:
            self.connection.rollback()
            raise

    def cancel_pending(self, shipment_id: str, slot_id: str) -> None:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        hold = self.active_hold_for_shipment(shipment_id, slot_id)
        if hold:
            self.connection.execute(
                """
                UPDATE slot_holds
                SET hold_status = 'RELEASED', released_at = ?
                WHERE hold_id = ?
                """,
                (now, hold["hold_id"]),
            )
        self.connection.execute(
            """
            UPDATE appointments
            SET is_current = 0,
                appointment_status = 'CANCELLED',
                cancelled_at = ?,
                updated_at = ?
            WHERE shipment_id = ? AND slot_id = ? AND is_current = 1
              AND appointment_status = 'PENDING_CONFIRMATION'
            """,
            (now, now, shipment_id, slot_id),
        )
        self.connection.commit()

    def _expire_stale_holds(self, within_transaction: bool = False) -> None:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        self.connection.execute(
            """
            UPDATE slot_holds
            SET hold_status = 'EXPIRED', released_at = ?
            WHERE hold_status = 'HELD' AND expires_at <= ?
            """,
            (now, now),
        )
        if not within_transaction:
            self.connection.commit()
