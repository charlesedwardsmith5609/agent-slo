"""Compute the four agent SLIs from audit records.

Pure functions over immutable inputs; no shared state. Ratios are None when the denominator is
zero: no data is not 100%.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from agent_slo.audit import Outcome, RunRecord
from agent_slo.config import AgentConfig

P95 = 0.95


@dataclass(frozen=True)
class AgentSLIs:
    agent: str
    start: datetime
    end: datetime
    eligible: int  # non-dry-run runs
    passed: int
    gate_blocked: int
    failed: int
    parseable: int  # passed + gate_blocked (the harness got valid JSON back)
    precall_cost_blocks: int  # failed runs refused by the worst-case cost ceiling
    latency_good: int
    latency_total: int
    cost_good: int
    cost_total: int
    pass_rate: float | None
    gate_block_rate: float | None
    latency_ratio: float | None
    cost_headroom_ratio: float | None
    p95_duration_s: float | None
    p95_cost_ratio: float | None  # cost / max_cost_usd
    mean_cost_ratio: float | None


def _ratio(good: int, total: int) -> float | None:
    return good / total if total else None


def nearest_rank(values: list[float], quantile: float) -> float | None:
    """Nearest-rank percentile; None for an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(quantile * len(ordered)))
    return ordered[rank - 1]


def _utc(name: str, moment: datetime) -> datetime:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return moment.astimezone(UTC)


def compute_slis(
    records: Iterable[RunRecord], config: AgentConfig, start: datetime, end: datetime
) -> AgentSLIs:
    """SLIs for one agent over the window [start, end)."""
    start_utc, end_utc = _utc("start", start), _utc("end", end)
    if start_utc >= end_utc:
        raise ValueError("start must be before end")

    passed = gate_blocked = failed = precall = 0
    durations: list[float] = []
    cost_ratios: list[float] = []
    latency_good = cost_good = 0
    for rec in records:
        if rec.agent_name != config.name or not start_utc <= rec.timestamp < end_utc:
            continue
        if rec.outcome is Outcome.DRY_RUN:
            continue
        if rec.outcome is Outcome.FAILED:
            failed += 1
            precall += rec.precall_cost_block
            continue
        # Successful runs (PASS or GATE_BLOCKED) carry real duration and cost.
        if rec.outcome is Outcome.PASS:
            passed += 1
        else:
            gate_blocked += 1
        durations.append(rec.duration_s)
        latency_good += rec.duration_s <= config.latency_threshold_s
        max_cost = rec.logged_max_cost_usd or config.max_cost_usd
        ratio = rec.cost_usd / max_cost
        cost_ratios.append(ratio)
        limit = config.cost_headroom_fraction
        cost_good += ratio <= limit or math.isclose(ratio, limit, rel_tol=1e-9)

    parseable = passed + gate_blocked
    eligible = parseable + failed
    return AgentSLIs(
        agent=config.name,
        start=start_utc,
        end=end_utc,
        eligible=eligible,
        passed=passed,
        gate_blocked=gate_blocked,
        failed=failed,
        parseable=parseable,
        precall_cost_blocks=precall,
        latency_good=latency_good,
        latency_total=parseable,
        cost_good=cost_good,
        cost_total=parseable,
        pass_rate=_ratio(passed, eligible),
        gate_block_rate=_ratio(gate_blocked, parseable),
        latency_ratio=_ratio(latency_good, parseable),
        cost_headroom_ratio=_ratio(cost_good, parseable),
        p95_duration_s=nearest_rank(durations, P95),
        p95_cost_ratio=nearest_rank(cost_ratios, P95),
        mean_cost_ratio=sum(cost_ratios) / len(cost_ratios) if cost_ratios else None,
    )


def unconfigured_agent_counts(
    records: Iterable[RunRecord], configs: Mapping[str, AgentConfig]
) -> dict[str, int]:
    """Runs per agent name that appears in the logs but not in the config."""
    counts: dict[str, int] = {}
    for rec in records:
        if rec.agent_name not in configs:
            counts[rec.agent_name] = counts.get(rec.agent_name, 0) + 1
    return counts
