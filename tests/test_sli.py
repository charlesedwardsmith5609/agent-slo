from datetime import UTC, datetime, timedelta, timezone

import pytest

from agent_slo.audit import Outcome, RunRecord
from agent_slo.config import AgentConfig
from agent_slo.sli import compute_slis, nearest_rank, unconfigured_agent_counts

T0 = datetime(2026, 11, 1, tzinfo=UTC)
T1 = T0 + timedelta(days=7)
CFG = AgentConfig(
    name="a",
    max_cost_usd=1.0,
    pass_target=0.95,
    latency_threshold_s=10.0,
    latency_target=0.95,
    cost_headroom_fraction=0.8,
    cost_headroom_target=0.95,
    gate_block_ceiling=0.1,
)


def rec(
    outcome: Outcome = Outcome.PASS,
    *,
    agent: str = "a",
    at: datetime = T0 + timedelta(hours=1),
    cost: float = 0.1,
    duration: float = 1.0,
    precall: bool = False,
    logged_max: float | None = None,
) -> RunRecord:
    return RunRecord(agent, "r", at, outcome, cost, duration, precall, logged_max)


def test_counts_and_ratios() -> None:
    records = [
        rec(),
        rec(duration=20.0),  # slow
        rec(cost=0.9),  # over 80% of max
        rec(Outcome.GATE_BLOCKED, cost=0.5),
        rec(Outcome.FAILED, cost=0.0, duration=0.0, precall=True),
        rec(Outcome.FAILED, cost=0.0, duration=0.0),
        rec(Outcome.DRY_RUN),
    ]
    s = compute_slis(records, CFG, T0, T1)
    assert (s.eligible, s.passed, s.gate_blocked, s.failed, s.parseable) == (6, 3, 1, 2, 4)
    assert s.precall_cost_blocks == 1
    assert s.pass_rate == 3 / 6
    assert s.gate_block_rate == 1 / 4
    assert (s.latency_good, s.latency_total) == (3, 4)
    assert s.latency_ratio == 3 / 4
    assert (s.cost_good, s.cost_total) == (3, 4)
    assert s.cost_headroom_ratio == 3 / 4
    assert s.p95_duration_s == 20.0
    assert s.p95_cost_ratio == 0.9
    assert s.mean_cost_ratio == pytest.approx((0.1 + 0.1 + 0.9 + 0.5) / 4)


def test_dry_runs_only_means_no_data() -> None:
    s = compute_slis([rec(Outcome.DRY_RUN)], CFG, T0, T1)
    assert s.eligible == 0
    assert s.pass_rate is None and s.gate_block_rate is None
    assert s.latency_ratio is None and s.cost_headroom_ratio is None
    assert s.p95_duration_s is None and s.mean_cost_ratio is None


def test_failures_only_has_pass_rate_zero_but_no_latency_data() -> None:
    s = compute_slis([rec(Outcome.FAILED, cost=0.0)], CFG, T0, T1)
    assert s.pass_rate == 0.0
    assert s.gate_block_rate is None and s.latency_ratio is None


def test_window_start_inclusive_end_exclusive() -> None:
    records = [
        rec(at=T0),
        rec(at=T1),  # excluded
        rec(at=T0 - timedelta(seconds=1)),  # excluded
        rec(at=T1 - timedelta(seconds=1)),
    ]
    assert compute_slis(records, CFG, T0, T1).eligible == 2


def test_cost_boundary_is_good() -> None:
    s = compute_slis([rec(cost=0.8)], CFG, T0, T1)
    assert s.cost_good == 1


def test_logged_max_overrides_config() -> None:
    s = compute_slis([rec(cost=0.5, logged_max=0.5)], CFG, T0, T1)
    assert s.p95_cost_ratio == 1.0
    assert s.cost_good == 0


def test_only_requested_agent_counted_and_unconfigured_reported() -> None:
    records = [rec(), rec(agent="other"), rec(agent="other"), rec(agent="ghost")]
    assert compute_slis(records, CFG, T0, T1).eligible == 1
    assert unconfigured_agent_counts(records, {"a": CFG}) == {"other": 2, "ghost": 1}


def test_window_validation_and_offset_normalization() -> None:
    naive = datetime(2026, 11, 1)  # noqa: DTZ001
    with pytest.raises(ValueError, match="timezone-aware"):
        compute_slis([], CFG, naive, T1)
    with pytest.raises(ValueError, match="timezone-aware"):
        compute_slis([], CFG, T0, naive)
    with pytest.raises(ValueError, match="before"):
        compute_slis([], CFG, T1, T0)
    with pytest.raises(ValueError, match="before"):
        compute_slis([], CFG, T0, T0)
    plus5 = timezone(timedelta(hours=5))
    s = compute_slis(
        [rec(at=T0 + timedelta(hours=1))], CFG, T0.astimezone(plus5), T1.astimezone(plus5)
    )
    assert s.eligible == 1 and s.start == T0 and s.end == T1


def test_nearest_rank() -> None:
    values = [float(n) for n in range(1, 21)]
    assert nearest_rank(values, 0.95) == 19.0
    assert nearest_rank([5.0], 0.95) == 5.0
    assert nearest_rank([], 0.95) is None
