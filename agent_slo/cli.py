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

    gen = sub.add_parser("generate", help="write a synthetic audit log for demos")
    gen.add_argument("--config", required=True)
    gen.add_argument("--out", required=True)
    gen.add_argument("--days", type=int, default=30)
    gen.add_argument("--runs-per-day", type=int, default=40)
    gen.add_argument("--seed", type=int, default=1)
    gen.add_argument("--now", help="end of the span (ISO 8601 with timezone); default: now")
    gen.add_argument(
        "--scenario",
        action="append",
        default=[],
        metavar="AGENT=NAME",
        help=f"one of {', '.join(SCENARIOS)}; unnamed agents are healthy; repeatable",
    )

    met = sub.add_parser("metrics", help="write Prometheus textfile metrics")
    met.add_argument("--config", required=True)
    met.add_argument("--audit", required=True)
    met.add_argument("--out", required=True)
    met.add_argument("--now")

    rul = sub.add_parser("rules", help="print or write Prometheus rules")
    rul.add_argument("--config", required=True)
    rul.add_argument("--out")

    rep = sub.add_parser("report", help="print or write the weekly Markdown report")
    rep.add_argument("--config", required=True)
    rep.add_argument("--audit", required=True)
    rep.add_argument("--out")
    rep.add_argument("--now")
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
    args = _build_parser().parse_args(argv)
    try:
        _run(args)
    except (_Usage, ConfigError, AuditLimitError, ValueError, OSError, OverflowError) as err:
        print(f"agent-slo: error: {err}", file=sys.stderr)
        return 2
    return 0
