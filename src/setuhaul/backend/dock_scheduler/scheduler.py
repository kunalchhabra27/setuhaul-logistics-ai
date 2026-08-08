from __future__ import annotations

from setuhaul.backend.dock_scheduler.constraints import (
    FacilityConstraintEvaluator,
    PRIORITY_WEIGHT,
    parse_ts,
)
from setuhaul.backend.dock_scheduler.models import (
    DriverConstraints,
    SlotLifecycleStage,
    SlotSuggestion,
    SuggestionType,
)
from setuhaul.backend.dock_scheduler.ranking import rank_suggestions
from setuhaul.backend.dock_scheduler.repository import DockSchedulerRepository


class DeterministicReschedulingEngine:
    """Rule-based scheduling engine. It never reads or interprets free text."""

    def __init__(self, repository: DockSchedulerRepository):
        self.repository = repository

    def suggest(
        self,
        shipment_id: str,
        constraints: DriverConstraints | None = None,
        limit: int = 3,
    ) -> list[SlotSuggestion]:
        constraints = constraints or DriverConstraints()
        shipment = self.repository.shipment(shipment_id)
        if shipment["current_status"] in {"CANCELLED", "COMPLETED"}:
            return []

        facility = self.repository.facility(shipment["destination_facility_id"])
        rules = self.repository.facility_rules(shipment["destination_facility_id"])
        evaluator = FacilityConstraintEvaluator(dict(facility), [dict(row) for row in rules])

        if evaluator.is_docked_shipment_locked(dict(shipment)):
            return []

        unload_minutes = int(shipment["expected_unload_min"])
        target_priority = PRIORITY_WEIGHT[shipment["priority_code"]]
        original = self.repository.current_appointment(shipment_id)

        direct: list[SlotSuggestion] = []
        swaps: list[SlotSuggestion] = []
        compatible = self.repository.compatible_slots(shipment_id)

        for slot in compatible:
            feasible, _reason = evaluator.is_feasible(dict(slot), dict(shipment), constraints)
            if not feasible:
                continue

            start = parse_ts(slot["slot_start_ts"])
            end = parse_ts(slot["slot_end_ts"])

            is_original = original is not None and original["slot_id"] == slot["slot_id"]
            if is_original and slot["availability_status"] in {"OCCUPIED", "HELD"}:
                direct.append(
                    SlotSuggestion(
                        rank=0,
                        suggestion_type=SuggestionType.KEEP_ORIGINAL,
                        slot_id=slot["slot_id"],
                        dock_code=slot["dock_code"],
                        start=start,
                        end=end,
                        reason="Original appointment remains feasible after the revised ETA.",
                        lifecycle_stage=SlotLifecycleStage.PROPOSED,
                    )
                )
                continue

            if slot["availability_status"] in {"AVAILABLE", "HELD"}:
                if slot["availability_status"] == "HELD":
                    held_by = slot["held_shipment_id"]
                    if held_by and held_by != shipment_id:
                        continue
                direct.append(
                    SlotSuggestion(
                        rank=0,
                        suggestion_type=SuggestionType.ASSIGN_AVAILABLE,
                        slot_id=slot["slot_id"],
                        dock_code=slot["dock_code"],
                        start=start,
                        end=end,
                        reason="Earliest compatible open slot within the driver's time window.",
                        lifecycle_stage=SlotLifecycleStage.PROPOSED,
                    )
                )
                continue

            occupied_priority = slot["occupied_priority"]
            if slot["availability_status"] == "OCCUPIED" and occupied_priority:
                if target_priority > PRIORITY_WEIGHT[occupied_priority]:
                    replacement = self._find_replacement_for_occupant(slot, compatible, evaluator, shipment, constraints)
                    if replacement:
                        swaps.append(
                            SlotSuggestion(
                                rank=0,
                                suggestion_type=SuggestionType.PRIORITY_SWAP,
                                slot_id=slot["slot_id"],
                                dock_code=slot["dock_code"],
                                start=start,
                                end=end,
                                displaced_shipment_id=slot["shipment_id"],
                                displaced_to_slot_id=replacement["slot_id"],
                                reason=(
                                    "Higher-priority shipment may use this slot because the lower-priority "
                                    "shipment has a later compatible open slot. Human approval is still required."
                                ),
                                lifecycle_stage=SlotLifecycleStage.PROPOSED,
                            )
                        )

        return rank_suggestions(direct, swaps)[:limit]

    def _find_replacement_for_occupant(
        self,
        occupied_slot,
        compatible_slots,
        _evaluator: FacilityConstraintEvaluator,
        _shipment,
        _constraints: DriverConstraints,
    ):
        occupied_start = parse_ts(occupied_slot["slot_start_ts"])
        occupied_unload = int(occupied_slot["occupied_unload_min"] or 0)
        for candidate in compatible_slots:
            if candidate["availability_status"] not in {"AVAILABLE", "HELD"}:
                continue
            start = parse_ts(candidate["slot_start_ts"])
            end = parse_ts(candidate["slot_end_ts"])
            duration = int((end - start).total_seconds() // 60)
            if start > occupied_start and duration >= occupied_unload:
                return candidate
        return None
