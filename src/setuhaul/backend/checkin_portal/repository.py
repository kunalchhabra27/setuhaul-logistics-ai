"""Persistence helpers for the check-in portal domain."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any


class CheckInRepository:
    """Access facility check-in records stored in SQLite."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.connection.row_factory = sqlite3.Row

    def get_by_shipment(self, shipment_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM facility_checkins WHERE shipment_id = ?",
            (shipment_id,),
        ).fetchone()
        if row is None:
            return None
        record = dict(row)
        return {
            **record,
            "arrival_status": self._to_domain_arrival_status(record.get("arrival_state")),
            "queue_status": self._to_domain_queue_status(record.get("queue_state")),
            "gate_in_at": record.get("gate_in_ts"),
            "dock_in_at": record.get("dock_in_ts"),
            "completed_at": record.get("unload_end_ts"),
        }

    def create_gate_checkin(self, checkin_id: str, shipment_id: str, facility_id: str, gate_in_at: str | datetime) -> None:
        self.connection.execute(
            """
            INSERT INTO facility_checkins (
                checkin_id, shipment_id, facility_id,
                gate_in_ts, arrival_state, queue_state, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                checkin_id,
                shipment_id,
                facility_id,
                gate_in_at,
                self._to_db_arrival_status("GATE_IN"),
                self._to_db_queue_status("GATE_QUEUE"),
                gate_in_at,
            ),
        )
        self.connection.commit()

    def update_queue(self, shipment_id: str, queue_status: str) -> None:
        self.connection.execute(
            """
            UPDATE facility_checkins
            SET arrival_state = ?, queue_state = ?, updated_at = CURRENT_TIMESTAMP
            WHERE shipment_id = ?
            """,
            (self._to_db_arrival_status("WAITING"), self._to_db_queue_status(queue_status), shipment_id),
        )
        self.connection.commit()

    def mark_docked(self, shipment_id: str, dock_in_at: str | datetime) -> None:
        self.connection.execute(
            """
            UPDATE facility_checkins
            SET arrival_state = ?, queue_state = ?, dock_in_ts = ?, updated_at = CURRENT_TIMESTAMP
            WHERE shipment_id = ?
            """,
            (
                self._to_db_arrival_status("DOCKED"),
                self._to_db_queue_status("NONE"),
                dock_in_at,
                shipment_id,
            ),
        )
        self.connection.commit()

    def mark_completed(self, shipment_id: str, completed_at: str | datetime) -> None:
        self.connection.execute(
            """
            UPDATE facility_checkins
            SET arrival_state = ?, queue_state = ?, unload_end_ts = ?, updated_at = CURRENT_TIMESTAMP
            WHERE shipment_id = ?
            """,
            (
                self._to_db_arrival_status("COMPLETED"),
                self._to_db_queue_status("NONE"),
                completed_at,
                shipment_id,
            ),
        )
        self.connection.commit()

    @staticmethod
    def _to_domain_arrival_status(value: str | None) -> str | None:
        mapping = {
            "EARLY": "GATE_IN",
            "ON_TIME": "WAITING",
            "LATE": "DOCKED",
            "NO_SHOW": "COMPLETED",
        }
        return mapping.get(value, value)

    @staticmethod
    def _to_domain_queue_status(value: str | None) -> str | None:
        mapping = {
            "NOT_QUEUED": "NONE",
            "WAITING_EARLY": "GATE_QUEUE",
            "WAITING_LATE": "YARD_QUEUE",
            "WAITING_DOCK_UNAVAILABLE": "YARD_QUEUE",
            "CALLED_TO_DOCK": "CALLED_TO_DOCK",
            "IN_DOCK": "CALLED_TO_DOCK",
            "COMPLETED": "NONE",
        }
        return mapping.get(value, value)

    @staticmethod
    def _to_db_arrival_status(value: str) -> str:
        mapping = {
            "GATE_IN": "EARLY",
            "WAITING": "ON_TIME",
            "DOCKED": "LATE",
            "COMPLETED": "NO_SHOW",
        }
        return mapping.get(value, value)

    @staticmethod
    def _to_db_queue_status(value: str) -> str:
        mapping = {
            "NONE": "NOT_QUEUED",
            "GATE_QUEUE": "WAITING_EARLY",
            "YARD_QUEUE": "WAITING_LATE",
            "CALLED_TO_DOCK": "CALLED_TO_DOCK",
        }
        return mapping.get(value, value)
