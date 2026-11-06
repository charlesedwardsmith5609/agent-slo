import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_slo.audit import DEFAULT_MAX_RECORDS, Outcome, read_audit
from agent_slo.budget import evaluate_budget
from agent_slo.config import AgentConfig
from agent_slo.generate import generate_records, write_audit

END = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
START = END - timedelta(days=30)
FIXTURE = Path(__file__).parent / "fixtures" / "harness_audit_sample.jsonl"


def cfg(name: str) -> AgentConfig:
    return AgentConfig(
        name=name,
        max_cost_usd=0.5,
        pass_target=0.95,
        latency_threshold_s=30.0,
        latency_target=0.95,
        cost_headroom_fraction=0.8,
        cost_headroom_target=0.95,
        gate_block_ceiling=0.1,
        min_events=5,
    )


CONFIGS = {n: cfg(n) for n in ("healthy-a", "degrading-b", "burst-c")}
SCENARIOS = {"degrading-b": "degrading", "burst-c": "burst"}


def gen(**over: object) -> list[dict[str, object]]:
    args: dict[str, object] = {"seed": 7, "runs_per_day": 40, "scenarios": SCENARIOS}
    args.update(over)
    return generate_records(CONFIGS, START, END, **args)  # type: ignore[arg-type]


def stamp(rec: dict[str, object]) -> datetime:
    return datetime.strptime(str(rec["timestamp"]), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def failed(rec: dict[str, object]) -> bool:
    return rec["success"] is False


def test_deterministic_and_seed_sensitive() -> None:
    assert gen() == gen()
    assert gen() != gen(seed=8)


def test_count_and_bounds_and_order() -> None:
    records = gen()
    assert len(records) == 3 * 30 * 40
    stamps = [stamp(r) for r in records]
    assert stamps == sorted(stamps)
    assert all(START <= s < END for s in stamps)
    assert all(re.fullmatch(r"[0-9a-f]{8}", str(r["run_id"])) for r in records)
    assert len(gen(runs_per_day=10)) == 3 * 30 * 10


def test_read_audit_accepts_everything(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    write_audit(path, gen())
    result = read_audit(path)
    assert len(result.records) == 3 * 30 * 40 and dict(result.skipped) == {}


def test_full_field_and_type_contract_against_real_harness_lines() -> None:
    samples = [json.loads(line) for line in FIXTURE.read_text().splitlines()]
    by_class = {
        "failed": samples[0],
        "dry": samples[1],
        "pass": samples[2],
        "conf_block": samples[3],
        "cost_block": samples[4],
        "precall": samples[5],
    }
    seen: set[str] = set()
    for rec in gen(runs_per_day=200):
        reason = str(rec["gate_reason"])
        if reason.startswith("Dry run"):
            kind = "dry"
        elif failed(rec):
            kind = "precall" if "Worst-case cost" in reason else "failed"
        elif rec["gate_passed"]:
            kind = "pass"
        elif reason.startswith("Confidence"):
            kind = "conf_block"
        else:
            assert reason.startswith("Cost $"), reason
            kind = "cost_block"
        seen.add(kind)
        ref = by_class[kind]
        assert list(rec) == list(ref), kind  # same keys in the same order
        for key, value in ref.items():
            assert type(rec[key]) is type(value), (kind, key)
        if kind == "pass":
            assert rec["gate_reason"] == "" and rec["success"] is True
        if kind in ("failed", "precall"):
            assert rec["cost_usd"] == 0.0 and rec["data"] == {} and rec["confidence"] == 0.0
            assert reason == f"Agent execution failed: {rec['reasoning']}"
    assert seen == set(by_class)


def test_outcomes_classify_back_as_intended(tmp_path: Path) -> None:
    path = tmp_path / "a.jsonl"
    write_audit(path, gen(runs_per_day=200))
    outcomes = {r.outcome for r in read_audit(path).records}
    assert outcomes == {Outcome.PASS, Outcome.GATE_BLOCKED, Outcome.FAILED, Outcome.DRY_RUN}


def test_healthy_scenario_has_every_event_kind_and_good_pass_rate() -> None:
    records = [r for r in gen(runs_per_day=200) if r["agent_name"] == "healthy-a"]
    reasons = [str(r["gate_reason"]) for r in records]
    assert any(x.startswith("Dry run") for x in reasons)
    assert any(x.startswith("Confidence") for x in reasons)
    assert any("Worst-case cost" in x for x in reasons)
    eligible = [r for r in records if not str(r["gate_reason"]).startswith("Dry run")]
    passed = [r for r in eligible if r["gate_passed"]]
    assert len(passed) / len(eligible) > 0.9


def failure_rate(records: list[dict[str, object]], lo: datetime, hi: datetime) -> float:
    window = [r for r in records if lo <= stamp(r) < hi]
    window = [r for r in window if not str(r["gate_reason"]).startswith("Dry run")]
    return sum(failed(r) for r in window) / len(window)


def test_degrading_ramps_up() -> None:
    records = [r for r in gen(runs_per_day=200) if r["agent_name"] == "degrading-b"]
    third = (END - START) / 3
    assert failure_rate(records, START, START + third) < 0.1
    assert failure_rate(records, END - third, END) > 0.15


def test_burst_confined_to_final_hours() -> None:
    records = [r for r in gen(runs_per_day=400) if r["agent_name"] == "burst-c"]
    cut = END - timedelta(hours=12)
    assert failure_rate(records, START, cut) < 0.06
    assert failure_rate(records, cut, END) > 0.7


def test_scenarios_drive_budget_state() -> None:
    from agent_slo.audit import classify
    from agent_slo.budget import BudgetState

    raw = gen(runs_per_day=100)
    recs = [classify(r) for r in raw]
    states = {n: evaluate_budget(recs, CONFIGS[n], END).state for n in CONFIGS}
    assert states["healthy-a"] is BudgetState.OK
    assert states["degrading-b"] is BudgetState.EXHAUSTED
    status = evaluate_budget(recs, CONFIGS["burst-c"], END)
    assert next(w for w in status.windows if w.name == "Fast Burn").firing


@pytest.mark.parametrize(
    "over",
    [
        {"scenarios": {"nope": "healthy"}},
        {"scenarios": {"healthy-a": "chaos"}},
        {"runs_per_day": 0},
        {"runs_per_day": True},
        {"runs_per_day": 1.5},
        {"seed": True},
    ],
)
def test_bad_arguments(over: dict[str, object]) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        gen(**over)


def test_time_bounds_validated() -> None:
    naive = datetime(2026, 11, 1)  # noqa: DTZ001
    with pytest.raises(ValueError, match="timezone-aware"):
        generate_records(CONFIGS, naive, END, seed=1)
    with pytest.raises(ValueError, match="before"):
        generate_records(CONFIGS, END, END, seed=1)
    with pytest.raises(ValueError, match="before"):
        generate_records(CONFIGS, END, START, seed=1)


def test_record_limit_enforced_before_allocation() -> None:
    one = {"a": cfg("a")}
    # exactly at the limit is accepted lazily-checked by arithmetic: 1 agent x 1 day x limit runs
    with pytest.raises(ValueError, match="exceeds the limit"):
        generate_records(
            one, END - timedelta(days=1), END, seed=1, runs_per_day=DEFAULT_MAX_RECORDS + 1
        )
    with pytest.raises(ValueError, match="exceeds the limit"):
        generate_records(
            {n: cfg(n) for n in "abc"},
            END - timedelta(days=1),
            END,
            seed=1,
            runs_per_day=DEFAULT_MAX_RECORDS // 3 + 1,
        )


def test_write_audit_failure_keeps_old_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    path = tmp_path / "audit.jsonl"
    write_audit(path, gen(runs_per_day=1)[:3])
    before = path.read_text()

    def boom(src: str, dst: object) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk gone"):
        write_audit(path, gen(runs_per_day=1)[:5])
    assert path.read_text() == before
    assert [p.name for p in tmp_path.iterdir()] == ["audit.jsonl"]
