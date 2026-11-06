# Step 4: weekly Markdown report

Human instruction: continue without asking unless blocked.

## Scope
`agent_slo/report.py`: `render_report(records, skipped, configs, now, *, week_days=7, compliance_days=30) -> str`.
Out: generator and CLI (step 5), README (step 6). No new dependencies. Synchronous, pure, single-threaded;
no shared state. Deterministic for a given input and `now` (tz-aware, else ValueError).

## Content
- Heading, period (`now - week_days` to `now`, UTC), input summary: records read, skipped lines by reason
  (reasons with 0 omitted; a note if any were skipped, because skipped lines bias the SLIs).
- Per configured agent (sorted):
  - Budget line: state, 30d budget consumed % (raw, may exceed 100), and when EXHAUSTED the policy
    action: "Error budget exhausted: freeze auto-merge for this agent and require human review."
  - Firing burn windows (name, severity, short/long burn vs threshold) or "none".
  - Week SLIs against targets, each with PASS/MISS/NO DATA: pass rate vs target, gate-block rate vs ceiling,
    latency (share within threshold, plus p95 seconds), cost headroom (share under fraction x max, plus mean
    and p95 of cost/max). Ratios under `min_events` denominators show "insufficient data (n < min_events)".
  - Counts: eligible, passed, gate-blocked, failed, pre-call cost blocks.
- Unconfigured agents seen in logs (count only, top 20, rest summarized).
- Caveats section, always present: dry runs excluded; cost is logged as 0 for failed runs, including a run that
  fails to parse after a paid API call, so spend is under-reported; audit timestamps are end-of-run UTC to the
  second; skipped lines; SLIs depend on `gate_reason` text conventions of agent-harness.
- Never includes `data` or `reasoning` (not stored). Agent names from logs are untrusted: control characters
  stripped, truncated to 64 chars, and Markdown/HTML metacharacters escaped. Config names are already restricted.

## Tests
Golden Markdown for a fixed dataset (healthy agent, exhausted agent, no-data agent); exhausted-only policy line;
below-min_events wording; skipped-line section; escaping of a hostile unconfigured agent name (pipes, backticks,
`<script>`, newline, ESC); caveats present; naive `now` raises; empty input renders without error.

## Amendments after plan review (all six accepted)
1. Signature takes `records: Sequence[RunRecord]`, `skipped: Mapping[str, int]`, `configs: Mapping[str, AgentConfig]`;
   records are bucketed by agent once and each agent's bucket is passed to `compute_slis`/`evaluate_budget`.
2. Untrusted names are not stripped: each control character is rendered visibly as `\xNN`/`\uNNNN`, backslash
   and Markdown/HTML metacharacters are backslash-escaped, so `prod\n` never looks like `prod`. Names over 64
   characters are cut and given a `#` plus 8 hex characters of their SHA-1, so truncation cannot collide.
3. Check section added: `bash scripts/check.sh`, reported per stage.
4. Test: eligible >= min_events while parseable < min_events (mostly failures): pass rate has a verdict,
   gate-block, latency and cost show insufficient data, each independently.
5. Weekly interval is [now - week_days, now + 1s) so a run stamped exactly at `now` counts in both the weekly SLIs
   and the budget; `now` is normalised to UTC. Tests: boundary records and a non-UTC `now`.
6. `week_days` and `compliance_days` must be positive ints (bool rejected), else ValueError; tested.

## Check
`bash scripts/check.sh`, per stage.
