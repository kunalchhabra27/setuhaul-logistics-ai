"""Persistence helpers for the check-in portal domain."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any


class CheckInRepository:
    """Access facility check-in records stored in SQLite."""

    def __init__(self, connection: sqlite3.Connection):
        """Initialize the repository with a SQLite connection."""
        self.connection = connection
        self.connection.row_factory = sqlite3.Row

    def get_by_shipment(self, shipment_id: str) -> dict[str, Any] | None:
        """Return the latest check-in row for a shipment, if one exists."""
        row = self.connection.execute(
            """
            SELECT *
            FROM facility_checkins
            WHERE shipment_id = ?
            """,
            (shipment_id,),
        ).fetchone()

        if row is None:
            return None
        record = dict(row)
        return {
            **record,
            "arrival_status": record.get("arrival_state"),
            "queue_status": record.get("queue_state"),
            "gate_in_at": record.get("gate_in_ts"),
            "dock_in_at": record.get("dock_in_ts"),
            "completed_at": record.get("unload_end_ts"),
        }

    def create_gate_checkin(
        self,
        checkin_id: str,
        shipment_id: str,
        facility_id: str,
        gate_in_at: str | datetime,
    ) -> None:
        """Create an initial gate-in record for a shipment."""
        self.connection.execute(
            """
            INSERT INTO facility_checkins (
                checkin_id,
                shipment_id,
                facility_id,
                gate_in_ts,
                arrival_state,
                queue_state,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                checkin_id,
                shipment_id,
                facility_id,
                gate_in_at,
                "GATE_IN",
                "GATE_QUEUE",
                gate_in_at,
            ),
        )

        self.connection.commit()

    def update_queue(self, shipment_id: str, queue_status: str) -> None:
        """Update the queue status for an existing shipment check-in."""
        self.connection.execute(
            """
            UPDATE facility_checkins
            SET arrival_state = 'WAITING',
                queue_state = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE shipment_id = ?
            """,
            (queue_status, shipment_id),
        )
        self.connection.commit()

    def mark_docked(self, shipment_id: str, dock_in_at: str | datetime) -> None:
        """Mark a shipment as docked at the facility."""
        self.connection.execute(
            """
            UPDATE facility_checkins
            SET arrival_state = 'DOCKED',
                queue_state = 'NONE',
                dock_in_ts = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE shipment_id = ?
            """,
            (dock_in_at, shipment_id),
        )
        self.connection.commit()

    def mark_completed(self, shipment_id: str, completed_at: str | datetime) -> None:
        """Mark a shipment as completed at the facility."""
        self.connection.execute(
            """
            UPDATE facility_checkins
            SET arrival_state = 'COMPLETED',
                queue_state = 'NONE',
                unload_end_ts = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE shipment_id = ?
            """,
            (completed_at, shipment_id),
        )
        self.connection.commit()

    def backend_relationships(self) -> list[dict[str, Any]]:
        """Return lightweight relationship metadata for neighboring backend systems."""
        return [
            {
                "system": "TMS",
                "label": "Transport Management System",
                "owns": [
                    "Shipment identity",
                    "Vehicle assignment",
                    "Driver assignment",
                    "Destination facility",
                    "Shipment status",
                ],
                "consumes": [],
                "notes": "Check-in Portal may use TMS data to validate the destination facility.",
            },
            {
                "system": "DOCK_SCHEDULER",
                "label": "Dock Scheduler / WMS",
                "owns": [
                    "Docks",
                    "Appointment slots",
                    "Appointments",
                    "Scheduling decisions",
                ],
                "consumes": [
                    "Check-in state",
                    "Gate-in status",
                    "Dock status",
                    "Completion status",
                ],
                "notes": "Dock Scheduler uses Check-in Portal state to assess operational feasibility.",
            },
            {
                "system": "DRIVER_CHAT_ETA",
                "label": "Driver Chat / ETA Portal",
                "owns": [
                    "Driver messages",
                    "Exceptions",
                    "Declared ETA updates",
                    "Conversation threads",
                ],
                "consumes": [],
                "notes": "Driver ETA updates do not define the actual warehouse arrival state.",
            },
        ]
