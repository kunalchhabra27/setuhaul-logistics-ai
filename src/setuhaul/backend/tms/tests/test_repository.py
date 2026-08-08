from types import SimpleNamespace
from uuid import UUID

from postgrest.exceptions import APIError

from setuhaul.backend.tms.exceptions import ConflictError
from setuhaul.backend.tms.models import ShipmentStatus
from setuhaul.backend.tms.repository import TMSRepository


class Query:
    def __init__(self, rows, fail=None):
        self.rows = rows
        self.fail = fail
        self.filters = []
        self.payload = None

    def select(self, *_): return self
    def eq(self, key, value): self.filters.append(("eq", key, value)); return self
    def in_(self, key, values): self.filters.append(("in", key, values)); return self
    def limit(self, *_): return self
    def order(self, *_args, **_kwargs): return self
    def range(self, *_): return self
    def insert(self, payload): self.payload = payload; return self
    def update(self, payload): self.payload = payload; return self

    def execute(self):
        if self.fail:
            raise self.fail
        data = self.rows
        for operation, key, value in self.filters:
            if operation == "eq": data = [row for row in data if row.get(key) == value]
            else: data = [row for row in data if row.get(key) in value]
        if self.payload is not None:
            data = [{**data[0], **self.payload}] if data else [self.payload]
        return SimpleNamespace(data=data)


class Client:
    def __init__(self, rows, fail=None): self.rows, self.fail, self.query = rows, fail, None
    def table(self, _): self.query = Query(self.rows, self.fail); return self.query


def test_repository_fetches_driver_by_uuid():
    driver_id = UUID(int=1)
    client = Client([{"driver_id": str(driver_id)}])
    assert TMSRepository(client).get_driver(driver_id)["driver_id"] == str(driver_id)


def test_repository_applies_shipment_filters():
    client = Client([])
    TMSRepository(client).list_shipments(
        driver_id=UUID(int=1), status=ShipmentStatus.IN_TRANSIT, limit=25, offset=10
    )
    assert ("eq", "driver_id", str(UUID(int=1))) in client.query.filters
    assert ("eq", "status", "in_transit") in client.query.filters


def test_duplicate_database_error_maps_to_conflict():
    error = APIError({"code": "23505", "message": "duplicate", "details": None, "hint": None})
    client = Client([], fail=error)
    try:
        TMSRepository(client).create_driver({"driver_code": "DRV-001"})
    except ConflictError as exc:
        assert "same unique identifier" in str(exc)
    else:
        raise AssertionError("Expected ConflictError")
