import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_slo import metrics
from agent_slo.audit import Outcome, RunRecord
from agent_slo.budget import DEFAULT_WINDOWS, evaluate_budget
from agent_slo.config import AgentConfig
from agent_slo.metrics import render_metrics, write_textfile
from tests.promtext import get, parse

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
CFG = AgentConfig(
    name="a",
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
    n: int,
    hours_ago: float,
    outcome: Outcome = Outcome.PASS,
    agent: str = "a",
    duration: float = 1.0,
    cost: float = 0.1,
    precall: bool = False,
) -> list[RunRecord]:
    at = NOW - timedelta(hours=hours_ago)
    return [
        RunRecord(agent, f"r{i}", at - timedelta(seconds=i), outcome, cost, duration, precall, None)
        for i in range(n)
    ]


def render(records: list[RunRecord], skipped: dict[str, int] | None = None) -> str:
    return render_metrics(records, skipped or {}, {"a": CFG}, NOW)


def test_counters_and_zero_series() -> None:
    records = (
        runs(6, 2)
        + runs(2, 3, Outcome.GATE_BLOCKED, duration=40.0, cost=0.9)
        + runs(1, 4, Outcome.FAILED, precall=True)
        + runs(3, 1, Outcome.DRY_RUN)
    )
    samples, _, _ = parse(render(records))
    assert get(samples, "agent_runs_total", agent="a", outcome="pass") == 6
    assert get(samples, "agent_runs_total", agent="a", outcome="gate_blocked") == 2
    assert get(samples, "agent_runs_total", agent="a", outcome="failed") == 1
    assert not any(k[1] and ("outcome", "dry_run") in k[1] for k in samples)
    assert get(samples, "agent_run_latency_good_total", agent="a") == 6
    assert get(samples, "agent_run_cost_headroom_good_total", agent="a") == 6
    assert get(samples, "agent_run_precall_cost_blocks_total", agent="a") == 1
    empty, _, _ = parse(render([]))
    assert get(empty, "agent_runs_total", agent="a", outcome="failed") == 0


def test_type_and_help_once_per_family() -> None:
    _, types, helps = parse(render(runs(6, 2)))
    assert types["agent_runs_total"] == "counter"
    assert types["agent_slo_budget_exhausted"] == "gauge"
    assert all(count == 1 for count in helps.values())
    assert set(helps) == set(types)


def test_window_gauges_use_audit_timestamps_not_export_time() -> None:
    # Week-old failures must not appear inside the 6h window.
    records = runs(10, 100, Outcome.FAILED) + runs(6, 1)
    samples, _, _ = parse(render(records))
    assert get(samples, "agent_slo_window_eligible", agent="a", window="6h") == 6
    assert get(samples, "agent_slo_window_bad", agent="a", window="6h") == 0
    assert get(samples, "agent_slo_window_bad", agent="a", window="168h") == 10
    assert get(samples, "agent_slo_window_eligible", agent="a", window="720h") == 16


def test_state_is_one_hot_and_budget_gauges() -> None:
    records = runs(20, 100) + runs(4, 100, Outcome.FAILED)
    samples, _, _ = parse(render(records))
    states = {k[1][1][1]: v for k, v in samples.items() if k[0] == "agent_slo_state"}
    assert sum(states.values()) == 1 and states["exhausted"] == 1
    assert get(samples, "agent_slo_budget_exhausted", agent="a") == 1
    assert get(samples, "agent_slo_budget_consumed_ratio", agent="a") == pytest.approx(
        (4 / 24) / 0.05
    )


def test_no_data_omits_consumed_and_sli_ratios() -> None:
    samples, _, _ = parse(render([]))
    assert get(samples, "agent_slo_budget_consumed_ratio", agent="a") is None
    assert get(samples, "agent_slo_sli_ratio", agent="a", sli="pass_rate") is None
    assert get(samples, "agent_slo_sli_target", agent="a", sli="pass_rate") == 0.95
    assert get(samples, "agent_slo_state", agent="a", state="no_data") == 1


def test_sli_ratio_omitted_below_min_events_then_present() -> None:
    few, _, _ = parse(render(runs(4, 2)))
    assert get(few, "agent_slo_sli_ratio", agent="a", sli="pass_rate") is None
    enough, _, _ = parse(render(runs(4, 2) + runs(1, 2, Outcome.FAILED)))
    assert get(enough, "agent_slo_sli_ratio", agent="a", sli="pass_rate") == 0.8
    # latency/cost use parseable runs (4 here), still below min_events: no verdict.
    assert get(enough, "agent_slo_sli_ratio", agent="a", sli="latency") is None
    ok, _, _ = parse(render(runs(5, 2)))
    assert get(ok, "agent_slo_sli_ratio", agent="a", sli="latency") == 1.0


