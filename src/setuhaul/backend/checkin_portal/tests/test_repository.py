from __future__ import annotations

import sqlite3

from setuhaul.backend.checkin_portal.repository import CheckInRepository


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        """
        CREATE TABLE facility_checkins (
            checkin_id TEXT PRIMARY KEY,
            shipment_id TEXT NOT NULL UNIQUE,
            facility_id TEXT NOT NULL,
            gate_in_ts TEXT,
            yard_queue_enter_ts TEXT,
            dock_in_ts TEXT,
            unload_start_ts TEXT,
            unload_end_ts TEXT,
            gate_out_ts TEXT,
            arrival_state TEXT,
            queue_state TEXT,
            queue_position INTEGER,
            actual_dock_id TEXT,
            notes TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    return connection


def test_repository_crud_round_trip() -> None:
    connection = _connection()
    repo = CheckInRepository(connection)

    repo.create_gate_checkin("CHK-1", "SHP1006", "FAC-1", "2026-08-08T10:00:00")
    assert repo.get_by_shipment("SHP1006")["arrival_status"] == "GATE_IN"

    repo.update_queue("SHP1006", "YARD_QUEUE")
    assert repo.get_by_shipment("SHP1006")["queue_status"] == "YARD_QUEUE"

    repo.mark_docked("SHP1006", "2026-08-08T11:00:00")
    assert repo.get_by_shipment("SHP1006")["arrival_status"] == "DOCKED"

    repo.mark_completed("SHP1006", "2026-08-08T12:00:00")
    assert repo.get_by_shipment("SHP1006")["arrival_status"] == "COMPLETED"
