import json
from pathlib import Path

import pytest

from agent_slo.audit import AuditLimitError, Outcome, read_audit

FIXTURE = Path(__file__).parent / "fixtures" / "harness_audit_sample.jsonl"


def good(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "agent_name": "a",
        "run_id": "r1",
        "success": True,
        "gate_passed": True,
        "gate_reason": "",
        "cost_usd": 0.1,
        "duration_s": 2.0,
        "timestamp": "2026-11-06T09:00:00Z",
        "data": {"x": 1},
        "reasoning": "r",
    }
    base.update(over)
    return base


def write(tmp_path: Path, *lines: str | bytes) -> Path:
    path = tmp_path / "audit.jsonl"
    path.write_bytes(b"".join(x if isinstance(x, bytes) else x.encode() for x in lines))
    return path


def line(obj: object) -> str:
    return json.dumps(obj) + "\n"


def test_fixture_outcomes() -> None:
    result = read_audit(FIXTURE)
    assert dict(result.skipped) == {}
    assert [r.outcome for r in result.records] == [
        Outcome.FAILED,
        Outcome.DRY_RUN,
        Outcome.PASS,
        Outcome.GATE_BLOCKED,
        Outcome.GATE_BLOCKED,
        Outcome.FAILED,
    ]
    flags = [r.precall_cost_block for r in result.records]
    assert flags == [False, False, False, False, False, True]


def test_sensitive_payload_not_retained() -> None:
    result = read_audit(FIXTURE)
    text = repr(result.records)
    assert "SECRET-FINDING-TEXT" not in text
    assert "SECRET-REASONING-TEXT" not in text


def test_timestamp_is_utc_aware() -> None:
    rec = read_audit(FIXTURE).records[2]
    assert rec.timestamp.utcoffset() is not None
    assert rec.timestamp.isoformat() == "2026-11-06T09:00:00+00:00"


def test_dry_run_prefix_wins_over_conflicting_flags(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        line(good(gate_passed=True, gate_reason="Dry run: x")),
        line(good(success=False, gate_passed=False, gate_reason="Dry run: x")),
    )
    assert [r.outcome for r in read_audit(path).records] == [Outcome.DRY_RUN] * 2


def test_logged_max_cost_read(tmp_path: Path) -> None:
    rec = read_audit(write(tmp_path, line(good(max_cost_usd=0.3)))).records[0]
    assert rec.logged_max_cost_usd == 0.3


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("{not json\n", "invalid_json"),
        ("[1, 2]\n", "not_object"),
        (line({k: v for k, v in good().items() if k != "cost_usd"}), "missing_field"),
        (line(good(agent_name=5)), "bad_type"),
        (line(good(success="yes")), "bad_type"),
        (line(good(timestamp="2026-11-06 09:00:00")), "bad_timestamp"),
        (line(good(cost_usd=-1)), "bad_number"),
        (line(good(cost_usd=True)), "bad_number"),
        (line(good(duration_s="2")), "bad_number"),
        ('{"cost_usd": NaN}\n', "invalid_json"),
        (line(good(max_cost_usd=0)), "bad_number"),
        ("[" * 100000 + "\n", "invalid_json"),
        (b"\xff\xfe\n", "bad_encoding"),
    ],
)
def test_bad_lines_skipped_with_reason(tmp_path: Path, raw: str | bytes, reason: str) -> None:
    nan_case = isinstance(raw, str) and "NaN" in raw
    path = write(tmp_path, raw, line(good(run_id="ok")))
    result = read_audit(path)
    assert [r.run_id for r in result.records] == ["ok"]
    if nan_case:  # json accepts NaN; either path must skip the line
        assert sum(result.skipped.values()) == 1
    else:
        assert dict(result.skipped) == {reason: 1}


def test_nan_and_infinity_values_rejected(tmp_path: Path) -> None:
    body = json.dumps(good()).replace('"cost_usd": 0.1', '"cost_usd": NaN')
    path = write(tmp_path, body + "\n", body.replace("NaN", "Infinity") + "\n")
    result = read_audit(path)
    assert result.records == ()
    assert dict(result.skipped) == {"bad_number": 2}


def test_oversize_line_skipped_and_next_line_read(tmp_path: Path) -> None:
    huge = line(good(reasoning="x" * 5000))
    path = write(tmp_path, huge, line(good(run_id="after")))
    result = read_audit(path, max_line_bytes=1000)
    assert [r.run_id for r in result.records] == ["after"]
    assert dict(result.skipped) == {"too_long": 1}


def test_oversize_final_line_without_newline(tmp_path: Path) -> None:
    path = write(tmp_path, line(good(run_id="first")), "x" * 5000)
    result = read_audit(path, max_line_bytes=1000)
    assert [r.run_id for r in result.records] == ["first"]
    assert dict(result.skipped) == {"too_long": 1}


def test_blank_lines_ignored_and_empty_file(tmp_path: Path) -> None:
    assert read_audit(write(tmp_path, "")).records == ()
    result = read_audit(write(tmp_path, "\n", "   \n", line(good())))
    assert len(result.records) == 1 and dict(result.skipped) == {}


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_audit(tmp_path / "nope.jsonl")


def test_huge_integers_skipped_not_fatal(tmp_path: Path) -> None:
    digits = "9" * 5000  # beyond Python's int-digit limit: json.loads raises ValueError
    too_big_for_float = "9" * 400  # parses as int, float() overflows
    base = json.dumps(good())
    path = write(
        tmp_path,
        base.replace('"cost_usd": 0.1', f'"cost_usd": {digits}') + "\n",
        base.replace('"cost_usd": 0.1', f'"cost_usd": {too_big_for_float}') + "\n",
        line(good(run_id="after")),
    )
    result = read_audit(path)
    assert [r.run_id for r in result.records] == ["after"]
    assert dict(result.skipped) == {"invalid_json": 1, "bad_number": 1}


def test_max_records_enforced(tmp_path: Path) -> None:
    path = write(tmp_path, *(line(good(run_id=str(n))) for n in range(3)))
    assert len(read_audit(path, max_records=3).records) == 3
    with pytest.raises(AuditLimitError):
        read_audit(path, max_records=2)


def test_oversized_identifiers_skipped(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        line(good(agent_name="n" * 129)),
        line(good(run_id="r" * 65)),
        line(good(agent_name="n" * 128, run_id="r" * 64)),
    )
    result = read_audit(path)
    assert len(result.records) == 1
    assert dict(result.skipped) == {"bad_type": 2}
