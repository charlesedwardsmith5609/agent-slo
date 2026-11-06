"""Synthetic audit data for demos and tests, in exactly the shape agent-harness writes.

Deterministic: one ``random.Random(seed)`` is created per call and owned by it; no global state.
Synthetic data only, never security-sensitive randomness.
"""

from __future__ import annotations

import json
import random
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_slo.audit import DEFAULT_MAX_RECORDS
from agent_slo.config import AgentConfig
from agent_slo.files import write_atomic

SCENARIOS = ("healthy", "degrading", "burst")
BURST_HOURS = 12
MODEL = "claude-sonnet-5-5"
MIN_CONFIDENCE = 0.7  # the gate threshold quoted in synthetic gate_reason text
_BASE_FAILURE = 0.01
_DRY_RUN = 0.01
_CONFIDENCE_BLOCK = 0.015
_COST_BLOCK = 0.004
_PRECALL_SHARE = 0.25  # share of failures that are pre-call cost refusals
_EXEC_ERRORS = (
    "Error code: 529 - overloaded_error",
    "Expecting value: line 1 column 1 (char 0)",
    "Connection error.",
)


def _failure_probability(scenario: str, position: float, ts: datetime, end: datetime) -> float:
    if scenario == "degrading":
        return _BASE_FAILURE + 0.24 * position
    if scenario == "burst" and ts >= end - timedelta(hours=BURST_HOURS):
        return 0.9
    return _BASE_FAILURE


def _record(rng: random.Random, cfg: AgentConfig, ts: datetime, fail_p: float) -> dict[str, object]:
    roll = rng.random()
    base: dict[str, object] = {"agent_name": cfg.name, "run_id": f"{rng.getrandbits(32):08x}"}
    stamp = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
    tail: dict[str, object] = {"model": MODEL}

    def build(
        *,
        success: bool,
        data: dict[str, object],
        confidence: float,
        reasoning: str,
        cost: float,
        duration: float,
        tokens: tuple[int, int],
        gate_passed: bool,
        gate_reason: str,
    ) -> dict[str, object]:
        return {
            **base,
            "success": success,
            "data": data,
            "confidence": confidence,
            "reasoning": reasoning,
            "cost_usd": cost,
            "duration_s": duration,
            "input_tokens": tokens[0],
            "output_tokens": tokens[1],
            **tail,
            "gate_passed": gate_passed,
            "gate_reason": gate_reason,
            "timestamp": stamp,
        }

    if roll < _DRY_RUN:
        return build(
            success=True,
            data={
                "confidence": 0.0,
                "reasoning": "DRY_RUN: no API call was made",
                "findings": [],
                "recommendation": "DRY_RUN",
            },
            confidence=0.0,
            reasoning="DRY_RUN: no API call was made",
            cost=0.0,
            duration=round(rng.uniform(1e-5, 1e-3), 6),
            tokens=(0, 0),
            gate_passed=False,
            gate_reason="Dry run: no API call was made",
        )
    roll -= _DRY_RUN
    if roll < fail_p:
        if rng.random() < _PRECALL_SHARE:
            error = (
                f"Worst-case cost ${cfg.max_cost_usd * rng.uniform(1.1, 2.0):.4f} "
                f"exceeds max ${cfg.max_cost_usd:.2f}; call not made"
            )
            duration = round(rng.uniform(1e-4, 5e-3), 6)
        else:
            error = rng.choice(_EXEC_ERRORS)
            duration = round(rng.uniform(0.05, 3.0), 4)
        return build(
            success=False,
            data={},
            confidence=0.0,
            reasoning=error,
            cost=0.0,
            duration=duration,
            tokens=(0, 0),
            gate_passed=False,
            gate_reason=f"Agent execution failed: {error}",
        )
    roll -= fail_p

    duration = round(max(0.5, rng.gauss(8.0, 3.0)) * (3.0 if rng.random() < 0.04 else 1.0), 4)
    tokens = (rng.randint(1000, 9000), rng.randint(200, 1500))
    if roll < _CONFIDENCE_BLOCK:
        conf = round(rng.uniform(0.3, 0.65), 2)
        cost = round(cfg.max_cost_usd * min(0.75, max(0.05, rng.gauss(0.4, 0.15))), 6)
        return build(
            success=True,
            data={"confidence": conf, "reasoning": "synthetic run", "findings": []},
            confidence=conf,
            reasoning="synthetic run",
            cost=cost,
            duration=duration,
            tokens=tokens,
            gate_passed=False,
            gate_reason=f"Confidence {conf:.2f} below threshold {MIN_CONFIDENCE:.2f}",
        )
    roll -= _CONFIDENCE_BLOCK
    conf = round(rng.uniform(0.75, 0.98), 2)
    if roll < _COST_BLOCK:
        cost = round(cfg.max_cost_usd * rng.uniform(1.01, 1.3), 6)
        return build(
            success=True,
            data={"confidence": conf, "reasoning": "synthetic run", "findings": []},
            confidence=conf,
            reasoning="synthetic run",
            cost=cost,
            duration=duration,
            tokens=tokens,
            gate_passed=False,
            gate_reason=f"Cost ${cost:.4f} exceeds max ${cfg.max_cost_usd:.2f}",
        )
    cost = round(cfg.max_cost_usd * min(0.75, max(0.05, rng.gauss(0.4, 0.15))), 6)
    return build(
        success=True,
        data={
            "confidence": conf,
            "reasoning": "synthetic run",
            "findings": [],
            "recommendation": "OK",
        },
        confidence=conf,
        reasoning="synthetic run",
        cost=cost,
        duration=duration,
        tokens=tokens,
        gate_passed=True,
        gate_reason="",
    )


