"""Per-agent SLO configuration loaded from TOML (standard library ``tomllib``).

Layout::

    [defaults]            # optional; any key below
    pass_target = 0.95

    [agents.pr-reviewer]  # one table per agent; names match [A-Za-z0-9._-]{1,64}
    max_cost_usd = 0.30   # required (here or in [defaults]); the audit log does not carry it

Keys (built-in default; valid range):

- ``max_cost_usd``: the agent's per-run cost limit in USD (required; > 0)
- ``pass_target``: pass-rate SLO (0.95; between 0 and 1 exclusive)
- ``latency_threshold_s``: a run is fast at or under this many seconds (30; > 0)
- ``latency_target``: share of successful runs that must be fast (0.95; 0 to 1 exclusive)
- ``cost_headroom_fraction``: a run has headroom at or under this share of max_cost_usd
  (0.8; > 0 and <= 1)
- ``cost_headroom_target``: share of successful runs that must have headroom
  (0.95; 0 to 1 exclusive)
- ``gate_block_ceiling``: highest acceptable gate-block rate (0.10; 0 to 1 exclusive)
- ``min_events``: fewer runs than this gives no verdict (5; integer >= 1)

Unknown keys and unknown tables are rejected with ``ConfigError``.
"""

from __future__ import annotations

import math
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

_AGENT_NAME = re.compile(r"[A-Za-z0-9._-]{1,64}")
_BUILTIN_DEFAULTS: dict[str, float] = {
    "pass_target": 0.95,
    "latency_threshold_s": 30.0,
    "latency_target": 0.95,
    "cost_headroom_fraction": 0.8,
    "cost_headroom_target": 0.95,
    "gate_block_ceiling": 0.10,
    "min_events": 5,
}
_KEYS = frozenset(_BUILTIN_DEFAULTS) | {"max_cost_usd", "min_events"}
_OPEN_UNIT = ("pass_target", "latency_target", "cost_headroom_target", "gate_block_ceiling")


class ConfigError(ValueError):
    """The configuration file is invalid."""


@dataclass(frozen=True)
class AgentConfig:
    """Validated SLO settings for one agent; see the module docstring for each key."""

    name: str
    max_cost_usd: float
    pass_target: float
    latency_threshold_s: float
    latency_target: float
    cost_headroom_fraction: float
    cost_headroom_target: float
    gate_block_ceiling: float
    min_events: int = 5


def _value(where: str, key: str, value: object) -> float:
    if key == "min_events":
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ConfigError(f"{where}: {key} must be an integer >= 1")
        return float(value)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{where}: {key} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ConfigError(f"{where}: {key} must be finite")
    if key in _OPEN_UNIT and not 0 < number < 1:
        raise ConfigError(f"{where}: {key} must be between 0 and 1 exclusive")
    if key == "cost_headroom_fraction" and not 0 < number <= 1:
        raise ConfigError(f"{where}: {key} must be in (0, 1]")
    if key in ("latency_threshold_s", "max_cost_usd") and number <= 0:
        raise ConfigError(f"{where}: {key} must be positive")
    return number


def _table(where: str, table: object) -> dict[str, float]:
    if not isinstance(table, dict):
        raise ConfigError(f"{where} must be a table")
    unknown = sorted(set(table) - _KEYS)
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {', '.join(map(str, unknown))}")
    return {key: _value(where, key, val) for key, val in table.items()}


def load_config(path: str | Path) -> dict[str, AgentConfig]:
    """Load and validate the config; returns agent name -> AgentConfig."""
    try:
        with open(path, "rb") as handle:
            doc = tomllib.load(handle)
    except tomllib.TOMLDecodeError as err:
        raise ConfigError(f"invalid TOML: {err}") from None

    unknown_top = sorted(set(doc) - {"defaults", "agents"})
    if unknown_top:
        raise ConfigError(f"unknown top-level key(s) {', '.join(unknown_top)}")
    defaults = {**_BUILTIN_DEFAULTS, **_table("[defaults]", doc.get("defaults", {}))}
    agents = doc.get("agents")
    if not isinstance(agents, dict) or not agents:
        raise ConfigError("at least one [agents.<name>] table is required")

    result: dict[str, AgentConfig] = {}
    for name, table in agents.items():
        if not _AGENT_NAME.fullmatch(name):
            raise ConfigError(f"invalid agent name {name!r}")
        merged = {**defaults, **_table(f"[agents.{name}]", table)}
        if "max_cost_usd" not in merged:
            raise ConfigError(f"[agents.{name}]: max_cost_usd is required")
        min_events = int(merged.pop("min_events"))
        result[name] = AgentConfig(name=name, min_events=min_events, **merged)
    return result
