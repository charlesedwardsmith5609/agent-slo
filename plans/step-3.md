# Step 3: Prometheus textfile metrics and alert rules

Human instruction: continue without asking unless blocked. Metrics path decided earlier: textfile
collector `.prom` file, no HTTP server.

## Scope
In: `agent_slo/metrics.py` (render + atomic write of the exposition text) and `agent_slo/rules.py`
(render Prometheus rule file). Out: report (4), generator and CLI (5), README (6).
No new dependencies. Synchronous, single-threaded, pure functions; the only side effect is the
atomic file write in `write_textfile`, owned by the caller's single export run.

## Metrics (all agent label values come from validated config names; labels from the log are escaped)
Counters (totals over every record in the audit file; Prometheus handles resets if the file rotates):
- `agent_runs_total{agent,outcome}` outcome in pass|gate_blocked|failed (dry runs excluded; every
  configured agent and outcome is emitted, even at 0).
- `agent_run_latency_good_total{agent}`: successful runs with duration <= latency threshold.
- `agent_run_cost_headroom_good_total{agent}`: successful runs with cost <= fraction x max.
- `agent_run_precall_cost_blocks_total{agent}`.
- `agent_slo_audit_lines_skipped_total{reason}` for all reasons, and
  `agent_slo_unconfigured_runs_total{agent}` (top 20 by count; the rest summed as `__other__`).
Gauges (over the compliance window ending at export time):
- `agent_slo_budget_consumed_ratio{agent}` (omitted with no data), `agent_slo_budget_exhausted{agent}` 0/1,
  `agent_slo_gate_block_breach{agent}` 0/1, `agent_slo_state{agent,state}` one-hot over BudgetState.
- `agent_slo_sli_ratio{agent,sli}` and `agent_slo_sli_target{agent,sli}` for pass_rate, latency, cost_headroom;
  the ratio sample is omitted when its denominator < min_events, so a missing series means "no verdict".
- `agent_slo_export_timestamp_seconds` (unix time of the export).
`write_textfile(path, text)`: write to a temp file in the same directory, flush, then `os.replace`, so
node_exporter never reads a partial file.

## Rules (`render_rules(configs, compliance_days=30, windows=DEFAULT_WINDOWS) -> str`)
slo-toolkit's own `to_prometheus_rules` cannot filter the denominator by agent or add event guards, so
this module emits its own YAML but takes every window, threshold and `for:` value from
`AlertConfigGenerator.generate(...)` (same source as `evaluate_budget`). String scalars are JSON-quoted.
Per agent one group:
- recording rules `agent_slo:burn_rate:ratio_<h>h{agent}` = bad-rate / error budget, for each distinct window hour
  (hours rendered without truncation: a non-integer hour is rendered in minutes, e.g. `90m`).
- one alert per window: both ratios > threshold AND eligible runs in the short window >= min_events AND bad
  runs in the short window >= MIN_BAD_EVENTS. Severity label from the toolkit.
- `AgentSLOBudgetExhausted` (`agent_slo_budget_exhausted{agent} == 1`, page), `AgentSLOGateBlockBreach` (ticket),
  `AgentSLITargetMissed` (latency and cost_headroom ratio < target for 1h, ticket).
One global group: `AgentSLOExportStale` (time() - export timestamp > 900s).

## Tests
- Metrics: rendered text parsed back by a small test parser; counters equal hand-counted values for a fixture,
  dry runs absent, zero-valued series present, one-hot state sums to 1, ratio omitted under min_events,
  escaping of `\`, `"` and newline in an unconfigured agent name, `__other__` bucket beyond 20 names,
  skipped-line counters, TYPE/HELP lines present once per family.
- `write_textfile`: file appears complete, no temp file left, overwrite replaces, failure leaves old file intact.
- Rules: golden file for a two-agent config; thresholds equal `evaluate_budget` thresholds; every alert has the
  min-events and min-bad guard; non-integer window hours render as minutes; JSON-quoted strings round-trip.
- Unverified (no promtool in this environment): that Prometheus accepts the file. The README will say so and
  give the `promtool check rules` command.

## Amendments after plan review
1+2 (accepted). Counter `increase()` uses export time and extrapolates, so alerts must not use it. The
   exporter computes exact per-window counts from audit timestamps and exports gauges
   `agent_slo_window_eligible{agent,window}` and `agent_slo_window_bad{agent,window}` for every distinct
   short/long window and the compliance window (labels like `6h`, `720h`; a non-integer hour is `90m`).
   Recording rules and both event guards use these gauges only. The counters stay as informational
   totals for dashboards and are not used by any alert. `budget.window_counts()` is factored out of
   `evaluate_budget` so both use one definition.
3 (partly accepted). `promtool` is not installed and cannot be installed without approval, so
   "Prometheus accepts the file" stays UNVERIFIED and is reported as such, with the command to run.
4 (accepted). `AgentSLOExportStale` is `time() - ts > 900 or absent(ts)`; test asserts both branches exist.
5 (accepted). Overflow is its own metric `agent_slo_unconfigured_other_runs_total`; a test uses an agent
   literally named `__other__`.
6 (accepted). `AgentSLITargetMissed` is removed. Latency and cost headroom ratio/target gauges stay (no
   alert), and the report (step 4) shows them.
7 (accepted). `write_textfile` uses `tempfile.mkstemp` in the target directory, `fchmod 0o644`, flush and
   fsync, `os.replace`, and `finally` removes the temp file. Tests: replace failure keeps the old file and
   leaves no temp; unwritable directory raises and leaves nothing.
Rule PromQL uses `and on(agent)` because the guard gauges carry an extra `window` label.

## Change after cross-model code review (2026-11-06)
Unconfigured agent names no longer become metric labels at all (even top-20 plus overflow is unbounded over time):
`agent_slo_unconfigured_runs_total` and `agent_slo_unconfigured_agents` are label-free aggregates; names appear only,
escaped, in the report. Records are also grouped by agent once per export.
