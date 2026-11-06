import subprocess
import sys
from pathlib import Path

import pytest

from agent_slo.cli import main

ROOT = Path(__file__).resolve().parent.parent
NOW = "2026-11-20T12:00:00Z"
CONFIG = """
[defaults]
pass_target = 0.95

[agents.pr-reviewer]
max_cost_usd = 0.30

[agents.deploy-risk]
max_cost_usd = 0.20

[agents.runbook-gen]
max_cost_usd = 0.50
"""


def run(*args: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "agent_slo", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    (tmp_path / "slo.toml").write_text(CONFIG)
    return tmp_path


def test_full_pipeline_through_real_processes(workdir: Path) -> None:
    conf, audit = str(workdir / "slo.toml"), str(workdir / "audit.jsonl")
    gen = run(
        "generate", "--config", conf, "--out", audit, "--now", NOW, "--seed", "3",
        "--scenario", "deploy-risk=degrading", "--scenario", "runbook-gen=burst",
    )  # fmt: skip
    assert gen.returncode == 0, gen.stderr
    assert "wrote 3600 records for 3 agents" in gen.stdout

    prom = workdir / "agent_slo.prom"
    met = run("metrics", "--config", conf, "--audit", audit, "--out", str(prom), "--now", NOW)
    assert met.returncode == 0 and met.stdout == "", met.stderr
    text = prom.read_text()
    assert 'agent_slo_budget_exhausted{agent="deploy-risk"} 1' in text
    assert 'agent_slo_budget_exhausted{agent="pr-reviewer"} 0' in text

    rules = run("rules", "--config", conf)
    assert rules.returncode == 0 and "AgentSLOBudgetExhausted" in rules.stdout

    rules_file = workdir / "rules.yml"
    assert run("rules", "--config", conf, "--out", str(rules_file)).returncode == 0
    assert rules_file.read_text() == rules.stdout

    report = run("report", "--config", conf, "--audit", audit, "--now", NOW)
    assert report.returncode == 0
    assert "- deploy-risk: EXHAUSTED" in report.stdout and "- pr-reviewer: OK" in report.stdout

    again = run("generate", "--config", conf, "--out", str(workdir / "b.jsonl"), "--now", NOW,
                "--seed", "3", "--scenario", "deploy-risk=degrading",
                "--scenario", "runbook-gen=burst")  # fmt: skip
    assert again.returncode == 0
    assert (workdir / "b.jsonl").read_bytes() == (workdir / "audit.jsonl").read_bytes()


C = "{dir}/slo.toml"
A = "{dir}/a.jsonl"
O = "{dir}/o.jsonl"  # noqa: E741


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["report", "--config", "{dir}/missing.toml", "--audit", A], "missing.toml"),
        (["report", "--config", C, "--audit", "{dir}/nope.jsonl"], "nope.jsonl"),
        (["report", "--config", C, "--audit", A, "--now", "2026-11-20"], "timezone"),
        (["report", "--config", C, "--audit", A, "--now", "garbage"], "ISO 8601"),
        (["generate", "--config", C, "--out", O, "--scenario", "pr-reviewer=chaos"], "--scenario"),
        (["generate", "--config", C, "--out", O, "--scenario", "ghost=burst"], "unknown agent"),
        (["generate", "--config", C, "--out", O, "--days", "0"], "--days"),
        (["generate", "--config", C, "--out", "{dir}/no/such/dir/o.jsonl"], "agent-slo: error"),
    ],
)
def test_failures_exit_2_with_one_line_on_stderr(
    workdir: Path, args: list[str], message: str
) -> None:
    (workdir / "a.jsonl").write_text("")
    result = run(*[a.replace("{dir}", str(workdir)) for a in args])
    assert result.returncode == 2, result.stdout
    assert result.stdout == ""
    assert message in result.stderr
    assert result.stderr.startswith("agent-slo: error:") and result.stderr.count("\n") == 1


def test_bad_config_content_exit_2(workdir: Path) -> None:
    (workdir / "bad.toml").write_text("[agents.a]\n")
    result = run("rules", "--config", str(workdir / "bad.toml"))
    assert result.returncode == 2 and "max_cost_usd is required" in result.stderr


def test_argparse_errors_exit_2() -> None:
    for argv in (["bogus"], ["rules"], ["rules", "--config", "x", "--nope"], []):
        result = run(*argv)
        assert result.returncode == 2 and result.stdout == "" and "usage:" in result.stderr


def test_help_runs() -> None:
    result = run("--help")
    assert result.returncode == 0 and "generate" in result.stdout


def test_main_function_returns_codes(workdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    conf = str(workdir / "slo.toml")
    assert main(["rules", "--config", conf]) == 0
    assert "groups:" in capsys.readouterr().out
    assert main(["rules", "--config", str(workdir / "missing.toml")]) == 2
    assert "agent-slo: error" in capsys.readouterr().err


def test_extreme_values_give_one_line_error_not_a_traceback(workdir: Path) -> None:
    conf, out, audit = str(workdir / "slo.toml"), str(workdir / "o.jsonl"), str(workdir / "a.jsonl")
    (workdir / "a.jsonl").write_text("")
    cases = [
        ["generate", "--config", conf, "--out", out, "--days", "999999999"],
        ["generate", "--config", conf, "--out", out, "--now", "0001-01-01T00:00:00+00:00"],
        ["report", "--config", conf, "--audit", audit, "--now", "9999-12-31T23:59:59+00:00"],
        [
            "metrics",
            "--config",
            conf,
            "--audit",
            audit,
            "--out",
            out,
            "--now",
            "9999-12-31T23:59:59Z",
        ],
    ]
    for argv in cases:
        result = run(*argv)
        assert result.returncode == 2, (argv, result.stdout, result.stderr)
        assert result.stderr.startswith("agent-slo: error:") and "Traceback" not in result.stderr
