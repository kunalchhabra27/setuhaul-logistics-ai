"""SetuHaul observability harness entrypoint.

Run from the repository root, for example:
    LOCUST_HOST=http://localhost:8000 locust -f load_tests/locustfile.py
"""

import logging

import gevent
from locust import events

from load_tests.preflight import LoadTestPreflightError, preflight_environment
from scenarios.agentcore import AgentCoreDriverUser
from scenarios.checkin import CheckinUser
from scenarios.driver import DriverUser
from scenarios.read_only import ReadOnlyUser
from scenarios.tms import TmsUser
from scenarios.wms import WmsUser

logger = logging.getLogger(__name__)

__all__ = [
    "AgentCoreDriverUser",
    "CheckinUser",
    "DriverUser",
    "ReadOnlyUser",
    "TmsUser",
    "WmsUser",
]


@events.test_start.add_listener
def validate_selected_scenarios(environment, **_kwargs) -> None:
    try:
        preflight_environment(environment)
    except LoadTestPreflightError as exc:
        logger.error("Load-test preflight failed: %s", exc)
        options = getattr(environment, "parsed_options", None)
        stop = environment.runner.quit if getattr(options, "headless", False) else environment.runner.stop
        gevent.spawn_later(0, stop)
        # GreenletExit ends this spawn cycle without Locust recording a user
        # exception or printing a configuration traceback.
        raise gevent.GreenletExit
