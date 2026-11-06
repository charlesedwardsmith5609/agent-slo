# agent-slo requirements (confirmed by human 2026-11-06)

Status: all deliverables below are implemented; the README is the current user documentation. This file records
what was agreed. Where the implementation differs from the text further down, the differences are listed in
"Implementation notes" at the end.

Source of truth for inputs: agent-harness `harness/agent.py` (audit JSONL, OTel spans).
Reference reviewed: agent-harness and slo-toolkit at the commits cloned on 2026-11-06.

## Goal
Read agent-harness audit logs, compute agent SLIs, evaluate error budgets with multi-window
burn rates, and emit Prometheus alert rules plus a weekly Markdown report.

## Decisions made
- Dependency: `slo-toolkit` as a git dependency, pinned to commit `6c54869`
  (not on PyPI). slo-toolkit itself has no dependencies, so app code stays stdlib-only.
- `max_cost_usd` is not in the audit log. A per-agent TOML config in agent-slo supplies it
  and the SLO targets. A value in the record, if a future harness logs one, takes precedence.
  agent-harness is not modified.
- Metrics path: agent-slo exports counters to a `.prom` file for the node_exporter textfile
  collector. No HTTP server.
- Low volume: custom alert windows starting at 6h/24h, and a minimum-event guard on every
  alert so one failure cannot page.

## Assumptions (confirmed)
- Every failure counts against the pass-rate budget, including provider-side API errors.
  The log has no error-type field to separate them reliably.
- Dry runs are excluded from every SLI. A dry run is any record whose `gate_reason` starts
  with "Dry run", whatever its other flags.
- Run outcome is classified from the record: pass, gate-blocked, failed, dry run.
  A pre-call cost block is a failure by the harness's own logging; the report lists it
  separately as a cost-governance event.
- Audit timestamps are UTC end-of-run times at one-second resolution; windows use them.
- Unparseable audit lines are counted and reported, never silently dropped.
- Report and metrics never include `data` or `reasoning` content.

## SLIs (per agent, over eligible = non-dry-run runs)
1. Pass rate = runs with `success` and `gate_passed` / eligible. Main SLO (burn-rate alerts).
2. Gate-block rate = gate-blocked / parseable (`success` true). Ceiling threshold, diagnostic.
   Not a second burn-rate SLO, because it overlaps pass rate.
3. Latency = share of successful runs with `duration_s` <= threshold. Report also shows p95.
4. Cost headroom = share of successful runs (failed runs always log cost 0) with `cost_usd` <= 80% of `max_cost_usd`. Report also shows
   mean and p95 of cost / max.

## Deliverables
- `agent_slo` package: audit reader, SLI calculator, config loader, Prometheus exporter and
  rules emitter, weekly Markdown report, CLI.
- Synthetic data generator for demos.
- Tests (behavioral, per CLAUDE.md) and a README with an example agent error budget policy
  (budget exhausted: freeze auto-merge for that agent and require human review).

## Out of scope
Changes to agent-harness, a metrics HTTP server, Datadog output, any UI.

## Default targets (confirmed)
Pass 95%; latency 30s at 95%; cost headroom 80% of max at 95%; gate-block ceiling 10%.

## Known caveat
A run that fails to parse after a paid API call logs cost 0, so logged spend under-reports.
The report must say so.

## Alerting policy defaults (assumed 2026-11-06 under "continue without asking"; change on request)
- Compliance window 30 days; min_events 5 (short window); at least 2 bad events in the short window.
- Windows (budget %, short/long hours, severity): Fast Burn 5%, 6h/24h, page; Slow Burn 10%,
  24h/72h, ticket; Trend 10%, 72h/168h, info. Burn thresholds come from slo-toolkit.
- Budget exhausted = observed bad-run rate over the compliance window >= error budget, with
  at least min_events eligible runs.

## Implementation notes (differences from the text above)
- Alerts do not use counters. `increase()` runs on export time and extrapolates, so alerts read exact per-window
  gauges (`agent_slo_window_eligible`, `agent_slo_window_bad`) computed from audit timestamps. The counters are
  informational. This replaces "exports counters" under "Metrics path".
- slo-toolkit supplies windows, thresholds, severities and burn-rate math, but its `to_prometheus_rules` is not
  used: it cannot filter the denominator by agent or add event guards, so `agent_slo/rules.py` writes the rules.
- A single bad run never pages or freezes an agent: burn alerts and "exhausted" also need at least 2 bad runs.
- Agents missing from the config are reported but never become metric labels (only totals are exported).
- The generator's healthy scenario uses 1.5% confidence blocks and 0.4% cost blocks, and its burst scenario is
  12 hours at 90% failures (a 5-hour burst correctly does not page under the multi-window rule).
- Limits: lines over 1 MiB, agent names over 128 characters and run ids over 64 characters are skipped; files
  with over 1,000,000 usable records are refused.
- promtool 3.16.0 validates the generated rules and metrics, and unit-tests the Fast Burn alert only.
