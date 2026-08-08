from datetime import datetime
from pathlib import Path
from threading import Thread

import pytest

from setuhaul.backend.dock_scheduler.exceptions import (
    InvalidBookingError,
    SlotUnavailableError,
)
from setuhaul.backend.dock_scheduler.models import DriverConstraints
from setuhaul.backend.dock_scheduler.repository import DockSchedulerRepository
from setuhaul.backend.dock_scheduler.service import DockSchedulerService
from setuhaul.db.connection import build_database, connect
from setuhaul.scheduling.engine import DeterministicReschedulingEngine


@pytest.fixture()
def repo(tmp_path):
    root = Path(__file__).resolve().parents[1]
    db_path = tmp_path / "test.db"
    build_database(root / "data" / "setuhaul_schema_and_seed.sql", db_path)
    connection = connect(db_path)
    yield DockSchedulerRepository(connection)
    connection.close()


@pytest.fixture()
def service(repo):
    return DockSchedulerService(repo)


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
    with pytest.raises(InvalidBookingError, match="Explicit driver acceptance"):
        repo.book_after_acceptance("SHP1006", "SLOT-JAI-020", accepted=False)


def test_reefer_shipment_only_gets_reefer_docks(repo):
    suggestions = DeterministicReschedulingEngine(repo).suggest("SHP1010", limit=10)
    assert suggestions
    assert all(item.dock_code == "D5" for item in suggestions)


def test_hold_marks_slot_unavailable_for_others(service, repo):
    hold = service.hold_slot("SHP1006", "SLOT-JAI-020")
    slot = repo.slot_availability("SLOT-JAI-020")
    assert slot["availability_status"] == "HELD"
    assert slot["held_shipment_id"] == "SHP1006"
    assert hold.hold_id


def test_confirm_after_hold_and_pending(service, repo):
    service.hold_slot("SHP1006", "SLOT-JAI-021")
    service.request_confirmation("SHP1006", "SLOT-JAI-021")
    appointment_id = service.confirm_booking("SHP1006", "SLOT-JAI-021", accepted=True)
    slot = repo.slot_availability("SLOT-JAI-021")
    assert slot["availability_status"] == "OCCUPIED"
    assert appointment_id


def test_concurrent_booking_only_one_succeeds(tmp_path):
    root = Path(__file__).resolve().parents[1]
    db_path = tmp_path / "concurrent.db"
    build_database(root / "data" / "setuhaul_schema_and_seed.sql", db_path)

    slot_id = "SLOT-JAI-022"
    results: list[str | BaseException] = []

    def attempt(shipment_id: str) -> None:
        connection = connect(db_path)
        repository = DockSchedulerRepository(connection)
        try:
            repository.create_hold(shipment_id, slot_id, ttl_minutes=5)
            appointment_id = repository.book_after_acceptance(shipment_id, slot_id, accepted=True)
            results.append(appointment_id)
        except BaseException as exc:
            results.append(exc)
        finally:
            connection.close()

    threads = [Thread(target=attempt, args=("SHP1006",)), Thread(target=attempt, args=("SHP1012",))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    successes = [item for item in results if isinstance(item, str)]
    failures = [item for item in results if isinstance(item, SlotUnavailableError)]
    assert len(successes) == 1
    assert len(failures) == 1
