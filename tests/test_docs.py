"""Documentation drift tests: what the program emits or accepts must be documented.

Authoritative sets: metric families = `# TYPE` names from render_metrics; alerts = every `alert:`
in render_rules; config keys = AgentConfig fields except `name`; CLI options = every option string
of every subcommand; states = BudgetState names. Each must appear in README.md as a backticked
token.
"""

import argparse
import dataclasses
import re
from datetime import UTC, datetime
from pathlib import Path

from agent_slo.budget import BudgetState
from agent_slo.cli import _build_parser
from agent_slo.config import AgentConfig
from agent_slo.metrics import render_metrics
from agent_slo.rules import render_rules

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text()
CFG = AgentConfig("a", 1.0, 0.95, 30.0, 0.95, 0.8, 0.95, 0.1, 5)


def documented(name: str) -> bool:
    """True if README has `name` as a backticked token, optionally followed by {labels} or more."""
    return re.search(rf"`{re.escape(name)}[`{{ =]", README) is not None


def test_every_metric_family_is_documented() -> None:
    text = render_metrics([], {}, {"a": CFG}, datetime(2026, 11, 20, tzinfo=UTC))
    families = re.findall(r"^# TYPE (\S+) ", text, re.M)
    assert len(families) >= 15
    missing = [f for f in families if not documented(f)]
    assert not missing, f"metrics missing from README: {missing}"


def test_every_alert_and_recording_rule_is_documented() -> None:
    rules = render_rules({"a": CFG})
    alerts = set(re.findall(r'alert: "(\w+)"', rules))
    assert {"AgentSLOFastBurn", "AgentSLOExportStale"} <= alerts
    missing = [a for a in alerts if not documented(a)]
    assert not missing, f"alerts missing from README: {missing}"
    assert "agent_slo:burn_rate:ratio_<window>" in README
    assert 'record: "agent_slo:burn_rate:ratio_6h"' in rules


def test_every_config_key_is_documented() -> None:
    keys = [f.name for f in dataclasses.fields(AgentConfig) if f.name != "name"]
    assert len(keys) == 8
    missing = [k for k in keys if not documented(k)]
    assert not missing, f"config keys missing from README: {missing}"


def test_every_cli_option_is_documented_and_has_help() -> None:
    parser = _build_parser()
    subparsers = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    assert set(subparsers.choices) == {"generate", "metrics", "rules", "report"}
    for command, sub in subparsers.choices.items():
        assert documented(command), command
        assert sub.description, f"{command} has no description"
        for action in sub._actions:
            if isinstance(action, argparse._HelpAction):
                continue
            assert action.help, f"{command} {action.option_strings} has no help text"
            for option in action.option_strings:
                assert f"`{option}" in README, f"{command} {option} missing from README"


def test_every_state_is_documented() -> None:
    missing = [s.name for s in BudgetState if not documented(s.name)]
    assert not missing, f"states missing from README: {missing}"


def test_internal_links_resolve() -> None:
    for target in re.findall(r"\]\((?!https?://|#)([^)#]+)", README):
        assert (ROOT / target).exists(), f"README links to missing {target}"


def test_plans_index_lists_every_plan() -> None:
    index = (ROOT / "plans" / "README.md").read_text()
    for plan in sorted((ROOT / "plans").glob("step-*.md")):
        assert f"`{plan.name}`" in index, f"{plan.name} missing from plans/README.md"
