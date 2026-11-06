# Step 2: slo-toolkit dependency, error budget and multi-window burn-rate evaluation

Approved earlier: git dependency on slo-toolkit pinned to commit 6c548691bfd52ca6e45a98ded2b98c3f6999716e.
Human instruction: continue through remaining steps without asking unless blocked.

## Scope
In: pyproject dependency; `min_events` config key; `agent_slo/budget.py` evaluating the pass-rate
SLO with slo-toolkit math; mypy config for the untyped dependency.
Out: .prom export and alert rule text (step 3), report (step 4), generator and CLI (step 5), README (step 6).
No other new dependencies; slo-toolkit itself has none, so app code stays stdlib-only.

## Concurrency and ownership
Synchronous, single-threaded, pure functions over immutable inputs. No shared state.

## Design
- `pyproject.toml`: `dependencies = ["slo-toolkit @ git+https://github.com/charlesedwardsmith5609/slo-toolkit@<sha>"]`.
  slo-toolkit ships no `py.typed`; add a mypy override `follow_untyped_imports = true` for `slo_toolkit.*`
  (if the installed mypy lacks it, fall back to `ignore_missing_imports` for that module only).
- `config.py`: new key `min_events` (integer >= 1, default 5; bool/float rejected). Meaning: the
  short window of a burn alert needs at least this many eligible runs before it can fire.
- `agent_slo/budget.py`
  - `DEFAULT_WINDOWS` for low-volume agents as slo-toolkit custom windows
    `(name, budget_pct, short_h, long_h, severity)`: Fast Burn 5% / 6h / 24h page;
    Slow Burn 10% / 24h / 72h ticket; Trend 10% / 72h / 168h info.
    Burn thresholds come from `AlertConfigGenerator.generate(custom_windows=...)` on
    `BurnRateCalculator(slo_target=pass_target, compliance_window_days=30)`; nothing recomputed by hand.
  - `evaluate_budget(records, config, now, compliance_days=30, windows=DEFAULT_WINDOWS) -> BudgetStatus`.
    Bad event = eligible run that is not a pass (failed or gate-blocked). Counts come from
    `compute_slis` per window ending at `now` (so dry runs and window rules are shared).
  - Per window: short/long burn rate via `calculate_from_counts`, short/long event counts, `firing` =
    both burn rates > threshold and short events >= min_events; `insufficient_data` when short events < min_events.
  - Budget over the compliance window: `consumed_fraction` = (bad/eligible) / error_budget, via
    slo-toolkit `BurnRateCalculator.calculate_from_counts`; `exhausted` = consumed >= 1 AND eligible >=
    min_events; `status` is slo-toolkit's `BurnRateStatus`, or None with no eligible runs. This
    `exhausted` flag is what the error budget policy keys on.
  - Result dataclasses are frozen; `now` must be tz-aware.
- Gate-block rate stays a ceiling check, reported as `gate_block_breach` (rate > ceiling with parseable >= min_events).

## Tests
- Windows and thresholds equal slo-toolkit output for a 95% SLO (6x fast, 3x slow, 1x trend on 30d).
- Healthy history: no window firing, not exhausted.
- Failure burst inside 6h: Fast Burn fires; the same burst with a short window below min_events does not.
- Old failures only in the long window (short window clean): does not fire (both windows required).
- Budget exhausted flag at/over/under 100% consumption; exhausted false under min_events.
- No data gives status None, nothing firing. Dry runs ignored. Naive `now` raises.
- Gate-block breach true/false and guarded by min_events.
- Config: min_events accepted/rejected cases.

## Check
`bash scripts/check.sh`, per stage.

## Amendments after plan review (all six findings accepted)
1. Firing also requires at least `MIN_BAD_EVENTS = 2` bad events in the short window, so one failure
   can never page, whatever the target. Test: 1 failure in 5 runs at a 99% target does not fire.
2. Raw consumption (`consumed_fraction`, may exceed 1) is kept. The value given to slo-toolkit is
   `min(1.0, raw)`. Exhausted = raw >= 1 and eligible >= min_events.
3. Policy defaults (min_events 5, min bad 2, 5%/10%/10% budget fractions, 6h/24h/72h short and
   24h/72h/168h long windows, severities, 30-day compliance) are listed in REQUIREMENTS.md as
   assumptions made under the "continue without asking" instruction, each overridable by argument.
4. Drop toolkit `BurnRateStatus` from the result. Own `BudgetState`: NO_DATA (no eligible runs),
   INSUFFICIENT_DATA (eligible < min_events), OK, BURNING (any window firing), EXHAUSTED
   (takes precedence).
5. `evaluate_budget` takes `Sequence[RunRecord]`.
6. Test: gate-blocked-only history counts as bad events, burns, and can exhaust the budget.
7. (Found while writing tests) Exhausted also requires at least MIN_BAD_EVENTS bad events in the
   compliance window, so one failure in a handful of runs cannot freeze an agent.
