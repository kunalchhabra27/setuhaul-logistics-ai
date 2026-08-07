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

        return dict(row) if row else None

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
                gate_in_at,
                arrival_status,
                queue_status
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                checkin_id,
                shipment_id,
                facility_id,
                gate_in_at,
                "GATE_IN",
                "GATE_QUEUE",
            ),
        )

        self.connection.commit()

    def update_queue(self, shipment_id: str, queue_status: str) -> None:
        """Update the queue status for an existing shipment check-in."""
        self.connection.execute(
            """
            UPDATE facility_checkins
            SET queue_status = ?
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
            SET arrival_status = 'DOCKED',
                dock_in_at = ?
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
            SET arrival_status = 'COMPLETED',
                completed_at = ?
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
                "owns": [
                    "shipment identity",
                    "vehicle assignment",
                    "driver assignment",
                    "destination facility",
                    "shipment status",
                ],
                "consumes": [],
                "notes": "Check-in Portal may use TMS data to validate destination facility.",
            },
            {
                "system": "DOCK_SCHEDULER",
                "owns": [
                    "docks",
                    "appointment slots",
                    "appointments",
                    "scheduling decisions",
                ],
                "consumes": [
                    "check-in state",
                    "gate-in status",
                    "dock status",
                    "completion status",
                ],
                "notes": "Dock Scheduler uses check-in state to assess operational feasibility.",
            },
            {
                "system": "DRIVER_CHAT_ETA",
                "owns": [
                    "driver messages",
                    "exceptions",
                    "declared ETA updates",
                    "conversation threads",
                ],
                "consumes": [],
                "notes": "Driver ETA updates do not define actual warehouse arrival state.",
            },
        ]
