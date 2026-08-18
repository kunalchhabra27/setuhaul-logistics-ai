"""Keep Locust unit tests isolated from process-wide gevent patching."""

import os

os.environ.setdefault("LOCUST_SKIP_MONKEY_PATCH", "1")
import locust  # noqa: F401
