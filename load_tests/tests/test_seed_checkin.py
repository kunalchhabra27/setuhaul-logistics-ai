from __future__ import annotations

import pytest

from load_tests.seed_checkin import _reference_pairs


class _Response:
    is_success = True

    def __init__(self, payload: list[dict]):
        self._payload = payload

    def json(self) -> list[dict]:
        return self._payload


class _Client:
    def request(self, _method: str, path: str, **_kwargs) -> _Response:
        if "/drivers" in path:
            return _Response(
                [
                    {"driver_id": "DRV-1", "carrier_id": "CAR-1", "driver_status": "ACTIVE"},
                    {"driver_id": "DRV-2", "carrier_id": "CAR-1", "driver_status": "ACTIVE"},
                ]
            )
        return _Response(
            [
                {"vehicle_id": "VEH-1", "carrier_id": "CAR-1", "active_flag": True},
                {"vehicle_id": "VEH-2", "carrier_id": "CAR-1", "active_flag": True},
            ]
        )


def test_reference_pairs_fail_closed_when_unique_capacity_is_insufficient() -> None:
    with pytest.raises(RuntimeError, match="CHECKIN_ALLOW_REFERENCE_REUSE=true"):
        _reference_pairs(_Client(), 3)


def test_reference_pairs_cycle_only_when_explicitly_allowed() -> None:
    pairs = _reference_pairs(_Client(), 5, allow_reuse=True)

    assert [(driver["driver_id"], vehicle["vehicle_id"]) for driver, vehicle in pairs] == [
        ("DRV-1", "VEH-1"),
        ("DRV-2", "VEH-2"),
        ("DRV-1", "VEH-1"),
        ("DRV-2", "VEH-2"),
        ("DRV-1", "VEH-1"),
    ]
