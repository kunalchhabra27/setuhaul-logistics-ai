from __future__ import annotations

from datetime import datetime, timedelta

from setuhaul.backend.dock_scheduler.models import DriverConstraints

PRIORITY_WEIGHT = {"LOW": 1, "NORMAL": 2, "HIGH": 3, "CRITICAL": 4}


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def parse_facility_time(day: datetime, clock: str) -> datetime:
    hour, minute = (int(part) for part in clock.split(":"))
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)


class FacilityConstraintEvaluator:
    """Apply facility hours and facility_rules to slot feasibility checks."""

    def __init__(self, facility: dict, rules: list[dict]):
        self.facility = facility
        self.rules = {row["rule_type"]: row for row in rules if row["active_flag"]}

    def earliest_arrival(
        self,
        shipment: dict,
        constraints: DriverConstraints,
    ) -> datetime:
        candidates: list[datetime] = []
        if shipment.get("effective_eta_ts"):
            candidates.append(parse_ts(shipment["effective_eta_ts"]))
        if constraints.earliest_start:
            candidates.append(constraints.earliest_start)
        if shipment.get("gate_in_ts"):
            candidates.append(parse_ts(shipment["gate_in_ts"]))
        if not candidates:
            raise ValueError("No arrival time available for scheduling")
        return max(candidates)

    def slot_within_operating_hours(self, start: datetime, end: datetime) -> bool:
        open_at = parse_facility_time(start, self.facility["open_time"])
        close_at = parse_facility_time(start, self.facility["close_time"])
        return start >= open_at and end <= close_at

    def slot_respects_last_start_rule(self, start: datetime, unload_minutes: int) -> bool:
        rule = self.rules.get("LAST_NEW_START_TIME")
        if not rule:
            return True
        last_start = parse_facility_time(start, rule["rule_value"])
        return start <= last_start

    def dock_allowed_for_shipment(self, slot: dict, shipment: dict) -> bool:
        if shipment["temperature_control_required"]:
            reefer_rule = self.rules.get("REEFER_DOCK_REQUIRED")
            if reefer_rule and reefer_rule["rule_value"].upper() == "TRUE":
                if slot["dock_type"] != "REEFER":
                    return False

        heavy_rule = self.rules.get("HEAVY_DOCK_REQUIRED_KG")
        if heavy_rule and shipment["load_weight_kg"] > int(heavy_rule["rule_value"]):
            if slot["dock_type"] != "HEAVY":
                return False

        return True

    def is_feasible(
        self,
        slot: dict,
        shipment: dict,
        constraints: DriverConstraints,
    ) -> tuple[bool, str | None]:
        start = parse_ts(slot["slot_start_ts"])
        end = parse_ts(slot["slot_end_ts"])
        unload_minutes = int(shipment["expected_unload_min"])
        duration_minutes = int((end - start).total_seconds() // 60)
        earliest = self.earliest_arrival(shipment, constraints)
        must_finish_by = constraints.must_finish_by

        if start < earliest:
            return False, "Driver cannot reach the facility before this slot starts."
        if duration_minutes < unload_minutes:
            return False, "Slot is too short for the expected unload duration."
        if must_finish_by and end > must_finish_by:
            return False, "Slot ends after the driver's deadline."
        if not self.slot_within_operating_hours(start, end):
            return False, "Slot falls outside facility operating hours."
        if not self.slot_respects_last_start_rule(start, unload_minutes):
            return False, "Slot starts after the facility last-allowed start time."
        if not self.dock_allowed_for_shipment(slot, shipment):
            return False, "Dock does not satisfy facility compatibility rules."

        return True, None

    def is_docked_shipment_locked(self, shipment: dict) -> bool:
        queue_state = shipment.get("queue_state")
        return queue_state == "IN_DOCK" or shipment.get("current_status") == "IN_DOCK"
