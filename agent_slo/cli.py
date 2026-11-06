"""Command line interface: ``python -m agent_slo <generate|metrics|rules|report>``.

One process per invocation, single-threaded. The process owns the files it writes (atomically).
Every expected failure prints one line to stderr and exits 2.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from agent_slo.audit import AuditLimitError, read_audit
from agent_slo.config import ConfigError, load_config
from agent_slo.files import write_atomic
from agent_slo.generate import SCENARIOS, generate_records, write_audit
from agent_slo.metrics import render_metrics
from agent_slo.report import render_report
from agent_slo.rules import render_rules

_CONFIG_HELP = "TOML file with the per-agent SLO settings (see examples/slo.toml)"
_AUDIT_HELP = "agent-harness audit JSONL file; it must hold the full history needed (30 days)"
_NOW_HELP = (
    "evaluation time, ISO 8601 with a timezone, e.g. 2026-11-20T12:00:00Z (default: current time)"
)


class _Usage(Exception):
    """Bad command-line value."""


def _parse_now(value: str) -> datetime:
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        raise _Usage(f"--now must be an ISO 8601 timestamp, got {value!r}") from None
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise _Usage("--now must include a timezone (for example 2026-11-20T12:00:00Z)")
    return moment


def _now(value: str | None) -> datetime:
    return _parse_now(value) if value else datetime.now(UTC).replace(microsecond=0)


def _parse_scenarios(items: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in items:
        agent, sep, scenario = item.partition("=")
        if not sep or not agent or scenario not in SCENARIOS:
            raise _Usage(
                f"--scenario must be AGENT=NAME with NAME in {', '.join(SCENARIOS)}: {item!r}"
            )
        result[agent] = scenario
    return result


def _emit(text: str, out: str | None) -> None:
    if out:
        write_atomic(out, text)
    else:
        sys.stdout.write(text)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-slo", description="Agent SLIs, alerts and reports")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser(
        "generate",
        help="write a synthetic audit log for demos",
        description="Write a synthetic agent-harness audit log (JSONL) for each configured agent.",
    )
    gen.add_argument("--config", required=True, help=_CONFIG_HELP)
    gen.add_argument("--out", required=True, help="audit JSONL file to write (replaced atomically)")
    gen.add_argument("--days", type=int, default=30, help="length of the span (default: 30)")
    gen.add_argument(
        "--runs-per-day", type=int, default=40, help="runs per agent per day (default: 40)"
    )
    gen.add_argument(
        "--seed", type=int, default=1, help="random seed; same seed, same data (default: 1)"
    )
    gen.add_argument("--now", help=_NOW_HELP.replace("evaluation time", "end of the span"))
    gen.add_argument(
        "--scenario",
        action="append",
        default=[],
        metavar="AGENT=NAME",
        help=f"one of {', '.join(SCENARIOS)}; unnamed agents are healthy; repeatable",
    )

    met = sub.add_parser(
        "metrics",
        help="write Prometheus textfile metrics",
        description="Write a .prom file for node_exporter's textfile collector.",
    )
    met.add_argument("--config", required=True, help=_CONFIG_HELP)
    met.add_argument("--audit", required=True, help=_AUDIT_HELP)
    met.add_argument("--out", required=True, help="the .prom file to write (replaced atomically)")
    met.add_argument("--now", help=_NOW_HELP)

    rul = sub.add_parser(
        "rules",
        help="print or write Prometheus rules",
        description="Write the Prometheus recording and alerting rules (needs no audit log).",
    )
    rul.add_argument("--config", required=True, help=_CONFIG_HELP)
    rul.add_argument("--out", help="rule file to write (replaced atomically); default: stdout")

    rep = sub.add_parser(
        "report",
        help="print or write the weekly Markdown report",
        description="Write the Markdown report for the 7 days ending at --now.",
    )
    rep.add_argument("--config", required=True, help=_CONFIG_HELP)
    rep.add_argument("--audit", required=True, help=_AUDIT_HELP)
    rep.add_argument("--out", help="report file to write (replaced atomically); default: stdout")
    rep.add_argument("--now", help=_NOW_HELP)
    return parser


def _run(args: argparse.Namespace) -> None:
    configs = load_config(args.config)
    if args.command == "generate":
        if args.days <= 0:
            raise _Usage("--days must be positive")
        end = _now(args.now)
        records = generate_records(
            configs,
            end - timedelta(days=args.days),
            end,
            seed=args.seed,
            runs_per_day=args.runs_per_day,
            scenarios=_parse_scenarios(args.scenario),
        )
        write_audit(args.out, records)
        print(f"wrote {len(records)} records for {len(configs)} agents to {args.out}")
    elif args.command == "rules":
        _emit(render_rules(configs), args.out)
    else:
        result = read_audit(args.audit)
        now = _now(args.now)
        if args.command == "metrics":
            _emit(render_metrics(result.records, result.skipped, configs, now), args.out)
        else:
            _emit(render_report(result.records, result.skipped, configs, now), args.out)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI; returns 0 on success and 2 on any usage, config, input or output error."""
    args = _build_parser().parse_args(argv)
    try:
        _run(args)
    except (_Usage, ConfigError, AuditLimitError, ValueError, OSError, OverflowError) as err:
        print(f"agent-slo: error: {err}", file=sys.stderr)
        return 2
    return 0
