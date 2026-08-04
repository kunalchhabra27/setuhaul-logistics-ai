from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class SuggestionType(str, Enum):
    KEEP_ORIGINAL = "KEEP_ORIGINAL"
    ASSIGN_AVAILABLE = "ASSIGN_AVAILABLE"
    PRIORITY_SWAP = "PRIORITY_SWAP"


@dataclass(frozen=True)
class DriverConstraints:
    earliest_start: datetime | None = None
    must_finish_by: datetime | None = None


@dataclass(frozen=True)
class SlotSuggestion:
    rank: int
    suggestion_type: SuggestionType
    slot_id: str
    dock_code: str
    start: datetime
    end: datetime
    reason: str
    displaced_shipment_id: str | None = None
    displaced_to_slot_id: str | None = None
