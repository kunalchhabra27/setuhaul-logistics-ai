from __future__ import annotations

from datetime import datetime

from setuhaul.db.repository import OperationsRepository, PRIORITY_WEIGHT, parse_ts
from setuhaul.models import DriverConstraints, SlotSuggestion, SuggestionType


class DeterministicReschedulingEngine:
    """Rule-based scheduling engine. It never reads or interprets free text."""

    def __init__(self, repository: OperationsRepository):
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

        eta = parse_ts(shipment["effective_eta_ts"])
        earliest = max(filter(None, [eta, constraints.earliest_start]), default=eta)
        must_finish_by = constraints.must_finish_by
        unload_minutes = int(shipment["expected_unload_min"])
        target_priority = PRIORITY_WEIGHT[shipment["priority_code"]]
        original = self.repository.current_appointment(shipment_id)

        direct: list[SlotSuggestion] = []
        swaps: list[SlotSuggestion] = []
        compatible = self.repository.compatible_slots(shipment_id)

        for slot in compatible:
            start = parse_ts(slot["slot_start_ts"])
            end = parse_ts(slot["slot_end_ts"])
            duration_minutes = int((end - start).total_seconds() // 60)
            if start < earliest or duration_minutes < unload_minutes:
                continue
            if must_finish_by and end > must_finish_by:
                continue

            is_original = original is not None and original["slot_id"] == slot["slot_id"]
            if is_original and slot["availability_status"] == "OCCUPIED":
                direct.append(
                    SlotSuggestion(
                        rank=0,
                        suggestion_type=SuggestionType.KEEP_ORIGINAL,
                        slot_id=slot["slot_id"],
                        dock_code=slot["dock_code"],
                        start=start,
                        end=end,
                        reason="Original appointment remains feasible after the revised ETA.",
                    )
                )
                continue

            if slot["availability_status"] == "AVAILABLE":
                direct.append(
                    SlotSuggestion(
                        rank=0,
                        suggestion_type=SuggestionType.ASSIGN_AVAILABLE,
                        slot_id=slot["slot_id"],
                        dock_code=slot["dock_code"],
                        start=start,
                        end=end,
                        reason="Earliest compatible open slot within the driver's time window.",
                    )
                )
                continue

            occupied_priority = slot["occupied_priority"]
            if slot["availability_status"] == "OCCUPIED" and occupied_priority:
                if target_priority > PRIORITY_WEIGHT[occupied_priority]:
                    replacement = self._find_replacement_for_occupant(slot, compatible)
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
                            )
                        )

        ordered = sorted(
            direct,
            key=lambda item: (
                0 if item.suggestion_type is SuggestionType.KEEP_ORIGINAL else 1,
                item.start,
                item.dock_code,
            ),
        ) + sorted(swaps, key=lambda item: (item.start, item.dock_code))

        return [
            SlotSuggestion(**{**item.__dict__, "rank": index})
            for index, item in enumerate(ordered[:limit], start=1)
        ]

    @staticmethod
    def _find_replacement_for_occupant(occupied_slot, compatible_slots):
        occupied_start = parse_ts(occupied_slot["slot_start_ts"])
        occupied_unload = int(occupied_slot["occupied_unload_min"] or 0)
        for candidate in compatible_slots:
            if candidate["availability_status"] != "AVAILABLE":
                continue
            start = parse_ts(candidate["slot_start_ts"])
            end = parse_ts(candidate["slot_end_ts"])
            duration = int((end - start).total_seconds() // 60)
            if start > occupied_start and duration >= occupied_unload:
                return candidate
        return None
