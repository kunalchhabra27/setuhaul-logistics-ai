from __future__ import annotations

from postgrest.exceptions import APIError

from setuhaul.backend.driver_chat_eta.repository import DriverChatRepository

_MISSING_COLUMN_ERROR = {
    "message": "Could not find the 'auth_user_id' column of 'drivers' in the schema cache",
    "code": "PGRST204",
    "details": None,
    "hint": None,
}


class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    """Minimal stand-in for a postgrest query builder chain."""

    def __init__(self, table: "_FakeTable", op: str, payload=None):
        self.table = table
        self.op = op
        self.payload = payload

    def eq(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        return self.table._run(self.op, self.payload)


class _FakeTable:
    """Fake ``drivers`` table that rejects any payload/filter touching a
    column not present in ``existing_columns`` -- mirrors PostgREST's
    behaviour when the schema cache doesn't know about a column yet."""

    def __init__(self, existing_columns, rows):
        self.existing_columns = existing_columns
        self.rows = rows

    def select(self, *_args, **_kwargs):
        return _FakeQuery(self, "select")

    def upsert(self, payload, on_conflict=None):
        return _FakeQuery(self, "upsert", payload)

    def _run(self, op, payload):
        if op == "select":
            if "auth_user_id" not in self.existing_columns:
                raise APIError(_MISSING_COLUMN_ERROR)
            return _FakeResponse(self.rows)
        if op == "upsert":
            if "auth_user_id" in payload and "auth_user_id" not in self.existing_columns:
                raise APIError(_MISSING_COLUMN_ERROR)
            self.rows = [payload]
            return _FakeResponse(self.rows)
        raise AssertionError(f"unexpected op {op}")


class _FakeClient:
    def __init__(self, table: _FakeTable):
        self._table = table

    def table(self, name):
        assert name == "drivers"
        return self._table


def test_get_driver_by_auth_user_id_degrades_to_none_when_column_missing():
    table = _FakeTable(existing_columns={"driver_id"}, rows=[])
    repo = DriverChatRepository(_FakeClient(table))

    assert repo.get_driver_by_auth_user_id("auth-uuid-123") is None


def test_upsert_driver_falls_back_when_auth_user_id_column_missing():
    table = _FakeTable(existing_columns={"driver_id"}, rows=[])
    repo = DriverChatRepository(_FakeClient(table))

    row = repo.upsert_driver("DRV001", {"auth_user_id": "auth-uuid-123", "driver_name": "Ravi"})

    assert row["driver_id"] == "DRV001"
    assert "auth_user_id" not in row


def test_upsert_driver_persists_auth_user_id_once_column_exists():
    table = _FakeTable(existing_columns={"driver_id", "auth_user_id"}, rows=[])
    repo = DriverChatRepository(_FakeClient(table))

    row = repo.upsert_driver("DRV001", {"auth_user_id": "auth-uuid-123", "driver_name": "Ravi"})

    assert row["auth_user_id"] == "auth-uuid-123"
