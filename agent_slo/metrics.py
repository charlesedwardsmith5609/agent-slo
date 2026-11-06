"""Render agent SLO metrics in Prometheus text format for node_exporter's textfile collector.

Synchronous, single-threaded. Pure rendering except ``write_textfile``, whose single caller (one
export run) owns the output file; it replaces the file atomically so a reader never sees a
partial file.

Alerts use only the exact ``agent_slo_window_*`` gauges, computed from audit timestamps at export
time. The ``*_total`` counters are informational totals over the whole file: Prometheus sees them
change at export time (not run time) and ``increase()`` extrapolates, so no alert may use them.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta

from agent_slo.audit import SKIP_REASONS, RunRecord
from agent_slo.budget import (
    DEFAULT_WINDOWS,
    BudgetState,
    build_windows,
    evaluate_budget,
    window_counts,
    window_label,
)
from agent_slo.config import AgentConfig
from agent_slo.files import write_atomic
from agent_slo.sli import compute_slis

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_FOREVER = datetime(9999, 12, 31, tzinfo=UTC)

Labels = Mapping[str, str]
Sample = tuple[Labels, float]


def escape_label(value: str) -> str:
    """Escape a label value per the Prometheus text format."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _format_value(value: float) -> str:
    return str(int(value)) if value == int(value) and abs(value) < 1e15 else repr(float(value))


class _Families:
    def __init__(self) -> None:
        self._order: list[str] = []
        self._meta: dict[str, tuple[str, str]] = {}
        self._samples: dict[str, list[Sample]] = {}

    def add(self, name: str, kind: str, help_text: str, labels: Labels, value: float) -> None:
        """Add one sample, creating the family (with its HELP and TYPE) on first use."""
        if name not in self._meta:
            self._order.append(name)
            self._meta[name] = (kind, help_text)
            self._samples[name] = []
        self._samples[name].append((labels, value))

    def declare(self, name: str, kind: str, help_text: str) -> None:
        """Make sure a family is rendered (with HELP/TYPE) even with no samples."""
        if name not in self._meta:
            self._order.append(name)
            self._meta[name] = (kind, help_text)
            self._samples[name] = []

    def render(self) -> str:
        """Exposition text: HELP, TYPE and samples for each family, in first-use order."""
        lines: list[str] = []
        for name in self._order:
            kind, help_text = self._meta[name]
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {kind}")
            for labels, value in self._samples[name]:
                if labels:
                    body = ",".join(f'{k}="{escape_label(v)}"' for k, v in labels.items())
                    lines.append(f"{name}{{{body}}} {_format_value(value)}")
                else:
                    lines.append(f"{name} {_format_value(value)}")
        return "\n".join(lines) + "\n"


