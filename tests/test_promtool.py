"""Run the generated files through the real promtool. Skipped (and reported as skipped by pytest)
when promtool is not installed, so the check never claims verification it did not do."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_slo.budget import DEFAULT_WINDOWS
from agent_slo.config import AgentConfig
from agent_slo.rules import render_rules

PROMTOOL = shutil.which("promtool") or str(Path.home() / ".local" / "bin" / "promtool")
if os.environ.get("REQUIRE_PROMTOOL") and not Path(PROMTOOL).exists():
    # CI sets this so a broken promtool install fails the build instead of skipping the tests.
    pytest.fail("REQUIRE_PROMTOOL is set but promtool was not found", pytrace=False)
pytestmark = pytest.mark.skipif(not Path(PROMTOOL).exists(), reason="promtool not installed")

CFG = AgentConfig(
    name="pr-reviewer",
    max_cost_usd=0.3,
    pass_target=0.95,
    latency_threshold_s=30.0,
    latency_target=0.95,
    cost_headroom_fraction=0.8,
    cost_headroom_target=0.95,
    gate_block_ceiling=0.1,
    min_events=5,
)


def promtool(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [PROMTOOL, *args], input=stdin, capture_output=True, text=True, timeout=60, check=False
    )


def test_generated_rules_pass_check_rules(tmp_path: Path) -> None:
    (tmp_path / "rules.yml").write_text(render_rules({"pr-reviewer": CFG, "deploy-risk": CFG}))
    result = promtool("check", "rules", str(tmp_path / "rules.yml"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SUCCESS" in result.stdout


def test_non_integer_window_rules_pass_check_rules(tmp_path: Path) -> None:
    text = render_rules({"a": CFG}, windows=(("Odd", 5.0, 1.5, 6.0, "page"),))
    (tmp_path / "rules.yml").write_text(text)
    assert promtool("check", "rules", str(tmp_path / "rules.yml")).returncode == 0


def test_generated_metrics_pass_check_metrics() -> None:
    from datetime import UTC, datetime

    from agent_slo.metrics import render_metrics

    text = render_metrics([], {}, {"pr-reviewer": CFG}, datetime(2026, 11, 20, tzinfo=UTC))
    result = promtool("check", "metrics", stdin=text)
    assert result.returncode == 0, result.stdout + result.stderr


def _unit_test(tmp_path: Path, bad_6h: int) -> subprocess.CompletedProcess[str]:
    rules = render_rules({"pr-reviewer": CFG})
    (tmp_path / "rules.yml").write_text(rules)
    summary = json.loads(
        re.search(r'alert: "AgentSLOFastBurn".*?summary: (".*?")\n', rules, re.S).group(1)  # type: ignore[union-attr]
    )
    sel = 'agent="pr-reviewer"'
    series = {
        # 6h window: 20 runs; 24h window: 40 runs, 15 bad (burn 7.5x, over the 6x threshold)
        f'agent_slo_window_eligible{{{sel},window="6h"}}': "20x15",
        f'agent_slo_window_bad{{{sel},window="6h"}}': f"{bad_6h}x15",
        f'agent_slo_window_eligible{{{sel},window="24h"}}': "40x15",
        f'agent_slo_window_bad{{{sel},window="24h"}}': "15x15",
    }
    firing = bad_6h >= 2 and bad_6h / 20 / 0.05 > 6
    exp = (
        f"""
          - exp_labels: {{severity: page, agent: pr-reviewer, slo: pass-rate}}
            exp_annotations: {{summary: {json.dumps(summary)}}}"""
        if firing
        else " []"
    )
    inputs = "\n".join(f"      - series: '{k}'\n        values: '{v}'" for k, v in series.items())
    test_file = f"""rule_files: [rules.yml]
evaluation_interval: 1m
tests:
  - interval: 1m
    input_series:
{inputs}
    alert_rule_test:
      - eval_time: 12m
        alertname: AgentSLOFastBurn
        exp_alerts:{exp}
"""
    (tmp_path / "test.yml").write_text(test_file)
    return promtool("test", "rules", str(tmp_path / "test.yml"))


def test_fast_burn_fires_when_both_windows_and_guards_hold(tmp_path: Path) -> None:
    result = _unit_test(tmp_path, bad_6h=11)  # 55% bad in 6h = 11x burn
    assert result.returncode == 0, result.stdout + result.stderr


def test_fast_burn_silent_with_one_bad_run(tmp_path: Path) -> None:
    result = _unit_test(tmp_path, bad_6h=1)  # below the 2-bad-run guard (and below threshold)
    assert result.returncode == 0, result.stdout + result.stderr


def test_default_windows_exist_for_the_unit_test() -> None:
    assert DEFAULT_WINDOWS[0][0] == "Fast Burn"