def test_skipped_lines_all_reasons_present() -> None:
    samples, _, _ = parse(render([], {"too_long": 3}))
    assert get(samples, "agent_slo_audit_lines_skipped_total", reason="too_long") == 3
    assert get(samples, "agent_slo_audit_lines_skipped_total", reason="bad_encoding") == 0


def test_unconfigured_agents_only_aggregated_never_labels() -> None:
    nasty = 'ev"il\\name\nx'
    records = runs(2, 1, agent=nasty) + runs(1, 1, agent="__other__")
    for n in range(25):
        records += runs(1, 1, agent=f"extra-{n:02d}")
    text = render(records)
    assert "ev" not in text.replace("event", "") or 'agent="ev' not in text
    assert "extra-" not in text and "__other__" not in text
    samples, _, _ = parse(text)
    assert get(samples, "agent_slo_unconfigured_runs_total") == 28
    assert get(samples, "agent_slo_unconfigured_agents") == 27
    assert not any(dict(k[1]).get("agent") not in (None, "a") for k in samples)


def test_escape_label() -> None:
    assert metrics.escape_label('a"b\\c\nd') == 'a\\"b\\\\c\\nd'


def test_many_agents_many_records_stay_linear() -> None:
    configs = {
        f"agent-{n}": CFG.__class__(**{**CFG.__dict__, "name": f"agent-{n}"}) for n in range(50)
    }
    records: list[RunRecord] = []
    for name in configs:
        records += runs(200, 2, agent=name)
    text = render_metrics(records, {}, configs, NOW)
    samples, _, _ = parse(text)
    assert get(samples, "agent_runs_total", agent="agent-7", outcome="pass") == 200


def test_naive_now_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        render_metrics([], {}, {"a": CFG}, datetime(2026, 11, 20))  # noqa: DTZ001


def test_export_timestamp() -> None:
    samples, _, _ = parse(render([]))
    assert get(samples, "agent_slo_export_timestamp_seconds") == int(NOW.timestamp())


@pytest.mark.parametrize(
    "records",
    [
        runs(5, 2) + runs(5, 3, Outcome.FAILED) + runs(7, 10) + runs(3, 12, Outcome.FAILED),
        runs(10, 2) + runs(10, 15, Outcome.FAILED),
        runs(4, 1, Outcome.FAILED),
        runs(100, 30) + runs(20, 2),
    ],
)
def test_exported_gauges_reproduce_evaluate_budget_firing(records: list[RunRecord]) -> None:
    """Apply the alert expression's arithmetic to the exported gauges; it must match Python."""
    samples, _, _ = parse(render(records))
    status = evaluate_budget(records, CFG, NOW)
    for name, _pct, short_h, long_h, _sev in DEFAULT_WINDOWS:

        def burn(h: float) -> float:
            label = f"{int(h)}h"
            elig = get(samples, "agent_slo_window_eligible", agent="a", window=label) or 0
            bad = get(samples, "agent_slo_window_bad", agent="a", window=label) or 0
            return (bad / elig) / 0.05 if elig else float("nan")

        win = next(w for w in status.windows if w.name == name)
        short_label = f"{int(short_h)}h"
        fires = (
            burn(short_h) > win.threshold
            and burn(long_h) > win.threshold
            and (get(samples, "agent_slo_window_eligible", agent="a", window=short_label) or 0) >= 5
            and (get(samples, "agent_slo_window_bad", agent="a", window=short_label) or 0) >= 2
        )
        assert fires == win.firing, name


def test_write_textfile_atomic_and_readable(tmp_path: Path) -> None:
    target = tmp_path / "agent_slo.prom"
    write_textfile(target, "one\n")
    write_textfile(target, "two\n")
    assert target.read_text() == "two\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    assert [p.name for p in tmp_path.iterdir()] == ["agent_slo.prom"]


def test_write_textfile_failure_keeps_old_file_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "agent_slo.prom"
    write_textfile(target, "old\n")

    def boom(src: str, dst: object) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk gone"):
        write_textfile(target, "new\n")
    assert target.read_text() == "old\n"
    assert [p.name for p in tmp_path.iterdir()] == ["agent_slo.prom"]


def test_write_textfile_missing_directory_raises(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        write_textfile(tmp_path / "nope" / "x.prom", "x")
    assert list(tmp_path.iterdir()) == []


def test_write_textfile_write_failure_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(fd: int) -> None:
        raise OSError("fsync failed")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(OSError, match="fsync"):
        write_textfile(tmp_path / "x.prom", "x")
    assert list(tmp_path.iterdir()) == []
