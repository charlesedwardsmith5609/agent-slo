#!/usr/bin/env bash
# Python checks: ruff lint, ruff format, mypy, pytest. Runs in the Python project's directory.
set -u
fail=0
stage() { printf '\n== python: %s\n' "$1"; }
bin=.venv/bin

if [ ! -x "$bin/python" ]; then
  echo "FAIL: .venv missing. Recreate: python3 -m venv .venv && .venv/bin/pip install pytest ruff mypy"
  exit 1
fi

pkgs=$(find . -maxdepth 2 -name __init__.py -not -path './.venv/*' -not -path './tests/*' -printf '%h\n' | sed 's|^\./||' | sort -u)

stage "ruff check"
if "$bin/ruff" check .; then echo "PASS"; else echo "FAIL"; fail=1; fi

stage "ruff format"
if "$bin/ruff" format --check .; then echo "PASS"; else echo "FAIL"; echo "Fix: .venv/bin/ruff format ."; fail=1; fi

stage "mypy"
# shellcheck disable=SC2086
if "$bin/mypy" $pkgs tests; then echo "PASS"; else echo "FAIL"; fail=1; fi

stage "pytest"
"$bin/pytest" -q
rc=$?
if [ "$rc" -eq 0 ]; then echo "PASS"
elif [ "$rc" -eq 5 ]; then echo "PASS (no tests yet)"
else echo "FAIL"; fail=1
fi

exit "$fail"
