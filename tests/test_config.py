from pathlib import Path

import pytest

from agent_slo.config import ConfigError, load_config


def cfg(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "slo.toml"
    path.write_text(text)
    return path


def test_defaults_and_override(tmp_path: Path) -> None:
    path = cfg(
        tmp_path,
        """
[defaults]
pass_target = 0.9
max_cost_usd = 0.5

[agents.pr-reviewer]
max_cost_usd = 0.30
latency_threshold_s = 12

[agents.runbook-gen]
""",
    )
    configs = load_config(path)
    pr = configs["pr-reviewer"]
    assert (pr.max_cost_usd, pr.pass_target, pr.latency_threshold_s) == (0.30, 0.9, 12.0)
    rb = configs["runbook-gen"]
    assert (rb.max_cost_usd, rb.cost_headroom_fraction, rb.gate_block_ceiling) == (0.5, 0.8, 0.10)
    assert rb.latency_target == 0.95


@pytest.mark.parametrize(
    "text",
    [
        "[agents.a]\n",  # no max_cost_usd
        "[agents.a]\nmax_cost_usd = 0\n",
        "[agents.a]\nmax_cost_usd = 1\npass_target = 1.0\n",
        "[agents.a]\nmax_cost_usd = 1\npass_target = 0\n",
        "[agents.a]\nmax_cost_usd = 1\ncost_headroom_fraction = 1.5\n",
        "[agents.a]\nmax_cost_usd = 1\nbogus = 1\n",
        "[agents.a]\nmax_cost_usd = true\n",
        "[agents.a]\nmax_cost_usd = 'x'\n",
        "[agents.bad name]\nmax_cost_usd = 1\n",
        "[defaults]\nbogus = 1\n[agents.a]\nmax_cost_usd = 1\n",
        "[other]\nx = 1\n[agents.a]\nmax_cost_usd = 1\n",
        "[defaults]\npass_target = 0.9\n",  # no agents
        "[agents.a\n",  # malformed TOML
    ],
)
def test_invalid_config_raises(tmp_path: Path, text: str) -> None:
    with pytest.raises(ConfigError):
        load_config(cfg(tmp_path, text))


def test_min_events_accepted_and_validated(tmp_path: Path) -> None:
    ok = load_config(cfg(tmp_path, "[agents.a]\nmax_cost_usd = 1\nmin_events = 12\n"))
    assert ok["a"].min_events == 12
    assert load_config(cfg(tmp_path, "[agents.a]\nmax_cost_usd = 1\n"))["a"].min_events == 5
    for bad in ("0", "2.5", "true", "'3'"):
        with pytest.raises(ConfigError):
            load_config(cfg(tmp_path, f"[agents.a]\nmax_cost_usd = 1\nmin_events = {bad}\n"))
