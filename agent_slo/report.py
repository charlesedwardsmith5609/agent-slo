"""Weekly Markdown report.

Synchronous and pure: the same inputs and ``now`` give the same text. Agent names that come from
the audit log are untrusted, so they are escaped and made visible (never silently altered).
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta

from agent_slo.audit import RunRecord
from agent_slo.budget import BudgetState, BudgetStatus, evaluate_budget
from agent_slo.config import AgentConfig
from agent_slo.sli import AgentSLIs, compute_slis, unconfigured_agent_counts

MAX_NAME_CHARS = 64
MAX_UNCONFIGURED_LISTED = 20
POLICY_ACTION = "Error budget exhausted: freeze auto-merge for this agent and require human review."
_MD_SPECIALS = frozenset("\\`*_{}[]<>()#+-.!|~&\"'$%^=:/@")


def safe_name(name: str) -> str:
    """Render an untrusted name so it is visible, unambiguous and inert in Markdown."""
    clipped = name
    suffix = ""
    if len(name) > MAX_NAME_CHARS:
        clipped = name[:MAX_NAME_CHARS]
        suffix = "…#" + hashlib.sha1(name.encode("utf-8", "replace")).hexdigest()[:8]  # noqa: S324
    out: list[str] = []
    for ch in clipped:
        code = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif code < 0x20 or code == 0x7F:
            out.append(f"\\x{code:02x}")
        elif ch.isprintable():
            out.append("\\" + ch if ch in _MD_SPECIALS else ch)
        else:
            out.append(f"\\u{code:04x}" if code <= 0xFFFF else f"\\U{code:08x}")
    return "".join(out) + suffix


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _verdict(ratio: float | None, denom: int, min_events: int, ok: bool, *, label: str) -> str:
    if denom == 0 or ratio is None:
        return f"{label}: no data"
    if denom < min_events:
        return f"{label}: insufficient data (n={denom} < {min_events})"
    return f"{label}: {'PASS' if ok else 'MISS'}"


def _require_positive_int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _agent_section(cfg: AgentConfig, week: AgentSLIs, status: BudgetStatus) -> list[str]:
    me = cfg.min_events
    consumed = (
        "no data" if status.consumed_fraction is None else f"{status.consumed_fraction * 100:.0f}%"
    )
    lines = [
        f"## {cfg.name}",
        "",
        f"- State: **{status.state.name}** "
        f"({status.compliance_days}d budget consumed: {consumed}; "
        f"{status.bad} bad of {status.eligible} eligible runs)",
    ]
    if status.exhausted:
        lines += ["", f"> **Action required:** {POLICY_ACTION}", ""]
    firing = [w for w in status.windows if w.firing]
    if firing:
        lines.append("- Burn alerts firing:")
        lines += [
            f"  - {w.name} ({w.severity}): burn {w.short_burn:.1f}x over {w.short_hours:g}h and "
            f"{w.long_burn:.1f}x over {w.long_hours:g}h (threshold {w.threshold:g}x)"
            for w in firing
        ]
    else:
        lines.append("- Burn alerts firing: none")

    pass_ok = week.pass_rate is not None and week.pass_rate >= cfg.pass_target
    block_ok = week.gate_block_rate is not None and week.gate_block_rate <= cfg.gate_block_ceiling
    lat_ok = week.latency_ratio is not None and week.latency_ratio >= cfg.latency_target
    cost_ok = (
        week.cost_headroom_ratio is not None
        and week.cost_headroom_ratio >= cfg.cost_headroom_target
    )
    p95 = "n/a" if week.p95_duration_s is None else f"{week.p95_duration_s:.1f}s"
    p95c = "n/a" if week.p95_cost_ratio is None else f"{week.p95_cost_ratio * 100:.0f}% of max"
    meanc = "n/a" if week.mean_cost_ratio is None else f"{week.mean_cost_ratio * 100:.0f}% of max"
    lines += [
        "",
        "This week:",
        f"- Pass rate {_pct(week.pass_rate)} (target {_pct(cfg.pass_target)}); "
        + _verdict(week.pass_rate, week.eligible, me, pass_ok, label="verdict"),
        f"- Gate-block rate {_pct(week.gate_block_rate)} (ceiling {_pct(cfg.gate_block_ceiling)}); "
        + _verdict(week.gate_block_rate, week.parseable, me, block_ok, label="verdict"),
        f"- Latency: {_pct(week.latency_ratio)} within {cfg.latency_threshold_s:g}s "
        f"(target {_pct(cfg.latency_target)}), p95 {p95}; "
        + _verdict(week.latency_ratio, week.latency_total, me, lat_ok, label="verdict"),
        f"- Cost headroom: {_pct(week.cost_headroom_ratio)} of runs at or under "
        f"{cfg.cost_headroom_fraction * 100:.0f}% of ${cfg.max_cost_usd:g} "
        f"(target {_pct(cfg.cost_headroom_target)}); mean {meanc}, p95 {p95c}; "
        + _verdict(week.cost_headroom_ratio, week.cost_total, me, cost_ok, label="verdict"),
        f"- Runs: {week.eligible} eligible, {week.passed} passed, "
        f"{week.gate_blocked} gate-blocked, {week.failed} failed "
        f"({week.precall_cost_blocks} refused by the pre-call cost ceiling)",
        "",
    ]
    return lines


def render_report(
    records: Sequence[RunRecord],
    skipped: Mapping[str, int],
    configs: Mapping[str, AgentConfig],
    now: datetime,
    *,
    week_days: int = 7,
    compliance_days: int = 30,
) -> str:
    """The weekly report for the ``week_days`` ending at ``now`` (tz-aware)."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    week_days = _require_positive_int("week_days", week_days)
    compliance_days = _require_positive_int("compliance_days", compliance_days)
    now = now.astimezone(UTC)
    week_start = now - timedelta(days=week_days)
    week_end = now + timedelta(seconds=1)  # a run stamped exactly at ``now`` counts

    by_agent: dict[str, list[RunRecord]] = defaultdict(list)
    for rec in records:
        by_agent[rec.agent_name].append(rec)

    skipped_total = sum(skipped.values())
    lines = [
        "# Agent SLO weekly report",
        "",
        f"- Period: {week_start:%Y-%m-%d %H:%M} UTC to {now:%Y-%m-%d %H:%M} UTC ({week_days} days)",
        f"- Error budget window: {compliance_days} days",
        f"- Audit records read: {len(records)}",
    ]
    if skipped_total:
        detail = ", ".join(f"{reason}: {n}" for reason, n in sorted(skipped.items()) if n)
        lines.append(
            f"- Audit lines skipped: {skipped_total} ({detail}). Skipped lines bias every SLI."
        )
    else:
        lines.append("- Audit lines skipped: none")
    lines.append("")

    sections: list[list[str]] = []
    summary: list[str] = ["## Summary", ""]
    for name in sorted(configs):
        cfg = configs[name]
        bucket = by_agent.get(name, [])
        status = evaluate_budget(bucket, cfg, now, compliance_days=compliance_days)
        week = compute_slis(bucket, cfg, week_start, week_end)
        summary.append(f"- {cfg.name}: {status.state.name}")
        sections.append(_agent_section(cfg, week, status))
        if status.state is BudgetState.EXHAUSTED:
            summary[-1] += " (freeze auto-merge, require human review)"
    if not configs:
        summary.append("- No agents configured")
    lines += summary + [""]
    for section in sections:
        lines += section

    unconfigured = sorted(
        unconfigured_agent_counts(records, configs).items(), key=lambda kv: (-kv[1], kv[0])
    )
    if unconfigured:
        lines += ["## Agents in the logs but not in the config", ""]
        lines += [
            f"- `{safe_name(n)}`: {c} runs" for n, c in unconfigured[:MAX_UNCONFIGURED_LISTED]
        ]
        rest = unconfigured[MAX_UNCONFIGURED_LISTED:]
        if rest:
            lines.append(f"- {len(rest)} more agents, {sum(c for _, c in rest)} runs")
        lines.append("")

    lines += [
        "## How to read this report",
        "",
        "- Eligible runs exclude dry runs. A run is *bad* if it failed or a gate blocked it; "
        "everything else eligible is a pass.",
        "- Budget consumed compares the bad-run rate over the budget window with the error "
        "budget (1 minus the pass target). 100% or more means the budget is exhausted.",
        "- Burn rate is how many times faster than the sustainable pace the budget is being "
        "spent (1x is exactly sustainable). An alert fires only when both its short and its "
        "long window exceed the threshold.",
        "- States: NO_DATA (no eligible runs), INSUFFICIENT_DATA (fewer than `min_events` runs), "
        "OK, BURNING (at least one burn alert is firing), EXHAUSTED (budget used up; "
        "takes precedence).",
        '- Verdicts: PASS or MISS against the target; "insufficient data" means too few '
        "runs to judge, which is not a pass.",
        "",
        "## Caveats",
        "",
        "- Dry runs are excluded from every figure.",
        "- Failed runs always log cost 0, including a run that fails to parse after a paid API "
        "call, so spend is under-reported.",
        "- Timestamps are end-of-run UTC to the second.",
        "- Outcomes are read from agent-harness `gate_reason` wording; a harness change can "
        "misclassify runs.",
        "- Fewer than `min_events` runs gives no verdict, not a pass.",
        "",
    ]
    return "\n".join(lines)
