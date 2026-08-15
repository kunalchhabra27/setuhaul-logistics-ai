from __future__ import annotations

import pytest

from setuhaul.backend.checkin_portal.exceptions import InvalidCheckInTransition
from setuhaul.backend.checkin_portal.state_machine import validate_transition


@pytest.mark.parametrize(
    ("current_status", "target_status"),
    [
        ("GATE_IN", "WAITING"),
        ("GATE_IN", "DOCKED"),
        ("WAITING", "DOCKED"),
        ("DOCKED", "COMPLETED"),
    ],
)
def test_allowed_transitions_pass(current_status: str, target_status: str) -> None:
    validate_transition(current_status, target_status)


@pytest.mark.parametrize(
    ("current_status", "target_status"),
    [
        ("COMPLETED", "WAITING"),
        ("COMPLETED", "DOCKED"),
        ("DOCKED", "GATE_IN"),
        ("DOCKED", "YARD_QUEUE"),
        ("WAITING", "COMPLETED"),
    ],
)
def test_invalid_transitions_fail(current_status: str, target_status: str) -> None:
    with pytest.raises(InvalidCheckInTransition, match=r"Invalid transition:"):
        validate_transition(current_status, target_status)
