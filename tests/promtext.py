"""Tiny parser for the Prometheus text exposition format, for tests only."""

from __future__ import annotations

import re

_SAMPLE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})? (\S+)$")
_LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')

Key = tuple[str, tuple[tuple[str, str], ...]]


def _unescape(value: str) -> str:
    return re.sub(r"\\(.)", lambda m: "\n" if m.group(1) == "n" else m.group(1), value)


def parse(text: str) -> tuple[dict[Key, float], dict[str, str], dict[str, int]]:
    """Return (samples, types, help_line_counts)."""
    samples: dict[Key, float] = {}
    types: dict[str, str] = {}
    helps: dict[str, int] = {}
    for line in text.splitlines():
        if line.startswith("# TYPE "):
            _, _, name, kind = line.split(" ", 3)
            assert name not in types, f"duplicate TYPE for {name}"
            types[name] = kind
        elif line.startswith("# HELP "):
            name = line.split(" ", 3)[2]
            helps[name] = helps.get(name, 0) + 1
        elif line and not line.startswith("#"):
            match = _SAMPLE.match(line)
            assert match, f"unparseable line: {line!r}"
            name, raw_labels, value = match.groups()
            labels = tuple(sorted((k, _unescape(v)) for k, v in _LABEL.findall(raw_labels or "")))
            key = (name, labels)
            assert key not in samples, f"duplicate sample {key}"
            samples[key] = float(value)
    return samples, types, helps


def get(samples: dict[Key, float], name: str, **labels: str) -> float | None:
    return samples.get((name, tuple(sorted(labels.items()))))
