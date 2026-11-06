# Step 9: documentation audit (accuracy and new-reader clarity)

## Method
Read every document as a new reader would, then check each factual claim against the code or a real run. Documents in
scope: README.md, REQUIREMENTS.md, plans/*, examples/slo.toml, comments in .github/*, module docstrings, `--help` output,
and the text the program generates (report, alert annotations, metric HELP).

## Intended changes (to be confirmed by the audit)
1. README: a short "How it works" section with a data-flow sketch and a glossary (agent-harness, SLI, error budget, burn
   rate, bad run, eligible, parseable, the five states); move the quick demo ahead of the reference sections; list the
   prerequisites (Python 3.11+, git, network access to GitHub); say that every alert waits `for: 5m`; pin the dev tool
   versions to those CI uses.
2. CLI: `help=` text for every option so `--help` is self-explanatory.
3. Generated text: the alert summaries come from slo-toolkit and say "(availability) ... [env=production]", which is
   wrong for this tool (no environment concept, not an availability SLO). Build the summary in agent-slo instead.
4. Report: a short "How to read this report" section (states, burn rate, budget consumed).
5. Drift tests: every metric family, alert name and config key the program emits must be mentioned in the README; every
   CLI option must have help text. These fail when code and docs diverge.
6. Fix whatever factual errors the audit finds; record each in the final report.

## Out of scope
Pairing-kit files (CLAUDE.md, AGENTS.md, skills/, scripts/) are not this project's documentation and are not edited.
Plans stay as historical records; only plans/README.md explains how to read them.

## Check
`bash scripts/check.sh` after each change, per stage; golden files regenerated only after reviewing the diff.

## Amendments after plan review (all six accepted)
**Files touched (anything else needs a plan amendment first):** `README.md` (rewritten for structure), `plans/README.md` (index
adds steps 7-9), `agent_slo/cli.py` (help text only), `agent_slo/rules.py` (alert summary text only), `agent_slo/report.py`
(adds one section), `tests/test_docs.py` (new), `tests/test_cli.py`, `tests/test_rules.py`, `tests/test_report.py`,
`tests/golden/rules.yml`, `tests/golden/report.md`. `REQUIREMENTS.md` is read-only unless the audit finds its
"Implementation notes" factually wrong about already-approved behaviour. No function signatures, types or shared-state
ownership change (everything stays synchronous and single-threaded).
- **Mismatches with confirmed requirements** are reported to the human, not silently rewritten.
- **Historical plans** are not checked against current behaviour; only their labelling and the `plans/README.md` index are audited.
- **Drift tests (authoritative sets and rule):** metric families = the `# TYPE` names emitted by `render_metrics`; alerts = every
  `alert:` name in `render_rules`; config keys = `AgentConfig` fields except `name`; CLI options = every option string of every
  subcommand parser; states = `BudgetState` names. Each must appear in `README.md` as a backticked token (`name`, or
  `` `name{labels}` `` for metrics). The recording rules are documented by the literal pattern `agent_slo:burn_rate:ratio_<window>`
  and that string is asserted.
- **Behavioural tests:** real `--help` for each subcommand through a subprocess with a timeout; every alert in the rules has
  `for: "5m"`; alert summaries contain "pass-rate", never "availability" or "env=production"; the report contains the
  "How to read this report" section naming all five states.
- The audit report to the human lists each inaccuracy and clarity problem found, with its fix.
