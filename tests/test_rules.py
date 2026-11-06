import json
import re
from pathlib import Path

from agent_slo.budget import DEFAULT_WINDOWS, MIN_BAD_EVENTS, evaluate_budget
from agent_slo.config import AgentConfig
from agent_slo.rules import render_rules

GOLDEN = Path(__file__).parent / "golden" / "rules.yml"


def cfg(name: str, **over: float) -> AgentConfig:
    base = {
        "name": name,
        "max_cost_usd": 1.0,
        "pass_target": 0.95,
        "latency_threshold_s": 30.0,
        "latency_target": 0.95,
        "cost_headroom_fraction": 0.8,
        "cost_headroom_target": 0.95,
        "gate_block_ceiling": 0.1,
        "min_events": 5,
    }
    base.update(over)
    return AgentConfig(**base)  # type: ignore[arg-type]


CONFIGS = {"pr-reviewer": cfg("pr-reviewer"), "deploy-risk": cfg("deploy-risk", pass_target=0.9)}


def test_matches_golden() -> None:
    # To update after an intended change: regenerate, review the git diff, commit.
    assert render_rules(CONFIGS) == GOLDEN.read_text()


def test_every_quoted_scalar_is_valid_json_string() -> None:
    text = render_rules(CONFIGS)
    for line in text.splitlines():
        match = re.match(r"^\s*(?:- )?[a-z_]+: (\".*\")$", line)
        if match:
            assert isinstance(json.loads(match.group(1)), str)
        else:
            assert line.startswith("#") or line.rstrip().endswith(":") or not line.strip(), line


def test_every_burn_alert_has_both_windows_and_both_guards() -> None:
    text = render_rules({"pr-reviewer": CONFIGS["pr-reviewer"]})
    alerts = [
        json.loads(m)
        for m in re.findall(r'alert: "AgentSLO(?:FastBurn|SlowBurn|Trend)"\n\s+expr: (".*")', text)
    ]
    assert len(alerts) == 3
    for expr in alerts:
        assert expr.count("agent_slo:burn_rate:ratio_") == 2
        assert "agent_slo_window_eligible" in expr and ">= 5" in expr
        assert "agent_slo_window_bad" in expr and f">= {MIN_BAD_EVENTS}" in expr
        assert "increase(" not in expr and "rate(" not in expr


def test_thresholds_match_evaluate_budget() -> None:
    from datetime import UTC, datetime

    status = evaluate_budget([], CONFIGS["deploy-risk"], datetime(2026, 11, 20, tzinfo=UTC))
    text = render_rules({"deploy-risk": CONFIGS["deploy-risk"]})
    for win in status.windows:
        assert f"> {round(win.threshold, 6)!r}" in text
    assert '/ 0.1"' in text  # error budget of a 90% target


def test_non_integer_hours_render_as_minutes() -> None:
    windows = (("Odd", 5.0, 1.5, 6.0, "page"),)
    text = render_rules({"pr-reviewer": CONFIGS["pr-reviewer"]}, windows=windows)
    assert "ratio_90m" in text and 'window=\\"90m\\"' in text
    assert "ratio_1h" not in text


def test_stale_alert_covers_missing_and_old_files() -> None:
    text = render_rules(CONFIGS)
    assert "time() - agent_slo_export_timestamp_seconds > 900" in text
    assert "absent(agent_slo_export_timestamp_seconds)" in text


def test_exhausted_alert_names_the_policy() -> None:
    text = render_rules(CONFIGS)
    assert text.count('alert: "AgentSLOBudgetExhausted"') == 2
    assert "freeze auto-merge" in text


def test_no_target_missed_alert() -> None:
    assert "TargetMissed" not in render_rules(CONFIGS)


def test_default_windows_unchanged_in_output_count() -> None:
    text = render_rules({"pr-reviewer": CONFIGS["pr-reviewer"]})
    assert text.count("- alert:") == len(DEFAULT_WINDOWS) + 3  # + exhausted, gate-block, stale


def test_tiny_error_budget_is_not_rounded_away() -> None:
    text = render_rules({"a": cfg("a", pass_target=0.9999994)})
    assert "/ 6e-07" in text or "/ 5.99999999e-07" in text or "/ 6.00000e-07" in text, text[:600]


def test_every_alert_waits_five_minutes() -> None:
    text = render_rules(CONFIGS)
    alerts = text.count("- alert:")
    assert alerts == 2 * 5 + 1
    assert text.count('for: "5m"') == alerts


def test_summaries_describe_the_pass_rate_slo_without_foreign_wording() -> None:
    text = render_rules({"pr-reviewer": CONFIGS["pr-reviewer"]})
    summaries = [json.loads(m) for m in re.findall(r"summary: (\".*\")\n", text)]
    assert len(summaries) == 6
    for summary in summaries:
        assert "availability" not in summary and "env=" not in summary
        assert "\u2265" not in summary  # the rules use a strict ">", so never say "at least"
    burn = [s for s in summaries if "burning" in s]
    assert burn == [
        "pr-reviewer pass-rate: burning 5% of the 30d error budget in 6h "
        "(burn rate above 6.0x over both 6h and 24h)",
        "pr-reviewer pass-rate: burning 10% of the 30d error budget in 24h "
        "(burn rate above 3.0x over both 24h and 72h)",
        "pr-reviewer pass-rate: burning 10% of the 30d error budget in 72h "
        "(burn rate above 1.0x over both 72h and 168h)",
    ]
    assert any("10% ceiling" in s for s in summaries)
