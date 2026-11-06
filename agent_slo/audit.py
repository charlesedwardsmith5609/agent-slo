"""Read agent-harness audit JSONL and classify each run.

Synchronous and single-threaded; no shared state. The harness writes one JSON object per line
(see agent-harness ``AgentHarness._audit``). Only the fields needed for SLIs are kept: the
``data`` and ``reasoning`` payloads can hold sensitive findings and are never stored.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum, auto
from pathlib import Path

DEFAULT_MAX_LINE_BYTES = 1024 * 1024
DEFAULT_MAX_RECORDS = 1_000_000
MAX_AGENT_NAME_CHARS = 128
MAX_RUN_ID_CHARS = 64
_DRAIN_CHUNK = 64 * 1024
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_DRY_RUN_PREFIX = "Dry run"
_PRECALL_COST_MARKER = "Worst-case cost"

SKIP_REASONS = (
    "invalid_json",
    "not_object",
    "missing_field",
    "bad_type",
    "bad_timestamp",
    "bad_number",
    "too_long",
    "bad_encoding",
)


class Outcome(Enum):
    """What happened to one run."""

    PASS = auto()
    GATE_BLOCKED = auto()
    FAILED = auto()
    DRY_RUN = auto()


class AuditLimitError(Exception):
    """The audit file holds more usable records than the caller allowed."""


class RecordError(ValueError):
    """A record cannot be used; ``reason`` is one of SKIP_REASONS."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class RunRecord:
    """The SLI-relevant view of one audit line."""

    agent_name: str
    run_id: str
    timestamp: datetime  # tz-aware UTC, end of run, whole seconds
    outcome: Outcome
    cost_usd: float
    duration_s: float
    precall_cost_block: bool
    logged_max_cost_usd: float | None


@dataclass(frozen=True)
class AuditReadResult:
    """Usable records plus a count of skipped lines per reason (see ``SKIP_REASONS``)."""

    records: tuple[RunRecord, ...]
    skipped: Mapping[str, int]


def _number(raw: Mapping[str, object], key: str) -> float:
    if key not in raw:
        raise RecordError("missing_field")
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RecordError("bad_number")
    try:
        number = float(value)
    except OverflowError:
        raise RecordError("bad_number") from None
    if not math.isfinite(number) or number < 0:
        raise RecordError("bad_number")
    return number


def _text(
    raw: Mapping[str, object], key: str, *, allow_empty: bool, max_chars: int | None = None
) -> str:
    if key not in raw:
        raise RecordError("missing_field")
    value = raw[key]
    if not isinstance(value, str):
        raise RecordError("bad_type")
    if not value and not allow_empty:
        raise RecordError("bad_type")
    if max_chars is not None and len(value) > max_chars:
        raise RecordError("bad_type")
    return value


def _flag(raw: Mapping[str, object], key: str) -> bool:
    if key not in raw:
        raise RecordError("missing_field")
    value = raw[key]
    if not isinstance(value, bool):
        raise RecordError("bad_type")
    return value


def classify(raw: object) -> RunRecord:
    """Turn one decoded audit object into a RunRecord, or raise RecordError."""
    if not isinstance(raw, dict):
        raise RecordError("not_object")
    agent_name = _text(raw, "agent_name", allow_empty=False, max_chars=MAX_AGENT_NAME_CHARS)
    run_id = _text(raw, "run_id", allow_empty=False, max_chars=MAX_RUN_ID_CHARS)
    success = _flag(raw, "success")
    gate_passed = _flag(raw, "gate_passed")
    gate_reason = _text(raw, "gate_reason", allow_empty=True)
    cost = _number(raw, "cost_usd")
    duration = _number(raw, "duration_s")
    stamp = _text(raw, "timestamp", allow_empty=False)
    try:
        timestamp = datetime.strptime(stamp, _TIMESTAMP_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        raise RecordError("bad_timestamp") from None

    logged_max: float | None = None
    if "max_cost_usd" in raw:
        logged_max = _number(raw, "max_cost_usd")
        if logged_max <= 0:
            raise RecordError("bad_number")

    # A dry run is identified by its reason alone, checked first, whatever the other flags say.
    if gate_reason.startswith(_DRY_RUN_PREFIX):
        outcome = Outcome.DRY_RUN
    elif not success:
        outcome = Outcome.FAILED
    elif not gate_passed:
        outcome = Outcome.GATE_BLOCKED
    else:
        outcome = Outcome.PASS

    return RunRecord(
        agent_name=agent_name,
        run_id=run_id,
        timestamp=timestamp,
        outcome=outcome,
        cost_usd=cost,
        duration_s=duration,
        precall_cost_block=outcome is Outcome.FAILED and _PRECALL_COST_MARKER in gate_reason,
        logged_max_cost_usd=logged_max,
    )


def read_audit(
    path: str | Path,
    *,
    max_line_bytes: int = DEFAULT_MAX_LINE_BYTES,
    max_records: int = DEFAULT_MAX_RECORDS,
) -> AuditReadResult:
    """Read an audit file. Bad lines are counted by reason, never silently dropped.

    Raises FileNotFoundError (or another OSError) if the file cannot be opened, and
    AuditLimitError if more than ``max_records`` usable records are found (records are held in
    memory, so this bounds it).
    """
    records: list[RunRecord] = []
    skipped: dict[str, int] = {}

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    with open(path, "rb") as handle:
        while True:
            chunk = handle.readline(max_line_bytes + 1)
            if not chunk:
                break
            if len(chunk) > max_line_bytes and not chunk.endswith(b"\n"):
                # Oversize: discard the rest of the line in small pieces, never holding it.
                while True:
                    rest = handle.readline(_DRAIN_CHUNK)
                    if not rest or rest.endswith(b"\n"):
                        break
                skip("too_long")
                continue
            if not chunk.strip():
                continue
            try:
                text = chunk.decode("utf-8")
            except UnicodeDecodeError:
                skip("bad_encoding")
                continue
            try:
                decoded = json.loads(text)
            except (ValueError, RecursionError):  # JSONDecodeError, int digit limit, nesting
                skip("invalid_json")
                continue
            try:
                record = classify(decoded)
            except RecordError as err:
                skip(err.reason)
                continue
            if len(records) >= max_records:
                raise AuditLimitError(f"more than {max_records} records in {path}")
            records.append(record)
    return AuditReadResult(records=tuple(records), skipped=dict(skipped))
