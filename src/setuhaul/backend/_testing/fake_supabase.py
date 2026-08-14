"""A minimal in-memory stand-in for the supabase-py client's query builder.

Exercises the same chained-call surface our repositories use (`.table(name)
.select(cols).eq(...).in_(...).lte(...).order(...).limit(n).execute()`, plus
`.insert(payload).execute()` / `.update(payload).eq(...).execute()`), backed
by plain Python lists of dicts instead of a real Postgres connection. This
lets repository unit tests exercise the actual query-chain code (catching
wrong method names/argument shapes) without needing network access to
Supabase.

Deliberately not a full PostgREST reimplementation -- only the filter/order
operations our repositories actually call are supported.
"""

from __future__ import annotations

from typing import Any


class FakeResponse:
    def __init__(self, data: list[dict[str, Any]]):
        self.data = data


class _FakeNotProxy:
    """Backs the `.not_.in_(...)` chain postgrest-py exposes for negated
    filters -- e.g. `.not_.in_("status", ["DONE"])` for "status NOT IN
    (...)". Appends a negated filter onto the same underlying query and
    returns it, so chaining (.order(), .limit(), ...) continues normally."""

    def __init__(self, query: "FakeQuery"):
        self._query = query

    def in_(self, column: str, values: Any) -> "FakeQuery":
        self._query._filters.append(("not_in", column, list(values)))
        return self._query

    def eq(self, column: str, value: Any) -> "FakeQuery":
        self._query._filters.append(("not_eq", column, value))
        return self._query

    def is_(self, column: str, value: Any) -> "FakeQuery":
        self._query._filters.append(("not_is", column, None if value in ("null", None) else value))
        return self._query


class FakeQuery:
    def __init__(
        self,
        table: "FakeTable",
        op: str,
        payload: dict[str, Any] | None = None,
        on_conflict: str | None = None,
    ):
        self._table = table
        self._op = op
        self._payload = payload
        self._on_conflict = on_conflict
        self._filters: list[tuple[str, str, Any]] = []
        self._order_by: list[tuple[str, bool]] = []
        self._limit_n: int | None = None

    @property
    def not_(self) -> "_FakeNotProxy":
        return _FakeNotProxy(self)

    def eq(self, column: str, value: Any) -> "FakeQuery":
        self._filters.append(("eq", column, value))
        return self

    def in_(self, column: str, values: Any) -> "FakeQuery":
        self._filters.append(("in", column, list(values)))
        return self

    def is_(self, column: str, value: Any) -> "FakeQuery":
        self._filters.append(("is", column, None if value in ("null", None) else value))
        return self

    def lte(self, column: str, value: Any) -> "FakeQuery":
        self._filters.append(("lte", column, value))
        return self

    def gte(self, column: str, value: Any) -> "FakeQuery":
        self._filters.append(("gte", column, value))
        return self

    def order(self, column: str, desc: bool = False, **_kwargs: Any) -> "FakeQuery":
        self._order_by.append((column, desc))
        return self

    def range(self, start: int, end: int) -> "FakeQuery":
        self._limit_n = (end - start) + 1
        return self

    def limit(self, n: int) -> "FakeQuery":
        self._limit_n = n
        return self

    def _matches(self, row: dict[str, Any]) -> bool:
        for kind, column, value in self._filters:
            actual = row.get(column)
            if kind == "eq" and actual != value:
                return False
            if kind == "in" and actual not in value:
                return False
            if kind == "is" and actual != value:
                return False
            if kind == "lte" and not (actual is not None and actual <= value):
                return False
            if kind == "gte" and not (actual is not None and actual >= value):
                return False
            if kind == "not_in" and actual in value:
                return False
            if kind == "not_eq" and actual == value:
                return False
            if kind == "not_is" and actual == value:
                return False
        return True

    def execute(self) -> FakeResponse:
        if self._op == "select":
            rows = [row for row in self._table.data if self._matches(row)]
            for column, desc in reversed(self._order_by):
                rows.sort(key=lambda r: (r.get(column) is None, r.get(column)), reverse=desc)
            if self._limit_n is not None:
                rows = rows[: self._limit_n]
            return FakeResponse([dict(row) for row in rows])
        if self._op == "insert":
            payloads = self._payload if isinstance(self._payload, list) else [self._payload or {}]
            new_rows = [dict(row) for row in payloads]
            self._table.data.extend(new_rows)
            return FakeResponse([dict(row) for row in new_rows])
        if self._op == "update":
            matched = [row for row in self._table.data if self._matches(row)]
            for row in matched:
                row.update(self._payload or {})
            return FakeResponse([dict(row) for row in matched])
        if self._op == "upsert":
            payloads = self._payload if isinstance(self._payload, list) else [self._payload or {}]
            key = self._on_conflict or "id"
            results: list[dict[str, Any]] = []
            for payload in payloads:
                existing = next((row for row in self._table.data if row.get(key) == payload.get(key)), None)
                if existing is not None:
                    existing.update(payload)
                    results.append(dict(existing))
                else:
                    new_row = dict(payload)
                    self._table.data.append(new_row)
                    results.append(dict(new_row))
            return FakeResponse(results)
        raise AssertionError(f"Unsupported fake query op: {self._op}")


class FakeTable:
    def __init__(self, data: list[dict[str, Any]]):
        self.data = data

    def select(self, _columns: str = "*") -> FakeQuery:
        return FakeQuery(self, "select")

    def insert(self, payload: dict[str, Any] | list[dict[str, Any]]) -> FakeQuery:
        return FakeQuery(self, "insert", payload)

    def update(self, payload: dict[str, Any]) -> FakeQuery:
        return FakeQuery(self, "update", payload)

    def upsert(
        self, payload: dict[str, Any] | list[dict[str, Any]], on_conflict: str | None = None, **_kwargs: Any
    ) -> FakeQuery:
        return FakeQuery(self, "upsert", payload, on_conflict=on_conflict)


class FakeSupabaseClient:
    """Drop-in stand-in for `supabase.Client` limited to `.table(name)`."""

    def __init__(self, tables: dict[str, list[dict[str, Any]]] | None = None):
        self._tables = {name: FakeTable(rows) for name, rows in (tables or {}).items()}

    def table(self, name: str) -> FakeTable:
        if name not in self._tables:
            self._tables[name] = FakeTable([])
        return self._tables[name]
