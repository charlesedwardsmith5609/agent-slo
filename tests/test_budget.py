from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from slo_toolkit import BurnRateCalculator

from agent_slo.audit import Outcome, RunRecord
from agent_slo.budget import BudgetState, evaluate_budget
from agent_slo.config import AgentConfig

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
CFG = AgentConfig(
    name="a",
    max_cost_usd=1.0,
    pass_target=0.95,
    latency_threshold_s=30.0,
    latency_target=0.95,
    cost_headroom_fraction=0.8,
    cost_headroom_target=0.95,
    gate_block_ceiling=0.1,
    min_events=5,
)


def runs(
    n: int, hours_ago: float, outcome: Outcome = Outcome.PASS, agent: str = "a"
) -> list[RunRecord]:
    at = NOW - timedelta(hours=hours_ago)
    return [
        RunRecord(agent, f"r{n}-{i}", at - timedelta(seconds=i), outcome, 0.1, 1.0, False, None)
        for i in range(n)
    ]


def window(status, name: str):  # type: ignore[no-untyped-def]
    return next(w for w in status.windows if w.name == name)


def test_thresholds_match_slo_toolkit() -> None:
    status = evaluate_budget(runs(10, 1), CFG, NOW)
    calc = BurnRateCalculator(slo_target=0.95, compliance_window_days=30)
    assert calc.error_budget == 0.05
    got = {w.name: w.threshold for w in status.windows}
    assert got == pytest.approx({"Fast Burn": 6.0, "Slow Burn": 3.0, "Trend": 1.0})
    assert [w.severity for w in status.windows] == ["page", "ticket", "info"]


def test_healthy_history_is_ok() -> None:
    records = runs(100, 30) + runs(20, 2)
    status = evaluate_budget(records, CFG, NOW)
    assert status.state is BudgetState.OK
    assert not any(w.firing for w in status.windows)
    assert status.consumed_fraction == 0.0 and not status.exhausted


def test_failure_burst_fires_fast_burn() -> None:
    records = runs(5, 2) + runs(5, 3, Outcome.FAILED) + runs(7, 10) + runs(3, 12, Outcome.FAILED)
    status = evaluate_budget(records, CFG, NOW)
    fast = window(status, "Fast Burn")
    assert fast.firing and fast.short_burn > 6 and fast.long_burn > 6
    assert status.state in (BudgetState.BURNING, BudgetState.EXHAUSTED)


def test_short_window_below_min_events_does_not_fire() -> None:
    status = evaluate_budget(runs(4, 1, Outcome.FAILED), CFG, NOW)
    assert not any(w.firing for w in status.windows)
    assert status.state is BudgetState.INSUFFICIENT_DATA


def test_old_failures_alone_do_not_fire_fast_burn() -> None:
    records = runs(10, 2) + runs(10, 15, Outcome.FAILED)
    fast = window(evaluate_budget(records, CFG, NOW), "Fast Burn")
    assert fast.short_burn == 0.0 and fast.long_burn > 6
    assert not fast.firing


def test_single_failure_never_fires_or_exhausts() -> None:
    cfg = replace(CFG, pass_target=0.99)
    status = evaluate_budget(runs(4, 1) + runs(1, 1, Outcome.FAILED), cfg, NOW)
    assert window(status, "Fast Burn").short_burn == pytest.approx(20.0)
    assert not any(w.firing for w in status.windows)
    assert not status.exhausted and status.state is BudgetState.OK


@pytest.mark.parametrize(("bad", "exhausted"), [(1, False), (2, True), (4, True)])
def test_exhaustion_boundary(bad: int, exhausted: bool) -> None:
    # 40 eligible runs against a 5% budget: 2 bad is exactly 100% consumed.
    records = runs(40 - bad, 100) + runs(bad, 100, Outcome.FAILED)
    status = evaluate_budget(records, CFG, NOW)
    assert status.exhausted is exhausted
    assert (status.state is BudgetState.EXHAUSTED) is exhausted
    assert status.consumed_fraction == pytest.approx(bad / 40 / 0.05)


def test_consumption_above_one_is_kept_raw() -> None:
    status = evaluate_budget(runs(1, 100) + runs(4, 100, Outcome.FAILED), CFG, NOW)
    assert status.consumed_fraction == pytest.approx(16.0)
    assert status.exhausted


def test_gate_blocked_consumes_budget() -> None:
    status = evaluate_budget(runs(10, 100, Outcome.GATE_BLOCKED), CFG, NOW)
    assert status.bad == 10 and status.eligible == 10
    assert status.exhausted and status.state is BudgetState.EXHAUSTED
    assert status.gate_block_breach


def test_no_data_and_dry_runs_ignored() -> None:
    assert evaluate_budget([], CFG, NOW).state is BudgetState.NO_DATA
    status = evaluate_budget(runs(20, 1, Outcome.DRY_RUN), CFG, NOW)
    assert status.state is BudgetState.NO_DATA and status.consumed_fraction is None
    assert not any(w.firing for w in status.windows)


def test_other_agents_ignored() -> None:
    assert (
        evaluate_budget(runs(20, 1, Outcome.FAILED, agent="x"), CFG, NOW).state
        is BudgetState.NO_DATA
    )


def test_gate_block_breach_needs_min_events_and_rate_over_ceiling() -> None:
    few = evaluate_budget(runs(3, 100, Outcome.GATE_BLOCKED), CFG, NOW)
    assert not few.gate_block_breach
    ok = evaluate_budget(runs(19, 100) + runs(1, 100, Outcome.GATE_BLOCKED), CFG, NOW)
    assert not ok.gate_block_breach  # 5% <= 10%
    over = evaluate_budget(runs(8, 100) + runs(2, 100, Outcome.GATE_BLOCKED), CFG, NOW)
    assert over.gate_block_breach  # 20% > 10%


def test_run_stamped_exactly_now_is_included() -> None:
    assert evaluate_budget(runs(1, 0), CFG, NOW).eligible == 1


def test_naive_now_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate_budget([], CFG, datetime(2026, 11, 20))  # noqa: DTZ001
