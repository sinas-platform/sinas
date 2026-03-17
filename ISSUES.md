Tier 1 — Actively Exploitable Security
                                                                                                                                                                                          
  ┌─────┬────────────────────────────────────────────────────────┬─────────────────────────────────────────────────────┐                                                                  
  │  #  │                         Issue                          │                   Why it's first                    │
  ├─────┼────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────┤
  │ 1   │ SQL injection in request_logs.py                       │ Exploitable today by any authenticated user         │
  ├─────┼────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────┤
  │ 2   │ SSRF in OpenAPI import (functions.py)                  │ Can probe internal network, hit cloud metadata      │
  ├─────┼────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────┤
  │ 3   │ CORS — environment-aware origins (main.py)             │ * + credentials = any site can make authed requests │
  ├─────┼────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────┤
  │ 4   │ No rate limiting on auth/OTP                           │ OTP brute-force is trivial, no lockout              │
  ├─────┼────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────┤
  │ 5   │ Default secret key + no startup validation (config.py) │ All JWTs forgeable if missed in prod                │
  ├─────┼────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────┤
  │ 6   │ Encryption key auto-generates if unset (encryption.py) │ Restart = all encrypted DB passwords lost           │
  └─────┴────────────────────────────────────────────────────────┴─────────────────────────────────────────────────────┘

  Tier 2 — Data Integrity & Safety

  ┌─────┬───────────────────────────────────────────────────┬─────────────────────────────────────────────┐
  │  #  │                       Issue                       │                     Why                     │
  ├─────┼───────────────────────────────────────────────────┼─────────────────────────────────────────────┤
  │ 7   │ Unbounded recursive tool loop                     │ Runaway LLM = stack overflow / OOM          │
  ├─────┼───────────────────────────────────────────────────┼─────────────────────────────────────────────┤
  │ 8   │ No transaction boundaries on multi-step mutations │ Concurrent requests = inconsistent state    │
  ├─────┼───────────────────────────────────────────────────┼─────────────────────────────────────────────┤
  │ 9   │ No rollback / error handling in endpoints         │ Exception after db.add() = undefined state  │
  ├─────┼───────────────────────────────────────────────────┼─────────────────────────────────────────────┤
  │ 10  │ Migration downgrades drop data                    │ Downgrade nullifies backfilled JSON columns │
  └─────┴───────────────────────────────────────────────────┴─────────────────────────────────────────────┘

  Tier 3 — Architecture (compounding debt)

  ┌─────┬───────────────────────────────────────────────────────┬──────────────────────────────────────────┐
  │  #  │                         Issue                         │                   Why                    │
  ├─────┼───────────────────────────────────────────────────────┼──────────────────────────────────────────┤
  │ 11  │ Break up message_service.py (2,166 lines)             │ Untestable god class, highest-churn file │
  ├─────┼───────────────────────────────────────────────────────┼──────────────────────────────────────────┤
  │ 12  │ Break up config_apply.py (1,794 lines)                │ No transaction safety, 30+ methods       │
  ├─────┼───────────────────────────────────────────────────────┼──────────────────────────────────────────┤
  │ 13  │ Replace module-level singletons with DI               │ Can't mock, can't test, circular imports │
  ├─────┼───────────────────────────────────────────────────────┼──────────────────────────────────────────┤
  │ 14  │ Resolve circular imports (lazy imports in 12+ places) │ Fragile import ordering, hidden coupling │
  └─────┴───────────────────────────────────────────────────────┴──────────────────────────────────────────┘

  Tier 4 — Code Quality

  ┌─────┬───────────────────────────────────────┬────────────────────────────────────────┐
  │  #  │                 Issue                 │                  Why                   │
  ├─────┼───────────────────────────────────────┼────────────────────────────────────────┤
  │ 15  │ Replace print() with logger calls     │ 6 occurrences, invisible in prod       │
  ├─────┼───────────────────────────────────────┼────────────────────────────────────────┤
  │ 16  │ Fix bare except Exception clauses     │ 3 locations swallowing errors at DEBUG │
  ├─────┼───────────────────────────────────────┼────────────────────────────────────────┤
  │ 17  │ Extract magic numbers to constants    │ 10+ hardcoded values across services   │
  ├─────┼───────────────────────────────────────┼────────────────────────────────────────┤
  │ 18  │ Add KeyError guards in tool execution │ Crashes on malformed LLM output        │
  ├─────┼───────────────────────────────────────┼────────────────────────────────────────┤
  │ 19  │ Introduce request context objects     │ Bloated 8-12 param method signatures   │
  └─────┴───────────────────────────────────────┴────────────────────────────────────────┘

  Tier 5 — Operational

  ┌─────┬─────────────────────────────────────────────────────┬─────────────────────────────────────┐
  │  #  │                        Issue                        │                 Why                 │
  ├─────┼─────────────────────────────────────────────────────┼─────────────────────────────────────┤
  │ 20  │ Separate migration from container CMD               │ Migration failure = crash loop      │
  ├─────┼─────────────────────────────────────────────────────┼─────────────────────────────────────┤
  │ 21  │ Add health checks for backend, console, scheduler   │ Silent failures in prod             │
  ├─────┼─────────────────────────────────────────────────────┼─────────────────────────────────────┤
  │ 22  │ Add request timeouts (frontend + backend streaming) │ Stuck provider = hang forever       │
  ├─────┼─────────────────────────────────────────────────────┼─────────────────────────────────────┤
  │ 23  │ Docker socket mount isolation                       │ Container escape = host access      │
  ├─────┼─────────────────────────────────────────────────────┼─────────────────────────────────────┤
  │ 24  │ Structured logging with context                     │ Unstructured logs unusable at scale │
  └─────┴─────────────────────────────────────────────────────┴─────────────────────────────────────┘

  Tier 6 — Testing

  ┌─────┬───────────────────────────────────────────────────┬─────────────────────────────────────────────────────────┐
  │  #  │                       Issue                       │                           Why                           │
  ├─────┼───────────────────────────────────────────────────┼─────────────────────────────────────────────────────────┤
  │ 25  │ Unit test suite (currently ~19 integration tests) │ Zero coverage on permissions, transactions, error paths │
  └─────┴───────────────────────────────────────────────────┴─────────────────────────────────────────────────────────┘