def generate_records(
    configs: Mapping[str, AgentConfig],
    start: datetime,
    end: datetime,
    *,
    seed: int,
    runs_per_day: int = 40,
    scenarios: Mapping[str, str] | None = None,
) -> list[dict[str, object]]:
    """Synthetic audit records for every configured agent in [start, end), sorted by time."""
    scenarios = dict(scenarios or {})
    for name, scenario in scenarios.items():
        if name not in configs:
            raise ValueError(f"scenario given for unknown agent {name!r}")
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown scenario {scenario!r}; choose from {', '.join(SCENARIOS)}")
    for label, moment in (("start", start), ("end", end)):
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError(f"{label} must be timezone-aware")
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    if start >= end:
        raise ValueError("start must be before end")
    if isinstance(runs_per_day, bool) or not isinstance(runs_per_day, int) or runs_per_day <= 0:
        raise ValueError("runs_per_day must be a positive integer")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    span_s = int((end - start).total_seconds())
    if span_s < 1:
        raise ValueError("the span must be at least one second")
    per_agent = max(1, round(span_s / 86400 * runs_per_day))
    if per_agent * len(configs) > DEFAULT_MAX_RECORDS:
        raise ValueError(
            f"{per_agent * len(configs)} records exceeds the limit of {DEFAULT_MAX_RECORDS}"
        )

    rng = random.Random(seed)  # noqa: S311  # synthetic data, not security-sensitive
    out: list[tuple[datetime, str, dict[str, object]]] = []
    for name in sorted(configs):
        cfg, scenario = configs[name], scenarios.get(name, "healthy")
        for offset in sorted(rng.randrange(span_s) for _ in range(per_agent)):
            ts = start + timedelta(seconds=offset)
            fail_p = _failure_probability(scenario, offset / span_s, ts, end)
            out.append((ts, name, _record(rng, cfg, ts, fail_p)))
    out.sort(key=lambda item: (item[0], item[1]))
    return [rec for _, _, rec in out]


def write_audit(path: str | Path, records: list[dict[str, object]]) -> None:
    """Write records as JSONL, atomically."""
    write_atomic(path, "".join(json.dumps(rec) + "\n" for rec in records))
