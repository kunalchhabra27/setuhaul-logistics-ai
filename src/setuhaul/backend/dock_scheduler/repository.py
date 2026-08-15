"""Persistence boundary for dock scheduling (public.appointment_slots / docks /
appointments / slot_holds / facilities / facility_rules / facility_checkins).

Rewritten against the real Supabase project (verified live on 2026-08-10) --
this used to run against a local SQLite database seeded from
`data/setuhaul_schema_and_seed.sql`. That seed file turned out to be an
accurate mirror of the real Supabase schema (table/column names and value
vocabularies both matched what a live schema probe returned), so the
column/value semantics below are unchanged from the SQLite version. What
changed is the persistence layer itself: the SQLite version leaned on two
SQL views (`v_inbound_operational_state`, `v_slot_availability`) plus
`BEGIN IMMEDIATE` transactions for hold locking. Supabase's PostgREST API
has no equivalent for either -- there are no ad-hoc views and no
multi-statement transactions over REST -- so both are reimplemented here as
plain Python queries/joins. See `_operational_state` and `compatible_slots`
for the view replacements.

Concurrency note: hold creation here is check-then-insert at the application
level rather than the SQLite version's `BEGIN IMMEDIATE` pessimistic lock.
PostgREST does not expose transactions, so a narrow race window exists
between the availability check and the insert. Acceptable for this
demo-scale scheduler; a production version would want a Postgres RPC
function to make hold creation atomic.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, TYPE_CHECKING
from uuid import uuid4

from postgrest.exceptions import APIError

from setuhaul.backend.dock_scheduler.constraints import FacilityConstraintEvaluator
from setuhaul.backend.dock_scheduler.exceptions import (
    DockSchedulerError,
    InvalidBookingError,
    SlotUnavailableError,
    UnknownShipmentError,
)
from setuhaul.backend.dock_scheduler.models import HoldResult, SlotLifecycleStage

if TYPE_CHECKING:
    from supabase import Client
else:
    Client = Any

ACTIVE_APPOINTMENT_STATUSES = ("PENDING_CONFIRMATION", "CONFIRMED", "IN_PROGRESS")
PRIORITY_WEIGHT = {"LOW": 1, "NORMAL": 2, "HIGH": 3, "CRITICAL": 4}

# Process-wide, best-effort cache so ensure_future_slots() doesn't re-run its
# per-dock "am I topped up" check on every single board/feasibility read.
# driver_chat_eta's snapshot alone is polled every 8 seconds per active
# driver session -- without this, every one of those polls would fire an
# extra docks-select plus one small query per dock, forever, just to
# usually conclude "nothing to do". A facility only actually needs
# backfilling once every few days (see DockSchedulerRepository.
# FUTURE_SLOT_HORIZON_DAYS), so rechecking it every 6 hours is already
# generous. Tests clear this between runs (see dock_scheduler/tests/
# conftest.py and driver_chat_eta/tests/conftest.py) so one test's cached
# "already checked" result can't hide a real bug in another test's fixture.
_FUTURE_SLOTS_LAST_CHECKED: dict[str, datetime] = {}
_FUTURE_SLOTS_RECHECK_INTERVAL = timedelta(hours=6)


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class PersistenceError(DockSchedulerError):
    """Raised when an unexpected Supabase/PostgREST error occurs."""


class DockSchedulerRepository:
    """Persistence boundary for facilities, slots, appointments, and holds."""

    # See ensure_future_slots() -- how many days of OPEN capacity board/
    # feasibility reads try to keep available ahead of "now".
    FUTURE_SLOT_HORIZON_DAYS = 7

    def __init__(self, backend: Client):
        self.backend = backend
        # Per-instance-lifetime cache (one DockSchedulerRepository is built
        # fresh per HTTP request, see api.get_service) -- compatible_slots()
        # is a multi-query fetch (docks + a bounded appointment_slots scan +
        # appointments + shipments + slot_holds, see below) that can get
        # called more than once for the same shipment within one request
        # (e.g. DriverChatService.auto_book_earliest_feasible_slot calls it
        # once directly via _feasible_slots and again via
        # _best_priority_swap -> suggest_slots -> engine.suggest). Caching
        # here avoids paying for that scan twice. Invalidated by every
        # mutation method below that could change what it returns, so it
        # can never serve stale availability_status within the same
        # request.
        self._compatible_slots_cache: dict[str, list[dict[str, Any]]] = {}

    @staticmethod
    def _data(response: Any) -> list[dict[str, Any]]:
        return list(response.data or [])

    def _raise_persistence(self, exc: APIError) -> None:
        raise PersistenceError(str(getattr(exc, "message", "Dock scheduler database operation failed."))) from exc

    def _select(self, table: str, **filters: Any) -> list[dict[str, Any]]:
        try:
            query = self.backend.table(table).select("*")
            for key, value in filters.items():
                if isinstance(value, (list, tuple, set)):
                    query = query.in_(key, list(value))
                else:
                    query = query.eq(key, value)
            return self._data(query.execute())
        except APIError as exc:
            self._raise_persistence(exc)

    # -- shipment operational state (replaces v_inbound_operational_state) --

    def _operational_state(self, shipment_id: str) -> dict[str, Any]:
        rows = self._select("shipments", shipment_id=shipment_id)
        if not rows:
            raise UnknownShipmentError(f"Unknown shipment: {shipment_id}")
        shipment = rows[0]

        # v_latest_eta: most recent driver-declared ETA, falling back to the
        # shipment's own latest/original ETA columns.
        eta_rows = (
            self.backend.table("eta_updates")
            .select("*")
            .eq("shipment_id", shipment_id)
            .order("created_at", desc=True)
            .order("eta_update_id", desc=True)
            .limit(1)
            .execute()
        )
        eta_rows = self._data(eta_rows)
        latest_declared = eta_rows[0]["declared_eta_ts"] if eta_rows else None
        effective_eta_ts = latest_declared or shipment.get("latest_eta_ts") or shipment.get("original_eta_ts")

        appointment_rows = self._select(
            "appointments",
            shipment_id=shipment_id,
            is_current=1,
            appointment_status=list(ACTIVE_APPOINTMENT_STATUSES),
        )
        appointment = appointment_rows[0] if appointment_rows else None

        planned_dock_code = None
        slot_start_ts = None
        slot_end_ts = None
        slot_id = None
        if appointment:
            slot_id = appointment["slot_id"]
            slot_rows = self._select("appointment_slots", slot_id=slot_id)
            if slot_rows:
                slot_start_ts = slot_rows[0]["slot_start_ts"]
                slot_end_ts = slot_rows[0]["slot_end_ts"]
                dock_rows = self._select("docks", dock_id=slot_rows[0]["dock_id"])
                if dock_rows:
                    planned_dock_code = dock_rows[0]["dock_code"]

        checkin_rows = self._select("facility_checkins", shipment_id=shipment_id)
        checkin = checkin_rows[0] if checkin_rows else None

        return {
            "shipment_id": shipment["shipment_id"],
            "driver_id": shipment.get("driver_id"),
            "vehicle_id": shipment.get("vehicle_id"),
            "destination_facility_id": shipment["destination_facility_id"],
            "priority_code": shipment["priority_code"],
            "required_dock_type": shipment["required_dock_type"],
            "temperature_control_required": shipment["temperature_control_required"],
            "load_weight_kg": shipment["load_weight_kg"],
            "expected_unload_min": shipment["expected_unload_min"],
            "current_status": shipment["current_status"],
            "effective_eta_ts": effective_eta_ts,
            "appointment_id": appointment["appointment_id"] if appointment else None,
            "slot_id": slot_id,
            "slot_start_ts": slot_start_ts,
            "slot_end_ts": slot_end_ts,
            "planned_dock_code": planned_dock_code,
            "gate_in_ts": checkin.get("gate_in_ts") if checkin else None,
            "queue_state": checkin.get("queue_state") if checkin else None,
            "queue_position": checkin.get("queue_position") if checkin else None,
        }

    def shipment(self, shipment_id: str) -> dict[str, Any]:
        return self._operational_state(shipment_id)

    def facility(self, facility_id: str) -> dict[str, Any]:
        rows = self._select("facilities", facility_id=facility_id)
        if not rows:
            raise UnknownShipmentError(f"Unknown facility: {facility_id}")
        return rows[0]

    def facility_rules(self, facility_id: str) -> list[dict[str, Any]]:
        rows = self._select("facility_rules", facility_id=facility_id, active_flag=1)
        return sorted(rows, key=lambda row: row["rule_type"])

    # -- slot availability (replaces v_slot_availability) --------------------

    def _slot_rows_with_availability(
        self, slots: list[dict[str, Any]], docks_by_id: dict[str, dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Attach availability_status/occupant/hold info to raw slot rows."""
        if not slots:
            return []

        # Deliberately NOT filtered by `.in_("slot_id", [...])` over this
        # facility's slots: neither `appointments` nor `slot_holds` has a
        # facility_id column to scope by directly, and slots can easily
        # number in the hundreds once a few weeks of ensure_future_slots()
        # backfill have accumulated (every dock, every open hour, every
        # day). Enumerating that many ids into one request URL was slow
        # enough to make the app feel unresponsive and, in the worst case,
        # failed outright -- this is what was crashing the driver chatbot
        # with 500s. Both tables are naturally small regardless of how many
        # slots exist -- appointments only for currently-active bookings,
        # holds only for the last few minutes before they expire -- so
        # fetching by status alone and joining locally is both correct and
        # far cheaper than enumerating every slot id.
        occupant_rows = self._select("appointments", appointment_status=list(ACTIVE_APPOINTMENT_STATUSES))
        occupant_by_slot = {row["slot_id"]: row for row in occupant_rows}
        occupant_shipment_ids = [row["shipment_id"] for row in occupant_rows]
        occupant_shipments = (
            {row["shipment_id"]: row for row in self._select("shipments", shipment_id=occupant_shipment_ids)}
            if occupant_shipment_ids
            else {}
        )

        self._expire_stale_holds()
        hold_rows = self._select("slot_holds", hold_status="HELD")
        hold_by_slot = {row["slot_id"]: row for row in hold_rows}

        result: list[dict[str, Any]] = []
        for slot in slots:
            dock = docks_by_id.get(slot["dock_id"], {})
            occ_appointment = occupant_by_slot.get(slot["slot_id"])
            hold = hold_by_slot.get(slot["slot_id"])

            if slot["slot_status"] != "OPEN":
                availability_status = slot["slot_status"]
            elif occ_appointment is not None:
                availability_status = "OCCUPIED"
            elif hold is not None:
                availability_status = "HELD"
            else:
                availability_status = "AVAILABLE"

            occ_shipment = occupant_shipments.get(occ_appointment["shipment_id"]) if occ_appointment else None

            result.append(
                {
                    "slot_id": slot["slot_id"],
                    "facility_id": slot["facility_id"],
                    "dock_id": slot["dock_id"],
                    "dock_code": dock.get("dock_code"),
                    "dock_type": dock.get("dock_type"),
                    "dock_status": dock.get("dock_status"),
                    "supports_refrigerated": dock.get("supports_refrigerated"),
                    "max_vehicle_weight_kg": dock.get("max_vehicle_weight_kg"),
                    "slot_start_ts": slot["slot_start_ts"],
                    "slot_end_ts": slot["slot_end_ts"],
                    "availability_status": availability_status,
                    "appointment_id": occ_appointment["appointment_id"] if occ_appointment else None,
                    "shipment_id": occ_appointment["shipment_id"] if occ_appointment else None,
                    "appointment_status": occ_appointment["appointment_status"] if occ_appointment else None,
                    "occupied_priority": occ_shipment["priority_code"] if occ_shipment else None,
                    "occupied_unload_min": occ_shipment["expected_unload_min"] if occ_shipment else None,
                    "occupied_driver_id": occ_shipment.get("driver_id") if occ_shipment else None,
                    "hold_id": hold["hold_id"] if hold else None,
                    "held_shipment_id": hold["shipment_id"] if hold else None,
                }
            )
        return result

    def operational_state(self, shipment_id: str) -> dict[str, Any]:
        """Public accessor for _operational_state -- used by
        DockSchedulerService's cached compatible-slots composition (see
        that module's compatible_slots_cached/facility_availability_cached),
        which needs the shipment's compatibility requirements (dock type/
        refrigeration/weight) and destination facility separately from the
        facility-wide availability dataset below."""
        return self._operational_state(shipment_id)

    def facility_dock_availability(self, facility_id: str) -> list[dict[str, Any]]:
        """Facility-wide slot availability (which docks, which slots, who
        currently occupies/holds each one) -- independent of any single
        shipment's compatibility requirements, which compatible_slots()
        applies afterward via filter_compatible_slots(). Split out so this
        shipment-agnostic dataset can be cached and reused once per facility
        (see DockSchedulerService.facility_availability_cached) instead of
        being refetched from scratch for every different shipment shown on
        the same facility's board -- measured at ~9 sequential Supabase
        round trips (~3.5s) per call before this split existed.
        """
        docks = self._select("docks", facility_id=facility_id, dock_status="ACTIVE")
        docks_by_id = {d["dock_id"]: d for d in docks}
        if not docks_by_id:
            return []

        # Bounded to a rolling window starting a day before *now* (wall
        # clock), not an unfiltered fetch of every appointment_slots row
        # ever generated for these docks. ensure_future_slots() only ever
        # inserts new rows and never deletes old ones, so the table grows
        # without bound as real days pass -- an unbounded fetch here was
        # returning every slot back to the original seed date, and
        # _slot_rows_with_availability then built an
        # `appointments`/`slot_holds` .in_("slot_id", [...]) filter over ALL
        # of them. As that list grew past hundreds of ids the resulting
        # request URL eventually failed outright -- this is what was
        # crashing the driver chatbot with 500s.
        #
        # This intentionally does NOT anchor on any shipment's own
        # effective_eta_ts (an earlier version of this fix tried that): real
        # shipment ETA fields are themselves often stale demo data (the same
        # staleness that made ensure_future_slots necessary in the first
        # place), so anchoring on them reintroduced the exact "every slot
        # looks unavailable" bug for shipments with an old ETA. Wall-clock
        # "now" is what ensure_future_slots itself backfills relative to, so
        # it's the only anchor guaranteed to line up with what's actually in
        # the table. No upper bound is needed -- ensure_future_slots never
        # generates further ahead than its own horizon, so forward growth is
        # already self-limiting; only the unbounded past accumulates.
        slots = self._select_gte(
            "appointment_slots",
            "slot_start_ts",
            (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
            facility_id=facility_id,
            dock_id=list(docks_by_id.keys()),
        )
        return self._slot_rows_with_availability(slots, docks_by_id)

    @staticmethod
    def filter_compatible_slots(target: dict[str, Any], enriched: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Apply one shipment's compatibility requirements (dock type/
        refrigeration/weight) to a facility-wide availability dataset --
        cheap, in-memory, no Supabase calls. Shared by compatible_slots()
        below and DockSchedulerService.compatible_slots_cached()."""
        required_dock_type = target["required_dock_type"]
        temperature_control_required = target["temperature_control_required"]
        max_weight_ok_load = target["load_weight_kg"]

        compatible = [
            row
            for row in enriched
            if (required_dock_type == "ANY" or row["dock_type"] == required_dock_type)
            and (not temperature_control_required or row["supports_refrigerated"])
            and (row["max_vehicle_weight_kg"] is None or row["max_vehicle_weight_kg"] >= max_weight_ok_load)
        ]
        compatible.sort(key=lambda row: (row["slot_start_ts"], row["dock_code"]))
        return compatible

    def compatible_slots(self, shipment_id: str) -> list[dict[str, Any]]:
        cached = self._compatible_slots_cache.get(shipment_id)
        if cached is not None:
            return cached
        target = self._operational_state(shipment_id)
        enriched = self.facility_dock_availability(target["destination_facility_id"])
        compatible = self.filter_compatible_slots(target, enriched)
        self._compatible_slots_cache[shipment_id] = compatible
        return compatible

    def facility_id_for_shipment(self, shipment_id: str) -> str | None:
        """Cheap (single-row) lookup for callers that only need the
        destination facility, not the full _operational_state()."""
        rows = self._select("shipments", shipment_id=shipment_id)
        if not rows:
            return None
        return rows[0].get("destination_facility_id")

    def ensure_future_slots_for_shipment(self, shipment_id: str) -> int:
        """Cheap wrapper around ensure_future_slots() for board/feasibility
        read paths that already have a shipment_id in hand."""
        facility_id = self.facility_id_for_shipment(shipment_id)
        if not facility_id:
            return 0
        return self.ensure_future_slots(facility_id)

    def ensure_future_slots(self, facility_id: str) -> int:
        """Top up every ACTIVE dock at *facility_id* so at least
        FUTURE_SLOT_HORIZON_DAYS days of OPEN capacity exist ahead of *now*.

        The seed data (data/setuhaul_schema_and_seed.sql) only ever
        populated a single calendar day of appointment_slots per dock
        (2026-08-04), and nothing else in this module regenerates more as
        real time passes it. compatible_slots() has no date filter, so once
        that one seeded day is in the past every slot on every board --
        WMS, TMS's slot-change picker, and the driver's dock board -- looks
        permanently unavailable. That's what made the driver board
        unusable: every slot showed "Starts before the declared ETA".

        Rather than a one-off SQL patch (which goes stale again the next
        day), each dock's own most recent day of OPEN slots is used as a
        template and cloned forward, day by day, until the horizon is
        covered. This only ever inserts new rows -- existing slots
        (including historical BLOCKED ones and anything already booked)
        are never touched. Called from board/feasibility read paths
        (dock_board, driver_chat_eta._feasible_slots), not from every
        repository method, so it doesn't add write overhead to hold/
        confirm/cancel calls that already have a valid slot_id in hand.

        Deliberately does NOT fetch every appointment_slots row for the
        facility to figure out "is this dock topped up" -- that table only
        grows over time (each backfill adds another ~week of rows per
        dock), and compatible_slots() right after this call already has to
        pay for that full fetch once anyway. Doing it twice on every single
        board/feasibility read would make exactly the kind of the-app-feels-
        slower regression this whole feature exists to avoid. Instead, only
        each dock's single latest row is fetched (order + limit(1)) to
        decide whether backfill is needed at all, and only when it is do we
        pull that one template day's rows.
        """
        now = datetime.utcnow()
        last_checked = _FUTURE_SLOTS_LAST_CHECKED.get(facility_id)
        if last_checked is not None and now - last_checked < _FUTURE_SLOTS_RECHECK_INTERVAL:
            return 0
        _FUTURE_SLOTS_LAST_CHECKED[facility_id] = now

        docks = self._select("docks", facility_id=facility_id, dock_status="ACTIVE")
        if not docks:
            return 0

        facility_rows = self._select("facilities", facility_id=facility_id)
        evaluator = FacilityConstraintEvaluator(facility_rows[0], []) if facility_rows else None

        # Comparisons below are done in naive-UTC-ish terms rather than
        # timezone-aware ones: real appointment_slots rows carry a +05:30
        # offset, but some callers' fixtures (and, per driver_chat_eta's own
        # convention -- see its module docstring) use naive strings. Mixing
        # the two in a single comparison raises TypeError, and stripping
        # offsets here is harmless given this only decides "is this dock
        # backfilled roughly far enough ahead", not anything millisecond-
        # precise. The *stored* new rows below still preserve each source
        # row's original offset (or lack of one) via parse_ts(...).replace(...).
        horizon_naive = now + timedelta(days=self.FUTURE_SLOT_HORIZON_DAYS)
        now_str = _now_iso()

        new_rows: list[dict[str, Any]] = []
        for dock in docks:
            dock_id = dock["dock_id"]
            latest_row = self._latest_slot_for_dock(dock_id)
            if latest_row is None:
                continue  # dock has no seeded slots at all -- nothing to clone from

            latest_start = parse_ts(latest_row["slot_start_ts"])
            latest_start_naive = latest_start.replace(tzinfo=None)
            if latest_start_naive >= horizon_naive:
                continue  # already topped up for this dock -- the common case, no further reads

            day_start_iso = latest_start.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(
                timespec="seconds"
            )
            # latest_row is by definition the newest row that exists, so
            # "everything from the start of its calendar day onward" is
            # exactly that one day's rows -- nothing newer exists yet.
            day_rows = self._select_gte("appointment_slots", "slot_start_ts", day_start_iso, dock_id=dock_id)
            template = [
                row
                for row in day_rows
                if row["slot_status"] == "OPEN"
                and parse_ts(row["slot_start_ts"]).replace(tzinfo=None).date() == latest_start_naive.date()
                and (
                    evaluator is None
                    or evaluator.slot_within_operating_hours(
                        parse_ts(row["slot_start_ts"]).replace(tzinfo=None),
                        parse_ts(row["slot_end_ts"]).replace(tzinfo=None),
                    )
                )
            ]
            if not template:
                continue  # nothing to clone forward (e.g. only BLOCKED slots, or slots outside facility hours)

            existing_starts = {row["slot_start_ts"] for row in day_rows}
            cursor_day = latest_start_naive.date() + timedelta(days=1)
            while datetime.combine(cursor_day, datetime.min.time()) <= horizon_naive:
                for row in template:
                    start = parse_ts(row["slot_start_ts"]).replace(
                        year=cursor_day.year, month=cursor_day.month, day=cursor_day.day
                    )
                    end = parse_ts(row["slot_end_ts"]).replace(
                        year=cursor_day.year, month=cursor_day.month, day=cursor_day.day
                    )
                    start_iso = start.isoformat(timespec="seconds")
                    if start_iso in existing_starts:
                        continue
                    new_rows.append(
                        {
                            "slot_id": f"{dock_id}-{start.strftime('%Y%m%d%H%M')}",
                            "facility_id": facility_id,
                            "dock_id": dock_id,
                            "slot_start_ts": start_iso,
                            "slot_end_ts": end.isoformat(timespec="seconds"),
                            "slot_status": "OPEN",
                            "block_reason": None,
                            "created_at": now_str,
                        }
                    )
                    existing_starts.add(start_iso)
                cursor_day += timedelta(days=1)

        if not new_rows:
            return 0

        try:
            self.backend.table("appointment_slots").insert(new_rows).execute()
        except APIError as exc:
            # Best-effort: a failed backfill shouldn't break the board/
            # feasibility read that triggered it -- the caller just sees
            # whatever slots already existed.
            return 0
        return len(new_rows)

    def _latest_slot_for_dock(self, dock_id: str) -> dict[str, Any] | None:
        try:
            response = (
                self.backend.table("appointment_slots")
                .select("*")
                .eq("dock_id", dock_id)
                .order("slot_start_ts", desc=True)
                .limit(1)
                .execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        rows = self._data(response)
        return rows[0] if rows else None

    def _select_gte(self, table: str, column: str, value: str, **filters: Any) -> list[dict[str, Any]]:
        try:
            query = self.backend.table(table).select("*").gte(column, value)
            for key, filter_value in filters.items():
                if isinstance(filter_value, (list, tuple, set)):
                    query = query.in_(key, list(filter_value))
                else:
                    query = query.eq(key, filter_value)
            return self._data(query.execute())
        except APIError as exc:
            self._raise_persistence(exc)

    def _select_range(
        self, table: str, column: str, *, gte: str | None = None, lte: str | None = None, **filters: Any
    ) -> list[dict[str, Any]]:
        try:
            query = self.backend.table(table).select("*")
            if gte is not None:
                query = query.gte(column, gte)
            if lte is not None:
                query = query.lte(column, lte)
            for key, filter_value in filters.items():
                if isinstance(filter_value, (list, tuple, set)):
                    query = query.in_(key, list(filter_value))
                else:
                    query = query.eq(key, filter_value)
            return self._data(query.execute())
        except APIError as exc:
            self._raise_persistence(exc)

    def driver_names(self, driver_ids: list[str]) -> dict[str, str]:
        """Resolve driver_id -> driver_name for a batch of drivers, so the
        visual dock board can show WMS staff whose shipment holds/occupies
        each slot. dock_scheduler otherwise never needs driver identity --
        this is a narrow, read-only cross-context lookup (same pattern
        driver_chat_eta already uses to read across bounded contexts)."""
        ids = [value for value in {*driver_ids} if value]
        if not ids:
            return {}
        rows = self._select("drivers", driver_id=ids)
        return {row["driver_id"]: row.get("driver_name") for row in rows if row.get("driver_name")}

    def current_appointment(self, shipment_id: str) -> dict[str, Any] | None:
        rows = self._select(
            "appointments", shipment_id=shipment_id, is_current=1, appointment_status=list(ACTIVE_APPOINTMENT_STATUSES)
        )
        if not rows:
            return None
        appointment = rows[0]
        slot_rows = self._select("appointment_slots", slot_id=appointment["slot_id"])
        if not slot_rows:
            return {**appointment, "slot_start_ts": None, "slot_end_ts": None, "dock_code": None}
        slot = slot_rows[0]
        dock_rows = self._select("docks", dock_id=slot["dock_id"])
        dock_code = dock_rows[0]["dock_code"] if dock_rows else None
        return {
            **appointment,
            "slot_start_ts": slot["slot_start_ts"],
            "slot_end_ts": slot["slot_end_ts"],
            "dock_code": dock_code,
        }

    def slot_availability(self, slot_id: str) -> dict[str, Any] | None:
        self._expire_stale_holds()
        slots = self._select("appointment_slots", slot_id=slot_id)
        if not slots:
            return None
        docks = self._select("docks", dock_id=slots[0]["dock_id"])
        docks_by_id = {d["dock_id"]: d for d in docks}
        enriched = self._slot_rows_with_availability(slots, docks_by_id)
        return enriched[0] if enriched else None

    def active_hold_for_slot(self, slot_id: str) -> dict[str, Any] | None:
        self._expire_stale_holds()
        rows = self._select("slot_holds", slot_id=slot_id, hold_status="HELD")
        rows.sort(key=lambda row: row["held_at"], reverse=True)
        return rows[0] if rows else None

    def active_hold_for_shipment(self, shipment_id: str, slot_id: str) -> dict[str, Any] | None:
        self._expire_stale_holds()
        rows = self._select("slot_holds", shipment_id=shipment_id, slot_id=slot_id, hold_status="HELD")
        rows.sort(key=lambda row: row["held_at"], reverse=True)
        return rows[0] if rows else None

    def current_appointment_status(self, shipment_id: str, slot_id: str) -> str | None:
        """Latest appointment_status for a (shipment, slot, is_current) pair.

        Used only by DockSchedulerService.lifecycle_stage_for_slot, a helper
        with no current callers elsewhere in the codebase -- kept working
        rather than removed.
        """
        rows = self._select("appointments", shipment_id=shipment_id, slot_id=slot_id, is_current=1)
        if not rows:
            return None
        rows.sort(key=lambda row: row["booked_at"], reverse=True)
        return rows[0]["appointment_status"]

    # -- mutations ------------------------------------------------------------

    def create_hold(self, shipment_id: str, slot_id: str, ttl_minutes: int) -> HoldResult:
        self._expire_stale_holds()
        slot = self.slot_availability(slot_id)
        if slot is None:
            raise SlotUnavailableError(f"Unknown slot: {slot_id}")
        if slot["availability_status"] not in {"AVAILABLE"}:
            raise SlotUnavailableError("Selected slot is no longer available for hold")

        now = datetime.now().astimezone()
        expires_at = now + timedelta(minutes=ttl_minutes)
        hold_id = f"HLD-{uuid4().hex[:8].upper()}"
        try:
            self.backend.table("slot_holds").insert(
                {
                    "hold_id": hold_id,
                    "slot_id": slot_id,
                    "shipment_id": shipment_id,
                    "hold_status": "HELD",
                    "held_at": now.isoformat(timespec="seconds"),
                    "expires_at": expires_at.isoformat(timespec="seconds"),
                }
            ).execute()
        except APIError as exc:
            self._raise_persistence(exc)

        # A hold changes availability_status for every shipment that could
        # have used this slot, not just this one -- clear the whole cache,
        # not just this shipment_id's entry.
        self._compatible_slots_cache.clear()
        return HoldResult(
            hold_id=hold_id,
            slot_id=slot_id,
            shipment_id=shipment_id,
            expires_at=expires_at,
            lifecycle_stage=SlotLifecycleStage.HELD,
        )

    def release_hold(self, hold_id: str) -> None:
        now = _now_iso()
        try:
            self.backend.table("slot_holds").update({"hold_status": "RELEASED", "released_at": now}).eq(
                "hold_id", hold_id
            ).eq("hold_status", "HELD").execute()
        except APIError as exc:
            self._raise_persistence(exc)
        self._compatible_slots_cache.clear()

    def create_pending_appointment(self, shipment_id: str, slot_id: str) -> str:
        now = _now_iso()
        slot = self.slot_availability(slot_id)
        if slot is None:
            raise SlotUnavailableError(f"Unknown slot: {slot_id}")

        hold = self.active_hold_for_shipment(shipment_id, slot_id)
        if slot["availability_status"] == "HELD" and (hold is None or hold["hold_id"] != slot["hold_id"]):
            raise SlotUnavailableError("Slot is held by another shipment")
        if slot["availability_status"] not in {"AVAILABLE", "HELD"}:
            raise SlotUnavailableError("Selected slot is no longer available")

        previous = self.current_appointment(shipment_id)
        try:
            if previous and previous["slot_id"] != slot_id:
                self.backend.table("appointments").update(
                    {"is_current": 0, "appointment_status": "CANCELLED", "cancelled_at": now, "updated_at": now}
                ).eq("appointment_id", previous["appointment_id"]).execute()

            existing = self._select(
                "appointments",
                shipment_id=shipment_id,
                slot_id=slot_id,
                is_current=1,
                appointment_status="PENDING_CONFIRMATION",
            )
            if existing:
                return existing[0]["appointment_id"]

            appointment_id = f"APT-PND-{uuid4().hex[:8].upper()}"
            self.backend.table("appointments").insert(
                {
                    "appointment_id": appointment_id,
                    "shipment_id": shipment_id,
                    "slot_id": slot_id,
                    "appointment_status": "PENDING_CONFIRMATION",
                    "booking_source": "SCHEDULING_TOOL",
                    "is_current": 1,
                    "booked_at": now,
                    "replaced_appointment_id": previous["appointment_id"] if previous else None,
                    "updated_at": now,
                }
            ).execute()
            return appointment_id
        except APIError as exc:
            self._raise_persistence(exc)
        finally:
            self._compatible_slots_cache.clear()

    def book_after_acceptance(self, shipment_id: str, slot_id: str, accepted: bool) -> str:
        if not accepted:
            raise InvalidBookingError("Explicit driver acceptance is required before booking")

        now = _now_iso()
        slot = self.slot_availability(slot_id)
        if slot is None:
            raise SlotUnavailableError(f"Unknown slot: {slot_id}")

        hold = self.active_hold_for_shipment(shipment_id, slot_id)
        pending_rows = self._select(
            "appointments",
            shipment_id=shipment_id,
            slot_id=slot_id,
            is_current=1,
            appointment_status="PENDING_CONFIRMATION",
        )
        pending = pending_rows[0] if pending_rows else None

        if slot["availability_status"] == "HELD":
            if hold is None:
                raise SlotUnavailableError("Selected slot is held by another shipment")
        elif slot["availability_status"] != "AVAILABLE":
            if pending is None:
                raise SlotUnavailableError("Selected slot is no longer available")

        try:
            previous = self.current_appointment(shipment_id)
            if previous and previous["slot_id"] != slot_id:
                self.backend.table("appointments").update(
                    {"is_current": 0, "appointment_status": "CANCELLED", "cancelled_at": now, "updated_at": now}
                ).eq("appointment_id", previous["appointment_id"]).execute()

            if pending:
                self.backend.table("appointments").update(
                    {"appointment_status": "CONFIRMED", "confirmed_at": now, "updated_at": now}
                ).eq("appointment_id", pending["appointment_id"]).execute()
                confirmed_id = pending["appointment_id"]
            else:
                confirmed_id = f"APT-{shipment_id}-{slot_id.split('-')[-1]}"
                self.backend.table("appointments").insert(
                    {
                        "appointment_id": confirmed_id,
                        "shipment_id": shipment_id,
                        "slot_id": slot_id,
                        "appointment_status": "CONFIRMED",
                        "booking_source": "DRIVER_CHAT",
                        "is_current": 1,
                        "booked_at": now,
                        "confirmed_at": now,
                        "replaced_appointment_id": previous["appointment_id"] if previous else None,
                        "updated_at": now,
                    }
                ).execute()

            if hold:
                self.backend.table("slot_holds").update({"hold_status": "CONVERTED", "released_at": now}).eq(
                    "hold_id", hold["hold_id"]
                ).execute()
        except APIError as exc:
            self._raise_persistence(exc)

        self._compatible_slots_cache.clear()
        return confirmed_id

    def cancel_pending(self, shipment_id: str, slot_id: str) -> None:
        now = _now_iso()
        hold = self.active_hold_for_shipment(shipment_id, slot_id)
        try:
            if hold:
                self.backend.table("slot_holds").update({"hold_status": "RELEASED", "released_at": now}).eq(
                    "hold_id", hold["hold_id"]
                ).execute()
            self.backend.table("appointments").update(
                {"is_current": 0, "appointment_status": "CANCELLED", "cancelled_at": now, "updated_at": now}
            ).eq("shipment_id", shipment_id).eq("slot_id", slot_id).eq("is_current", 1).eq(
                "appointment_status", "PENDING_CONFIRMATION"
            ).execute()
        except APIError as exc:
            self._raise_persistence(exc)
        self._compatible_slots_cache.clear()

    def _expire_stale_holds(self) -> None:
        now = _now_iso()
        try:
            self.backend.table("slot_holds").update({"hold_status": "EXPIRED", "released_at": now}).eq(
                "hold_status", "HELD"
            ).lte("expires_at", now).execute()
        except APIError as exc:
            self._raise_persistence(exc)

    # -- dock-slot change requests (TMS/driver requested, WMS approved) -----

    def _enrich_change_request(self, row: dict[str, Any]) -> dict[str, Any]:
        slot_rows = self._select("appointment_slots", slot_id=row["requested_slot_id"])
        if not slot_rows:
            return {**row, "dock_code": None, "slot_start_ts": None, "slot_end_ts": None}
        slot = slot_rows[0]
        dock_rows = self._select("docks", dock_id=slot["dock_id"])
        dock_code = dock_rows[0]["dock_code"] if dock_rows else None
        return {
            **row,
            "dock_code": dock_code,
            "slot_start_ts": slot["slot_start_ts"],
            "slot_end_ts": slot["slot_end_ts"],
        }

    def create_change_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            self.backend.table("dock_slot_change_requests").insert(payload).execute()
        except APIError as exc:
            self._raise_persistence(exc)
        return self._enrich_change_request(payload)

    def list_change_requests(self, status: str | None = None) -> list[dict[str, Any]]:
        """List change requests, enriched with dock_code/slot times.

        Batches the enrichment lookups (one query for every requested
        slot_id, one for every dock_id) instead of the 2 extra queries per
        row _enrich_change_request() does on its own -- this endpoint is
        polled every 15s by every WMS user's pending-requests header (see
        PortalWorkspace's WmsChangeRequestsHeader), so an N+1 here means
        2*N avoidable round trips on every single poll tick.
        """
        rows = self._select("dock_slot_change_requests", **({"request_status": status} if status else {}))
        rows.sort(key=lambda row: row.get("created_at") or "", reverse=True)
        if not rows:
            return []

        slot_ids = {row["requested_slot_id"] for row in rows if row.get("requested_slot_id")}
        slots_by_id = (
            {row["slot_id"]: row for row in self._select("appointment_slots", slot_id=list(slot_ids))}
            if slot_ids
            else {}
        )
        dock_ids = {slot["dock_id"] for slot in slots_by_id.values() if slot.get("dock_id")}
        docks_by_id = {row["dock_id"]: row for row in self._select("docks", dock_id=list(dock_ids))} if dock_ids else {}

        enriched: list[dict[str, Any]] = []
        for row in rows:
            slot = slots_by_id.get(row.get("requested_slot_id"))
            if slot is None:
                enriched.append({**row, "dock_code": None, "slot_start_ts": None, "slot_end_ts": None})
                continue
            dock = docks_by_id.get(slot["dock_id"])
            enriched.append(
                {
                    **row,
                    "dock_code": dock["dock_code"] if dock else None,
                    "slot_start_ts": slot["slot_start_ts"],
                    "slot_end_ts": slot["slot_end_ts"],
                }
            )
        return enriched

    def get_change_request(self, change_request_id: str) -> dict[str, Any] | None:
        rows = self._select("dock_slot_change_requests", change_request_id=change_request_id)
        return self._enrich_change_request(rows[0]) if rows else None

    def update_change_request(self, change_request_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        try:
            response = (
                self.backend.table("dock_slot_change_requests")
                .update(payload)
                .eq("change_request_id", change_request_id)
                .execute()
            )
        except APIError as exc:
            self._raise_persistence(exc)
        rows = self._data(response)
        return self._enrich_change_request(rows[0]) if rows else None
