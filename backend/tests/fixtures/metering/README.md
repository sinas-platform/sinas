# sinas.metering/v2 contract fixtures

Shared fixtures for Platform contract tests (SIN-640). Contract decisions
settled 2026-08-31 (supersede the Metering API Confluence page where they
differ):

- POST-only: no GET /context endpoint; period metadata arrives exclusively
  in POST /reports responses.
- Bootstrap sentinel is the literal string `"init"` (not `"0000"`). The
  Platform APPLIES an init report to the then-current billing period and
  returns that period. Core adopts it WITHOUT resetting counters (the
  applied counts are the period baseline; a reset would trip
  counter_regression on the next report). A missing or unknown period id
  that is not "init" remains a 409.
- Rollover is detected on BOTH paths and handled identically (adopt the
  returned period + reset counters and snapshot_seq): a 200 ack whose
  period.id differs from the reported canonical_period_id, and a 409
  period_mismatch / period_context_required.
- All counters and sequence values are decimal strings; by_kind carries an
  explicit "other" so total == sum(by_kind) always holds.
