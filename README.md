# agent-slo

Reads [agent-harness](https://github.com/charlesedwardsmith5609/agent-harness) audit logs and
computes agent SLIs, error budgets and multi-window burn rates. It emits Prometheus metrics, Prometheus
alert rules and a weekly Markdown report. The burn-rate math comes from
[slo-toolkit](https://github.com/charlesedwardsmith5609/slo-toolkit).

Application code uses only the Python standard library (3.11+). slo-toolkit is the one dependency; it is not
on PyPI, so it is pinned to a git commit in `pyproject.toml`.

```bash
python3 -m venv .venv && .venv/bin/pip install -e .   # needs network access to GitHub
```

## What it reads

One agent-harness audit JSONL file (`AGENT_AUDIT_LOG`). Per line it uses `agent_name`, `run_id`, `success`,
`gate_passed`, `gate_reason`, `cost_usd`, `duration_s` and `timestamp`. The `data` and `reasoning` fields are
parsed by the JSON decoder but never inspected, stored, or written to any output.

The log does **not** record `max_cost_usd`, so it comes from a config file ([`examples/slo.toml`](examples/slo.toml)).
If a future harness logs `max_cost_usd` on the record, that value wins.

Each run falls into one outcome:

- **pass**: `success` and `gate_passed`
- **gate-blocked**: parseable output, but the confidence or cost gate blocked it
- **failed**: an exception (API error, malformed JSON, missing key, pre-call cost refusal)
- **dry run**: `gate_reason` starts with "Dry run"; excluded from everything

Unusable lines are counted by reason and shown in the report and metrics, never silently dropped.

**Keep the full history in the input file.** The 30-day budget and the 7-day window need that much history in
the single `--audit` file. If log rotation drops older runs, the budget is understated without any warning.
Combine rotated files first (`cat audit-*.jsonl > all.jsonl`). Multiple input files are not supported.

## SLIs (per agent, dry runs excluded)

| SLI | Definition | Default target |
|---|---|---|
| Pass rate | passed / eligible runs. The burn-rate SLO. | 95% |
| Gate-block rate | gate-blocked / parseable runs. A ceiling, not a burn-rate SLO. | at most 10% |
| Latency | successful runs with `duration_s` at or under the threshold | 30 s at 95% |
| Cost headroom | successful runs with cost at or under 80% of `max_cost_usd` | 95% |

All failures count against pass rate, including provider-side API errors: the log cannot tell the causes apart.
Latency and cost use successful runs only because failed runs always log cost 0 and a meaningless duration.
Reports also show p95 duration and the mean and p95 of cost divided by `max_cost_usd`. A ratio built from fewer
than `min_events` (default 5) runs is reported as "insufficient data", never as a pass.

## Quick demo

```bash test-demo
python -m agent_slo generate --config examples/slo.toml --out audit.jsonl \
  --scenario deploy-risk=degrading --scenario runbook-gen=burst
python -m agent_slo report --config examples/slo.toml --audit audit.jsonl --out report.md
python -m agent_slo metrics --config examples/slo.toml --audit audit.jsonl --out agent_slo.prom
python -m agent_slo rules --config examples/slo.toml --out rules.yml
```

`generate` writes 30 days of synthetic runs in exactly the shape the harness writes. Scenarios: `healthy`
(default), `degrading` (failure rate ramps from 1% to 25%), `burst` (90% failures in the final 12 hours).
`report.md` then shows `deploy-risk` EXHAUSTED, `runbook-gen` burning and `pr-reviewer` OK. Use `--seed` for a
different dataset and `--now` (ISO 8601 with a timezone) to pin the clock.

## Metrics and alert rules

`metrics` writes a Prometheus text file. Put it where node_exporter's **textfile collector** reads, and re-run
it on a schedule (cron or a systemd timer, every minute or two). It is written atomically. Do not let runs
overlap: a slow older run could replace a newer file with stale values. Use `flock -n /run/agent-slo.lock ...` in
cron, or a systemd `Type=oneshot` service, which does not overlap itself.

One export reads the whole file, so cost grows with file size (up to 1,000,000 records are accepted).

`rules` writes a rule file. Add it to the `rule_files:` list in `prometheus.yml` and reload Prometheus. This is a
different destination from the `.prom` file: node_exporter does not read rule files.

Alerts (per agent):

- `AgentSLOFastBurn` (page): 5% of the 30-day budget in 6 hours, burn above 6x, confirmed over 24 hours
- `AgentSLOSlowBurn` (ticket): 10% in 24 hours, burn above 3x, confirmed over 72 hours
- `AgentSLOTrend` (info): 10% in 72 hours, burn above 1x, confirmed over 168 hours
- `AgentSLOBudgetExhausted` (page): the 30-day budget is used up
- `AgentSLOGateBlockBreach` (ticket): gate-block rate above its ceiling
- `AgentSLOExportStale` (page, global): the metrics file is missing or older than 15 minutes

A burn alert fires only when both its windows exceed the threshold, the short window holds at least `min_events`
runs, and it holds at least 2 bad runs. One failure can never page or freeze an agent. These windows start at 6
hours because agents run tens of times a day; slo-toolkit's own 1-hour windows would be mostly empty.
Thresholds come from slo-toolkit. Budget exhausted means the bad-run rate over 30 days is at or above the error
budget, with the same minimums.

Names of agents that are not in the config never become metric labels (the log is untrusted and unbounded); only
totals are exported, and the report lists them.

Alerts read exact per-window counts (`agent_slo_window_eligible`, `agent_slo_window_bad`) computed from audit
timestamps at export time. The `*_total` counters are informational only: Prometheus sees them change when the
exporter runs, not when the runs happened, and `increase()` extrapolates.

**Verification.** With `promtool` installed (the tests skip if it is not), the test suite runs
`promtool check rules` and `promtool check metrics` on the generated files and `promtool test rules` on the Fast
Burn alert (it fires when both windows and the guards hold, and stays silent with a single bad run). Tested with
promtool 3.16.0. The other alerts are covered by golden-file and arithmetic tests but not by `promtool test rules`.
Run `promtool check rules rules.yml` yourself before loading the rules.

## Example: agent error budget policy

Adapt this to your organisation. Items in [brackets] are placeholders you must fill in.

**Scope.** Each agent has its own 30-day pass-rate budget (default 95%). Gate-blocked and failed runs both use it.

**When the budget is exhausted** (alert `AgentSLOBudgetExhausted`, `agent_slo_budget_exhausted == 1`, or
"EXHAUSTED" in the weekly report):

1. Freeze auto-merge for that agent. Its output may not merge or deploy without a human review.
2. Require human review of every output from that agent until the freeze is lifted.
3. Open an incident-style ticket with the report section attached. Prioritise reliability work over new
   features for that agent.

**Burn alerts before exhaustion.**

- Fast Burn (page): investigate now; consider the freeze early if failures are not a provider outage.
- Slow Burn (ticket): triage within [one working day].
- Trend (info): review at the weekly SLO meeting.
- Gate-block breach (ticket): the agent is being caught by its own gates. Review prompts and thresholds. This
  alone does not freeze anything.

**Lifting the freeze.** Decision owner: [team or role]. Suggested machine-checkable condition:
`agent_slo_budget_exhausted == 0` and no `page` burn alert firing for 24 hours. A manual override is allowed but
must be recorded with who approved it and why.

**Enforcement is yours.** agent-slo reports and alerts; it does not block merges. Wire the alert or the
`agent_slo_budget_exhausted` gauge into your CI or branch protection.

## Caveats

- Spend is under-reported: failed runs log cost 0, including a run that fails to parse after a paid API call.
- Outcomes are read from agent-harness `gate_reason` wording. A harness change can misclassify runs; the
  fixture in `tests/fixtures` was produced by the real harness, but nothing re-checks it automatically.
- Timestamps are end-of-run UTC to the second.
- The harness swallows audit-write errors, so a missing line is invisible to this tool.
- The default windows and thresholds are policy choices, not measurements; tune them to your volume.

## Development

```bash
bash scripts/check.sh   # ruff check, ruff format --check, mypy, pytest
```
