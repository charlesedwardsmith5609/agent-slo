"""Atomic file output shared by the exporter, generator and CLI."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def write_atomic(path: str | Path, text: str) -> None:
    """Atomically replace ``path`` with ``text`` (mode 0644 so other services can read it).

    Writes to a temporary file in the same directory, flushes and fsyncs, then renames over the
    target, so a reader never sees a partial file and a failure leaves the old file intact.
    """
    target = Path(path)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".agent-slo-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o644)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
