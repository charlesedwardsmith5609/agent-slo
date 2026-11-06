# Agent SLO weekly report

- Period: 2026-11-13 12:00 UTC to 2026-11-20 12:00 UTC (7 days)
- Error budget window: 30 days
- Audit records read: 69
- Audit lines skipped: 2 (too_long: 2). Skipped lines bias every SLI.

## Summary

- burning: EXHAUSTED (freeze auto-merge, require human review)
- healthy: OK
- quiet: NO_DATA

## burning

- State: **EXHAUSTED** (30d budget consumed: 909%; 10 bad of 22 eligible runs)

> **Action required:** Error budget exhausted: freeze auto-merge for this agent and require human review.

- Burn alerts firing:
  - Slow Burn (ticket): burn 9.1x over 24h and 9.1x over 72h (threshold 3x)
  - Trend (info): burn 9.1x over 72h and 9.1x over 168h (threshold 1x)

This week:
- Pass rate 54.5% (target 95.0%); verdict: MISS
- Gate-block rate 25.0% (ceiling 10.0%); verdict: MISS
- Latency: 100.0% within 10s (target 90.0%), p95 2.0s; verdict: PASS
- Cost headroom: 75.0% of runs at or under 80% of $1 (target 90.0%); mean 38% of max, p95 90% of max; verdict: MISS
- Runs: 22 eligible, 12 passed, 4 gate-blocked, 6 failed (6 refused by the pre-call cost ceiling)

## healthy

- State: **OK** (30d budget consumed: 0%; 0 bad of 42 eligible runs)
- Burn alerts firing: none

This week:
- Pass rate 100.0% (target 95.0%); verdict: PASS
- Gate-block rate 0.0% (ceiling 10.0%); verdict: PASS
- Latency: 95.2% within 10s (target 90.0%), p95 2.0s; verdict: PASS
- Cost headroom: 100.0% of runs at or under 80% of $1 (target 90.0%); mean 20% of max, p95 20% of max; verdict: PASS
- Runs: 42 eligible, 42 passed, 0 gate-blocked, 0 failed (0 refused by the pre-call cost ceiling)

## quiet

- State: **NO_DATA** (30d budget consumed: no data; 0 bad of 0 eligible runs)
- Burn alerts firing: none

This week:
- Pass rate n/a (target 95.0%); verdict: no data
- Gate-block rate n/a (ceiling 10.0%); verdict: no data
- Latency: n/a within 10s (target 90.0%), p95 n/a; verdict: no data
- Cost headroom: n/a of runs at or under 80% of $1 (target 90.0%); mean n/a, p95 n/a; verdict: no data
- Runs: 0 eligible, 0 passed, 0 gate-blocked, 0 failed (0 refused by the pre-call cost ceiling)

## Agents in the logs but not in the config

- `ghost`: 2 runs

## How to read this report

- Eligible runs exclude dry runs. A run is *bad* if it failed or a gate blocked it; everything else eligible is a pass.
- Budget consumed compares the bad-run rate over the budget window with the error budget (1 minus the pass target). 100% or more means the budget is exhausted.
- Burn rate is how many times faster than the sustainable pace the budget is being spent (1x is exactly sustainable). An alert fires only when both its short and its long window exceed the threshold.
- States: NO_DATA (no eligible runs), INSUFFICIENT_DATA (fewer than `min_events` runs), OK, BURNING (at least one burn alert is firing), EXHAUSTED (budget used up; takes precedence).
- Verdicts: PASS or MISS against the target; "insufficient data" means too few runs to judge, which is not a pass.

## Caveats

- Dry runs are excluded from every figure.
- Failed runs always log cost 0, including a run that fails to parse after a paid API call, so spend is under-reported.
- Timestamps are end-of-run UTC to the second.
- Outcomes are read from agent-harness `gate_reason` wording; a harness change can misclassify runs.
- Fewer than `min_events` runs gives no verdict, not a pass.
