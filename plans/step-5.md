# Step 5: synthetic data generator and CLI

Human instruction: continue without asking unless blocked.

## Scope
- `agent_slo/generate.py`: `generate_records(configs, start, end, *, seed, runs_per_day, scenarios) -> list[dict]`
  and `write_audit(path, records)`. Deterministic: one `random.Random(seed)` owned by the call; no global state.
  Output lines have exactly the field set agent-harness `_audit` writes, so `read_audit` and the real harness
  fixture agree. Scenarios per agent: `healthy` (about 1% failures, 3% confidence blocks, ~1% dry runs, a rare
  pre-call cost block), `degrading` (failure probability ramps from 1% to 25% across the span), `burst`
  (healthy except 60% failures in the final 5 hours). Gate-blocked and failed lines use the harness's real
  `gate_reason` wording. `data`/`reasoning` hold fixed placeholder text only.
- `agent_slo/cli.py` + `agent_slo/__main__.py` (`python -m agent_slo`), argparse, subcommands:
  `generate`, `metrics`, `rules`, `report`. Common: `--config`, `--audit`, `--now ISO8601` (default current UTC,
  must be tz-aware). Output via `--out` (written atomically with `write_textfile`) or stdout for rules/report.
  Errors (ConfigError, FileNotFoundError, AuditLimitError, bad arguments) print one line to stderr and exit 2;
  success exits 0. No other commands (no status/exit-code gating: listed under suggestions).
No new dependencies. Synchronous, single-threaded. The CLI process owns the output files; nothing is shared.

## Tests
- Generator: same seed gives byte-identical output; different seed differs; every line parses via `read_audit`
  with zero skipped; key set equals the key set of a real harness line from `tests/fixtures`; counts roughly
  match scenario (healthy: pass rate > 90%; degrading ends exhausted via `evaluate_budget`; burst fires Fast
  Burn at `now`); timestamps inside [start, end) and whole seconds; `runs_per_day` honoured; unknown scenario or
  unconfigured agent name raises ValueError; ids are 8 hex characters.
- CLI end to end through `main(argv)` with real files in tmp_path: generate -> metrics -> rules -> report;
  outputs exist and are non-empty; bad config returns exit 2 with stderr message; missing audit file exit 2;
  naive `--now` exit 2; `python -m agent_slo --help` runs via subprocess with a timeout.

## Amendments after plan review (all six accepted)
1. Arguments. `generate`: `--config`, `--out` (required); `--days` (30), `--runs-per-day` (40), `--seed` (1),
   `--now` (end of span, default current UTC), `--scenario AGENT=NAME` repeatable (agents not named are
   `healthy`; an agent not in the config, or an unknown scenario name, is an error). `metrics`: `--config`,
   `--audit`, `--out` required; `--now`. `rules`: `--config` required; `--out` optional (stdout). `report`:
   `--config`, `--audit` required; `--out` optional (stdout); `--now`. Only the listed options exist per subcommand.
2. `generate_records` validates before allocating: tz-aware, `start < end`, `runs_per_day` a positive int (bool
   rejected), and agents x days x runs_per_day <= `DEFAULT_MAX_RECORDS`, else ValueError. Tests at the limits.
3. One shared `agent_slo/files.py: write_atomic(path, text)` (the step-3 implementation moved; metrics keeps
   `write_textfile` as an alias). `write_audit` and every CLI `--out` use it. Existing tests cover failure paths;
   add a `write_audit` failure test that keeps the old file and leaves no temp file.
4. CLI tests run through real subprocesses (`python -m agent_slo`, `sys.executable`, timeout on each): the full
   generate -> metrics -> rules -> report pipeline, plus failure cases (bad config, missing audit, naive `--now`,
   unknown scenario, bad option) checking exit code 2, stderr text and empty stdout. `main(argv)` is also unit-tested.
5. Scenario contracts with fixed seeds and explicit assertions: degrading failure rate in the first vs last third
   of the span (e.g. < 6% vs > 15%); burst failures only in the final 5 hours and >= 40% there, < 6% before;
   healthy has at least one dry run, one confidence block, and (with enough runs) a pre-call cost block.
6. Contract test against the real-harness fixture: identical key set and identical Python type per key for each
   outcome class; each generated outcome class classifies back to the intended `Outcome` via `read_audit`; flags
   and `gate_reason` combinations match the harness (`gate_reason` empty on pass, `Confidence ...`, `Cost ...`,
   `Agent execution failed: ...`, `Dry run: ...`).

## Check
`bash scripts/check.sh`, per stage.

## Tuning note (found by the scenario budget test)
Healthy block rates lowered to 1.5% confidence blocks and 0.4% cost blocks (plus ~1% failures). With the original
3% and 1%, a "healthy" agent spent about 5% (all non-pass outcomes burn the pass-rate budget), which is exactly
the 95% target's budget, and was reported EXHAUSTED.
Burst scenario changed from 5 hours at 60% to 12 hours at 90% failures: the multi-window rule correctly did not
page for the smaller burst (24h burn 3.0x < 6x threshold), so the demo needs a burst large enough to page.
