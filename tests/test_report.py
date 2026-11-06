from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_slo.audit import Outcome, RunRecord
from agent_slo.config import AgentConfig
from agent_slo.report import POLICY_ACTION, render_report, safe_name

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
GOLDEN = Path(__file__).parent / "golden" / "report.md"


def cfg(name: str) -> AgentConfig:
    return AgentConfig(
        name=name,
        max_cost_usd=1.0,
        pass_target=0.95,
        latency_threshold_s=10.0,
        latency_target=0.9,
        cost_headroom_fraction=0.8,
        cost_headroom_target=0.9,
        gate_block_ceiling=0.1,
        min_events=5,
    )


def runs(
    agent: str,
    n: int,
    hours_ago: float,
    outcome: Outcome = Outcome.PASS,
    duration: float = 2.0,
    cost: float = 0.2,
    precall: bool = False,
) -> list[RunRecord]:
    at = NOW - timedelta(hours=hours_ago)
    return [
        RunRecord(agent, f"r{i}", at - timedelta(seconds=i), outcome, cost, duration, precall, None)
        for i in range(n)
    ]


CONFIGS = {n: cfg(n) for n in ("healthy", "burning", "quiet")}


def dataset() -> list[RunRecord]:
    return (
        runs("healthy", 40, 30)
        + runs("healthy", 2, 40, duration=15.0)
        + runs("burning", 12, 20)
        + runs("burning", 6, 10, Outcome.FAILED, precall=True)
        + runs("burning", 4, 12, Outcome.GATE_BLOCKED, cost=0.9)
        + runs("healthy", 3, 5, Outcome.DRY_RUN)
        + runs("ghost", 2, 3)
    )


def test_matches_golden() -> None:
    text = render_report(dataset(), {"too_long": 2}, CONFIGS, NOW)
    assert text == GOLDEN.read_text()


def test_policy_action_only_when_exhausted() -> None:
    text = render_report(dataset(), {}, CONFIGS, NOW)
    assert text.count(POLICY_ACTION) == 1
    burning = text.split("## burning")[1].split("## ")[0]
    healthy = text.split("## healthy")[1].split("## ")[0]
    assert POLICY_ACTION in burning and POLICY_ACTION not in healthy
    assert "- burning: EXHAUSTED (freeze auto-merge, require human review)" in text


def test_no_data_agent_and_empty_input() -> None:
    text = render_report([], {}, CONFIGS, NOW)
    assert "- quiet: NO_DATA" in text
    assert "verdict: no data" in text
    assert "Audit lines skipped: none" in text
    assert "No agents configured" in render_report([], {}, {}, NOW)


def test_skipped_lines_called_out() -> None:
    text = render_report([], {"invalid_json": 3, "too_long": 0}, CONFIGS, NOW)
    assert "skipped: 3 (invalid_json: 3)" in text and "bias every SLI" in text


def test_each_sli_reports_insufficient_data_on_its_own_denominator() -> None:
    # 10 eligible runs (meets min_events=5) but only 3 parseable.
    records = runs("healthy", 3, 2) + runs("healthy", 7, 2, Outcome.FAILED)
    text = render_report(records, {}, {"healthy": CONFIGS["healthy"]}, NOW)
    assert "Pass rate 30.0% (target 95.0%); verdict: MISS" in text
    assert text.count("insufficient data (n=3 < 5)") == 3  # gate-block, latency, cost


def test_caveats_always_present() -> None:
    text = render_report([], {}, CONFIGS, NOW)
    assert "## Caveats" in text and "under-reported" in text and "Dry runs are excluded" in text


def test_weekly_boundaries_and_utc_normalisation() -> None:
    cfg1 = {"healthy": CONFIGS["healthy"]}
    inside_now = runs("healthy", 1, 0)  # stamped exactly at now
    at_start = [
        RunRecord("healthy", "s", NOW - timedelta(days=7), Outcome.PASS, 0.1, 1.0, False, None)
    ]
    before_start = [
        RunRecord(
            "healthy",
            "b",
            NOW - timedelta(days=7, seconds=1),
            Outcome.PASS,
            0.1,
            1.0,
            False,
            None,
        )
    ]
    text = render_report(inside_now + at_start + before_start, {}, cfg1, NOW)
    assert (
        "- Runs: 2 eligible" in text
    )  # now and exactly-7-days-ago count; one second earlier does not
    plus5 = NOW.astimezone(timezone(timedelta(hours=5)))
    assert render_report(inside_now, {}, cfg1, plus5) == render_report(inside_now, {}, cfg1, NOW)
    assert "2026-11-20 12:00 UTC" in render_report([], {}, cfg1, plus5)


@pytest.mark.parametrize("bad", [0, -1, True, 1.5, "7"])
def test_day_parameters_validated(bad: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        render_report([], {}, CONFIGS, NOW, week_days=bad)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="positive integer"):
        render_report([], {}, CONFIGS, NOW, compliance_days=bad)  # type: ignore[arg-type]


def test_naive_now_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        render_report([], {}, CONFIGS, datetime(2026, 11, 20))  # noqa: DTZ001


def test_hostile_agent_names_are_visible_and_inert() -> None:
    hostile = "prod\n| <script>alert(1)</script> `x` \x1b[31m"
    records = runs(hostile, 1, 1) + runs("prod", 1, 1)
    text = render_report(records, {}, {}, NOW)
    assert "\x1b" not in text and "<script>" not in text
    assert "\\x0a" in text and "\\x1b" in text
    assert "\\<script\\>" in text and "\\|" in text and "\\`" in text
    assert "- `prod`: 1 runs" in text
    assert safe_name("prod\n") != safe_name("prod")


def test_long_names_are_clipped_with_a_stable_distinguishing_suffix() -> None:
    a, b = "x" * 100 + "a", "x" * 100 + "b"
    assert safe_name(a) != safe_name(b)
    assert safe_name(a) == safe_name(a) and len(safe_name(a)) < 80


def test_unconfigured_agents_capped() -> None:
    records: list[RunRecord] = []
    for n in range(25):
        records += runs(f"g{n:02d}", 1, 1)
    text = render_report(records, {}, {}, NOW)
    assert "- 5 more agents, 5 runs" in text


def test_report_explains_itself_to_a_new_reader() -> None:
    text = render_report([], {}, CONFIGS, NOW)
    legend = text.split("## How to read this report")[1].split("## Caveats")[0]
    for state in ("NO_DATA", "INSUFFICIENT_DATA", "OK", "BURNING", "EXHAUSTED"):
        assert state in legend
    assert "Burn rate" in legend and "Budget consumed" in legend and "bad" in legend
    assert text.index("## How to read this report") < text.index("## Caveats")
