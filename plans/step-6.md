# Step 6: README, error budget policy, example config

Human instruction: continue without asking unless blocked. Docs and an example file only; no code changes.

## Files
- `README.md`: what it is; install (`pip install` of the repo; slo-toolkit comes as a pinned git dependency, not on
  PyPI); the input contract (agent-harness audit JSONL, fields used, fields never read, `max_cost_usd` from config
  because the log lacks it); the four SLIs with exact definitions and defaults; quick demo using only the CLI
  (`generate` -> `report` / `metrics` / `rules`); metrics and rules explained, including why alerts use the
  exact `agent_slo_window_*` gauges and not counters, the node_exporter textfile setup, a cron/systemd-timer note
  with the 15-minute stale alert; low-volume guards; limits and caveats; `promtool check rules` marked as NOT
  yet run in this repo; development (`bash scripts/check.sh`).
- Example agent error budget policy section (policy text in the README, not code): trigger = alert
  `AgentSLOBudgetExhausted` / gauge `agent_slo_budget_exhausted == 1` / report "EXHAUSTED"; action = freeze
  auto-merge for that agent and require human review; who decides to lift it; exit criteria (state returns to OK
  with at least `min_events` runs and a clean short window; manual override recorded); what burn alerts mean
  per severity (page, ticket, info); gate-block breach is a ticket, not a freeze; a note that this tool does
  not itself block merges (enforcement is the user's CI or branch protection).
- `examples/slo.toml`: the three agent-harness agents with their real `max_cost_usd` (0.30, 0.20, 0.50).
- Test: `tests/test_examples.py` loads `examples/slo.toml` and checks the three agents/limits, and that the README's
  quick-demo commands run (parsed from the README code fence marked for testing) against a tmp directory through
  subprocesses with timeouts, so the documented commands cannot rot.

## Check
`bash scripts/check.sh`, per stage.

## Amendments after plan review (all five accepted)
1. README states that one `--audit` file must hold the full history needed: at least 30 days for the budget
   (and 7 days for the longest burn window). Log rotation that drops history silently understates the budget and
   windows; combine rotated files first (`cat audit-*.jsonl > all.jsonl`). Multi-file input is not supported.
2. Privacy wording: `data` and `reasoning` are parsed by the JSON decoder (so they pass through memory) but are
   never inspected, stored, or written to any output.
3. Rules setup section: the rules file goes in Prometheus `rule_files` (then reload); the `.prom` file goes in
   node_exporter's textfile directory; they are different destinations.
4. Policy: owner and exit criteria are marked as organisation-specific placeholders with a suggested
   machine-checkable default (`agent_slo_budget_exhausted == 0` and no `page` burn alert firing for 24h).
5. `tests/test_examples.py` also asserts the README contains the freeze/human-review statement, the spend
   under-reporting caveat, and the "promtool not run" statement.
