from __future__ import annotations

from .models import SlotSuggestion, SuggestionType


def rank_suggestions(
    direct: list[SlotSuggestion],
    swaps: list[SlotSuggestion],
) -> list[SlotSuggestion]:
    """Rank slot suggestions for the driver-facing scheduler response."""
    direct_sorted = sorted(
        direct,
        key=lambda item: (
            0 if item.suggestion_type is SuggestionType.KEEP_ORIGINAL else 1,
            item.start,
            item.dock_code,
        ),
    )
    swaps_sorted = sorted(swaps, key=lambda item: (item.start, item.dock_code))
    ordered = direct_sorted + swaps_sorted
    return [
        SlotSuggestion(**{**item.__dict__, "rank": index})
        for index, item in enumerate(ordered, start=1)
    ]
