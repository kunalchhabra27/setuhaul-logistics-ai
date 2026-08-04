from datetime import datetime
from pathlib import Path

import pytest

from setuhaul.db.connection import build_database, connect
from setuhaul.db.repository import OperationsRepository
from setuhaul.models import DriverConstraints
from setuhaul.scheduling.engine import DeterministicReschedulingEngine


@pytest.fixture()
def repo(tmp_path):
    root = Path(__file__).resolve().parents[1]
    db_path = tmp_path / "test.db"
    build_database(root / "data" / "setuhaul_schema_and_seed.sql", db_path)
    connection = connect(db_path)
    yield OperationsRepository(connection)
    connection.close()


def test_returns_only_slots_after_effective_eta(repo):
    suggestions = DeterministicReschedulingEngine(repo).suggest("SHP1006")
    eta = datetime.fromisoformat(repo.shipment("SHP1006")["effective_eta_ts"])
    assert suggestions
    assert all(item.start >= eta for item in suggestions)


def test_respects_driver_deadline(repo):
    deadline = datetime.fromisoformat("2026-08-04T13:00:00+05:30")
    suggestions = DeterministicReschedulingEngine(repo).suggest(
        "SHP1006", DriverConstraints(must_finish_by=deadline)
    )
    assert all(item.end <= deadline for item in suggestions)


def test_cancelled_shipment_has_no_options(repo):
    assert DeterministicReschedulingEngine(repo).suggest("SHP1019") == []


def test_booking_requires_explicit_acceptance(repo):
    with pytest.raises(ValueError, match="Explicit driver acceptance"):
        repo.book_after_acceptance("SHP1006", "SLOT-JAI-020", accepted=False)
