# Load-Test Data Contract

This directory intentionally contains no credentials or live shipment data.

- Prepare only the configurable number of `LT-...` shipments required for the
  selected smoke, pilot, or approved load run.
- Assign each a driver, vehicle, compatible confirmed appointment, and the
  required check-in preconditions before running `CheckinUser`.
- Prefer `load_tests/seed_checkin.py`, which uses authenticated application and
  scheduler APIs and writes a credential-free `CHECKIN_MANIFEST`. Each Locust
  user claims exactly one manifest shipment and stops after completion.
- Use separate `LT-...` shipment and slot IDs for WMS holds and confirmations.
- Recreate the data after every completion run. Never point mutation scenarios
  at shared development or production shipments.
