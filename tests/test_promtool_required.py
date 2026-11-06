"""REQUIRE_PROMTOOL must turn a missing promtool into a failure, and its absence into skips."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run_without_promtool(tmp_path: Path, *, require: bool) -> subprocess.CompletedProcess[str]:
    empty = tmp_path / "empty-path"
    empty.mkdir()
    env = {"PATH": str(empty), "HOME": str(tmp_path), "PYTHONPATH": str(ROOT)}
    if require:
        env["REQUIRE_PROMTOOL"] = "1"
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", "tests/test_promtool.py", "-q", "-p", "no:cacheprovider"],
        cwd=ROOT,
        env={
            k: v for k, v in env.items() if k in ("PATH", "HOME", "PYTHONPATH", "REQUIRE_PROMTOOL")
        },
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_required_but_missing_fails(tmp_path: Path) -> None:
    result = run_without_promtool(tmp_path, require=True)
    assert result.returncode != 0
    assert "REQUIRE_PROMTOOL is set but promtool was not found" in result.stdout + result.stderr


def test_not_required_and_missing_skips(tmp_path: Path) -> None:
    result = run_without_promtool(tmp_path, require=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "skipped" in result.stdout and "passed" not in result.stdout