def render_metrics(
    records: Sequence[RunRecord],
    skipped: Mapping[str, int],
    configs: Mapping[str, AgentConfig],
    now: datetime,
    *,
    compliance_days: int = 30,
    windows: Sequence[tuple[str, float, float, float, str]] = DEFAULT_WINDOWS,
) -> str:
    """Exposition text for every configured agent as of ``now`` (tz-aware)."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    by_agent: dict[str, list[RunRecord]] = defaultdict(list)
    for rec in records:
        by_agent[rec.agent_name].append(rec)
    fam = _Families()
    for name in (
        "agent_runs_total",
        "agent_run_latency_good_total",
        "agent_run_cost_headroom_good_total",
        "agent_run_precall_cost_blocks_total",
    ):
        fam.declare(name, "counter", f"Informational audit-file totals ({name}); not for alerts")
    for agent, cfg in sorted(configs.items()):
        bucket = by_agent.get(agent, [])
        totals = compute_slis(bucket, cfg, _EPOCH, _FOREVER)
        for outcome, count in (
            ("pass", totals.passed),
            ("gate_blocked", totals.gate_blocked),
            ("failed", totals.failed),
        ):
            fam.add(
                "agent_runs_total",
                "counter",
                "Non-dry-run runs in the audit file by outcome (informational, not for alerts)",
                {"agent": agent, "outcome": outcome},
                count,
            )
        fam.add(
            "agent_run_latency_good_total",
            "counter",
            "Successful runs within the latency threshold",
            {"agent": agent},
            totals.latency_good,
        )
        fam.add(
            "agent_run_cost_headroom_good_total",
            "counter",
            "Successful runs at or under the cost headroom fraction of max_cost_usd",
            {"agent": agent},
            totals.cost_good,
        )
        fam.add(
            "agent_run_precall_cost_blocks_total",
            "counter",
            "Runs refused by the worst-case cost ceiling before any API call",
            {"agent": agent},
            totals.precall_cost_blocks,
        )

        # Exact window counts from audit timestamps: the only inputs alerts may use.
        _, alert_windows = build_windows(cfg, compliance_days, windows)
        hours = {compliance_days * 24.0}
        for win in alert_windows:
            hours.update((win.short_window_hours, win.long_window_hours))
        for h in sorted(hours):
            eligible, bad = window_counts(bucket, cfg, now, h)
            labels = {"agent": agent, "window": window_label(h)}
            fam.add(
                "agent_slo_window_eligible",
                "gauge",
                "Non-dry-run runs in the trailing window, by audit timestamp",
                labels,
                eligible,
            )
            fam.add(
                "agent_slo_window_bad",
                "gauge",
                "Failed or gate-blocked runs in the trailing window, by audit timestamp",
                labels,
                bad,
            )

        status = evaluate_budget(
            records, cfg, now, compliance_days=compliance_days, windows=windows
        )
        if status.consumed_fraction is not None:
            fam.add(
                "agent_slo_budget_consumed_ratio",
                "gauge",
                "Bad-run rate over the compliance window divided by the error budget",
                {"agent": agent},
                status.consumed_fraction,
            )
        fam.add(
            "agent_slo_budget_exhausted",
            "gauge",
            "1 when the pass-rate error budget is exhausted",
            {"agent": agent},
            1 if status.exhausted else 0,
        )
        fam.add(
            "agent_slo_gate_block_breach",
            "gauge",
            "1 when the gate-block rate exceeds its ceiling",
            {"agent": agent},
            1 if status.gate_block_breach else 0,
        )
        for state in BudgetState:
            fam.add(
                "agent_slo_state",
                "gauge",
                "One-hot budget state",
                {"agent": agent, "state": state.name.lower()},
                1 if status.state is state else 0,
            )

        end_full = compute_slis(bucket, cfg, *_compliance_bounds(now, compliance_days))
        for sli, ratio, denom, target in (
            ("pass_rate", end_full.pass_rate, end_full.eligible, cfg.pass_target),
            ("latency", end_full.latency_ratio, end_full.latency_total, cfg.latency_target),
            (
                "cost_headroom",
                end_full.cost_headroom_ratio,
                end_full.cost_total,
                cfg.cost_headroom_target,
            ),
        ):
            labels = {"agent": agent, "sli": sli}
            fam.add("agent_slo_sli_target", "gauge", "SLI target", labels, target)
            # A missing ratio series means "not enough data for a verdict".
            if ratio is not None and denom >= cfg.min_events:
                fam.add(
                    "agent_slo_sli_ratio",
                    "gauge",
                    "SLI over the compliance window (omitted below min_events)",
                    labels,
                    ratio,
                )

    fam.declare(
        "agent_slo_sli_ratio", "gauge", "SLI over the compliance window (omitted below min_events)"
    )
    for reason in SKIP_REASONS:
        fam.add(
            "agent_slo_audit_lines_skipped_total",
            "counter",
            "Audit lines that could not be used, by reason",
            {"reason": reason},
            skipped.get(reason, 0),
        )
    # Names from the log are untrusted and unbounded, so they never become label values:
    # only totals are exported (names appear, escaped, in the report).
    stray = [name for name in by_agent if name not in configs]
    fam.add(
        "agent_slo_unconfigured_runs_total",
        "counter",
        "Runs from agents missing in the config",
        {},
        sum(len(by_agent[name]) for name in stray),
    )
    fam.add(
        "agent_slo_unconfigured_agents",
        "gauge",
        "Distinct agent names in the logs that are missing in the config",
        {},
        len(stray),
    )
    fam.add(
        "agent_slo_export_timestamp_seconds",
        "gauge",
        "Unix time of this export",
        {},
        int(now.timestamp()),
    )
    return fam.render()


def _compliance_bounds(now: datetime, compliance_days: int) -> tuple[datetime, datetime]:
    end = now + timedelta(seconds=1)
    return end - timedelta(days=compliance_days), end


write_textfile = write_atomic
