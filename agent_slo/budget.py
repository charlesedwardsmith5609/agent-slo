"""Pass-rate error budget and multi-window burn-rate evaluation.

The burn-rate math and window thresholds come from slo-toolkit (pinned git dependency). This
module only decides which runs count as bad, applies the low-volume guards, and reports state.
Synchronous, single-threaded, pure functions over immutable inputs.

Bad event = an eligible (non-dry-run) run that is not a pass: failed or gate-blocked.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum, auto

from slo_toolkit import AlertConfigGenerator, BurnRateCalculator
from slo_toolkit.alert_config import AlertWindow

from agent_slo.audit import RunRecord
from agent_slo.config import AgentConfig
from agent_slo.sli import compute_slis

# (name, % of budget, short window h, long window h, severity); slo-toolkit custom_windows format.
# Chosen for agents that run tens of times a day, so they start at 6h rather than 1h.
DEFAULT_WINDOWS: tuple[tuple[str, float, float, float, str], ...] = (
    ("Fast Burn", 5, 6, 24, "page"),
    ("Slow Burn", 10, 24, 72, "ticket"),
    ("Trend", 10, 72, 168, "info"),
)
# A single bad run must never page or freeze an agent, whatever the target.
MIN_BAD_EVENTS = 2
SLO_NAME = "pass-rate"


class BudgetState(Enum):
    NO_DATA = auto()  # no eligible runs in the compliance window
    INSUFFICIENT_DATA = auto()  # fewer than min_events eligible runs
    OK = auto()
    BURNING = auto()  # at least one burn-rate window is firing
    EXHAUSTED = auto()  # takes precedence over BURNING


@dataclass(frozen=True)
class WindowEval:
    name: str
    severity: str
    short_hours: float
    long_hours: float
    threshold: float  # burn rate; fires when both windows exceed it
    short_burn: float
    long_burn: float
    short_events: int
    short_bad: int
    firing: bool


@dataclass(frozen=True)
class BudgetStatus:
    agent: str
    now: datetime
    state: BudgetState
    compliance_days: int
    error_budget: float
    eligible: int
    bad: int
    consumed_fraction: float | None  # raw bad-rate / error budget; may exceed 1
    exhausted: bool
    gate_block_breach: bool
    windows: tuple[WindowEval, ...]


def build_windows(
    config: AgentConfig,
    compliance_days: int = 30,
    windows: Sequence[tuple[str, float, float, float, str]] = DEFAULT_WINDOWS,
) -> tuple[BurnRateCalculator, list[AlertWindow]]:
    """slo-toolkit's calculator and alert windows (thresholds, severities, pending time)."""
    calc = BurnRateCalculator(slo_target=config.pass_target, compliance_window_days=compliance_days)
    generated = AlertConfigGenerator(calc).generate(
        service=config.name, slo_name=SLO_NAME, custom_windows=list(windows), pending_for="5m"
    )
    return calc, generated.windows


def window_label(hours: float) -> str:
    """Prometheus-style duration for a window, without truncating fractions of an hour."""
    if hours == int(hours):
        return f"{int(hours)}h"
    return f"{round(hours * 60)}m"


def window_counts(
    records: Sequence[RunRecord], config: AgentConfig, now: datetime, hours: float
) -> tuple[int, int]:
    """(eligible, bad) runs in the ``hours`` ending at ``now``, by audit timestamp.

    Audit timestamps have whole-second resolution and compute_slis excludes its end, so the end
    is extended by one second to include a run stamped exactly at ``now``.
    """
    end = now + timedelta(seconds=1)
    sli = compute_slis(records, config, end - timedelta(hours=hours), end)
    return sli.eligible, sli.eligible - sli.passed


def evaluate_budget(
    records: Sequence[RunRecord],
    config: AgentConfig,
    now: datetime,
    *,
    compliance_days: int = 30,
    windows: Sequence[tuple[str, float, float, float, str]] = DEFAULT_WINDOWS,
) -> BudgetStatus:
    """Evaluate one agent's pass-rate budget as of ``now`` (tz-aware)."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    calc, alert_windows = build_windows(config, compliance_days, windows)

    evals: list[WindowEval] = []
    for win in alert_windows:
        short_total, short_bad = window_counts(records, config, now, win.short_window_hours)
        long_total, long_bad = window_counts(records, config, now, win.long_window_hours)
        short_burn = calc.calculate_from_counts(short_bad, short_total).burn_rate
        long_burn = calc.calculate_from_counts(long_bad, long_total).burn_rate
        firing = (
            short_total >= config.min_events
            and short_bad >= MIN_BAD_EVENTS
            and short_burn > win.burn_rate
            and long_burn > win.burn_rate
        )
        evals.append(
            WindowEval(
                name=win.name,
                severity=win.severity.value,
                short_hours=win.short_window_hours,
                long_hours=win.long_window_hours,
                threshold=win.burn_rate,
                short_burn=short_burn,
                long_burn=long_burn,
                short_events=short_total,
                short_bad=short_bad,
                firing=firing,
            )
        )

    end = now + timedelta(seconds=1)
    full = compute_slis(records, config, end - timedelta(days=compliance_days), end)
    eligible, bad = full.eligible, full.eligible - full.passed
    consumed = (bad / eligible) / calc.error_budget if eligible else None
    exhausted = (
        consumed is not None
        and consumed >= 1.0
        and eligible >= config.min_events
        and bad >= MIN_BAD_EVENTS
    )
    breach = (
        full.gate_block_rate is not None
        and full.parseable >= config.min_events
        and full.gate_block_rate > config.gate_block_ceiling
    )

    if eligible == 0:
        state = BudgetState.NO_DATA
    elif exhausted:
        state = BudgetState.EXHAUSTED
    elif eligible < config.min_events:
        state = BudgetState.INSUFFICIENT_DATA
    elif any(w.firing for w in evals):
        state = BudgetState.BURNING
    else:
        state = BudgetState.OK

    return BudgetStatus(
        agent=config.name,
        now=now,
        state=state,
        compliance_days=compliance_days,
        error_budget=calc.error_budget,
        eligible=eligible,
        bad=bad,
        consumed_fraction=consumed,
        exhausted=exhausted,
        gate_block_breach=breach,
        windows=tuple(evals),
    )
