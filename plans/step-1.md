# Step 1: audit reader, outcome classification, SLI computation, config

Approved assumptions: targets default to pass 95%, latency 30s at 95%, cost headroom 80% of
max at 95%, gate-block ceiling 10%. Gate-block rate is a ceiling diagnostic, not a burn-rate SLO.
Failures of every cause count against pass rate. Dry runs excluded everywhere.

## Scope
In: read audit JSONL, classify runs, compute the four SLIs from counts, load per-agent TOML config.
Out (later steps): slo-toolkit dependency and burn rates (step 2, pure math needs it first there),
.prom export and alert rules, Markdown report, CLI, generator, README/policy.
No new dependencies. Standard library only.

## Concurrency and ownership
Synchronous, single-threaded, no asyncio, no network. No shared mutable state: functions take
inputs and return frozen dataclasses. The caller owns the list of records.

## Files
- `agent_slo/audit.py`
  - `Outcome` enum: PASS, GATE_BLOCKED, FAILED, DRY_RUN.
  - `RunRecord` (frozen): agent_name, run_id, timestamp (tz-aware UTC), outcome, cost_usd,
    duration_s, precall_cost_block (bool), logged_max_cost_usd (float | None, read if present).
    `data` and `reasoning` are never stored.
  - `classify(raw) -> RunRecord`: DRY_RUN = gate_reason starts with "Dry run" (prefix alone,
    checked first, regardless of other flags); FAILED = not success (precall_cost_block when reason contains "Worst-case cost");
    GATE_BLOCKED = success and not gate_passed; else PASS.
  - `read_audit(path) -> AuditReadResult(records, skipped: dict[reason, count])`. Streams line by
    line in binary mode with `readline(limit + 1)`; a line longer than 1 MiB is
    drained in chunks to its newline without being held in memory, counted `too_long`, and the next
    line is read normally. Strict UTF-8, blank lines ignored. A line is skipped and counted
    (reasons: invalid_json, not_object, missing_field, bad_type, bad_timestamp, bad_number,
    too_long, bad_encoding) if a needed field is absent or wrongly typed, a number is
    NaN/inf/negative (and logged max_cost_usd must be > 0), or bool is used where a number is expected. Duplicate run_ids are kept
    (the harness id is 8 hex characters; collisions are possible).
- `agent_slo/config.py`
  - `AgentConfig` (frozen): name, max_cost_usd, pass_target, latency_threshold_s, latency_target,
    cost_headroom_fraction, cost_headroom_target, gate_block_ceiling.
  - `load_config(path)` with `tomllib`: `[defaults]` plus `[agents.<name>]` overrides.
    `max_cost_usd` required per agent and > 0. Targets must be in (0,1); thresholds positive; unknown
    keys rejected with a clear `ConfigError`.
- `agent_slo/sli.py`
  - `compute_slis(records, agent_config, start, end) -> AgentSLIs`: window is [start, end). `start` and `end` must be tz-aware
    (naive raises ValueError) and `start < end`; offsets are normalized to UTC.
    Only the config's agent. Max cost = record's logged value if present (> 0), else config.
    Cost headroom is successful-runs-only (failed runs always log cost 0); decided by the human.
  - Counts: eligible (non-dry), passed, gate_blocked, failed, parseable (passed + gate_blocked),
    latency_good/total (PASS and GATE_BLOCKED runs, i.e. successful), cost_good/total (successful
    runs, cost <= fraction * max), precall_cost_blocks.
  - Ratios are `None` when the denominator is 0 (no data is not 100%).
  - p95 duration and p95/mean of cost/max by nearest-rank over successful runs; `None` if empty.
  - `records_for_unconfigured_agents(records, configs) -> dict[str, int]` so the caller can report
    agents seen in logs but absent from config.
- `pyproject.toml`: unchanged.

## Tests (pytest, real files in tmp_path, no sleeps, no network)
- `tests/fixtures/harness_audit_sample.jsonl`: lines in the exact shape `_audit` writes. Failed and
  dry-run lines produced by running the real agent-harness checkout (fail-closed and dry_run paths)
  in a scratch directory; pass and gate-blocked lines written by hand in the same shape.
- test_audit: each outcome classified from the fixture; precall cost block flagged; `data` and
  `reasoning` absent from the record; malformed JSON, non-object, missing field, wrong type, bad
  timestamp, NaN, negative, bool-as-number, invalid UTF-8, oversize line each skipped with the
  right reason and the good lines still read; empty file; missing file raises FileNotFoundError.
- test_config: defaults merge, per-agent override, missing max_cost_usd, out-of-range target,
  unknown key, malformed TOML each raise ConfigError.
- test_sli: counts and ratios on a hand-built set; dry runs excluded; window boundaries (start
  inclusive, end exclusive); zero-denominator gives None; naive datetime and start >= end raise; non-UTC offsets normalized; nearest-rank p95 on a known list;
  logged max_cost_usd overrides config; only the requested agent counted; unconfigured agents
  reported.

## Check
`bash scripts/check.sh`: ruff check, ruff format --check, mypy, pytest; report per stage.
