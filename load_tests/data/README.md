# Load-Test Data Contract

This directory intentionally contains no credentials or live shipment data.

- Prepare exactly 100 `LT-...` shipments at an isolated test facility.
- Assign each a driver, vehicle, compatible confirmed appointment, and the
  required check-in preconditions before running `CheckinUser`.
- Export the shipment IDs into `CHECKIN_SHIPMENT_IDS`; each Locust user claims
  exactly one ID and stops after completion.
- Use separate `LT-...` shipment and slot IDs for WMS holds and confirmations.
- Recreate the data after every completion run. Never point mutation scenarios
  at shared development or production shipments.
