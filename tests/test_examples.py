import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from agent_slo.config import load_config

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text()


def test_example_config_has_the_three_harness_agents() -> None:
    configs = load_config(ROOT / "examples" / "slo.toml")
    assert {n: c.max_cost_usd for n, c in configs.items()} == {
        "pr-reviewer": 0.30,
        "deploy-risk": 0.20,
        "runbook-gen": 0.50,
    }


def test_readme_demo_commands_run(tmp_path: Path) -> None:
    block = re.search(r"```bash test-demo\n(.*?)```", README, re.S)
    assert block, "README must contain a ```bash test-demo block"
    commands = block.group(1).replace("\\\n", " ").strip().splitlines()
    assert len(commands) == 4
    shutil.copytree(ROOT / "examples", tmp_path / "examples")
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    for command in commands:
        argv = shlex.split(command)
        assert argv[:3] == ["python", "-m", "agent_slo"], command
        result = subprocess.run(  # noqa: S603
            [sys.executable, *argv[1:]],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, (command, result.stderr)
    report = (tmp_path / "report.md").read_text()
    assert "- deploy-risk: EXHAUSTED" in report and "- pr-reviewer: OK" in report
    assert (tmp_path / "agent_slo.prom").stat().st_size > 0
    assert "AgentSLOBudgetExhausted" in (tmp_path / "rules.yml").read_text()


def test_readme_keeps_mandatory_statements() -> None:
    flat = " ".join(README.split())
    assert "Freeze auto-merge for that agent" in flat
    assert "human review" in flat
    assert "Spend is under-reported" in flat
    assert "promtool check rules" in flat and "other alerts are covered by golden-file" in flat
    assert "Keep the full history" in flat
    assert "never inspected, stored, or written to any output" in flat
