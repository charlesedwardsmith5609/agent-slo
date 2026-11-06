# Step 7: GitHub Actions CI running check.sh with promtool

Human instruction: add a CI workflow that runs `scripts/check.sh` with promtool. Continue without asking unless blocked.

## Scope
`.github/workflows/ci.yml` and one small test change. No application code. No new Python dependencies. The only
new third-party components are the two GitHub actions below and the promtool binary, all pinned.

## Workflow
- Triggers: `push` to `main` and `pull_request`. `permissions: contents: read`. `concurrency` cancels superseded
  runs per ref. `timeout-minutes: 15`.
- One job on `ubuntu-latest`, matrix Python `3.11` (the minimum in pyproject) and `3.14` (the version used in development).
- Actions pinned by full commit SHA with the tag in a comment: `actions/checkout` v7.0.1
  (3d3c42e5aac5ba805825da76410c181273ba90b1), `actions/setup-python` v7.1.0
  (6d4fd22b20465ccc0f7354476588e8df7c9ca0e0). `persist-credentials: false` on checkout.
- Steps: checkout; setup-python; create `.venv` (check.sh expects it) and `pip install -e . pytest==9.2.0
  ruff==0.17.4 mypy==2.4.1` (tool versions pinned because `ruff format --check` output depends on the version);
  install promtool 3.16.0 from the official release tarball, verifying a SHA-256 hard-coded in the workflow
  (68727009472a2c02e1ff7f062fd5b3e637285bffc8fc2d340c76fb449d6e0f34), extracting only `promtool` into a runner-temp
  directory, added to `GITHUB_PATH`; print `promtool --version`; run `bash scripts/check.sh` with `REQUIRE_PROMTOOL=1`.
- Secrets: none used. `pip install -e .` fetches the public slo-toolkit repo over HTTPS at its pinned commit.

## Test change
`tests/test_promtool.py` skips when promtool is missing. In CI that would hide a broken install, so when
`REQUIRE_PROMTOOL` is set and promtool is missing, collection fails instead of skipping. A test covers both modes
by running pytest in a subprocess with a timeout.

## Verification
Cannot run Actions locally. Instead replay the workflow commands in a clean temp copy of the working tree (fresh venv,
pinned tool versions, promtool installed from the verified tarball) and run `check.sh` there; confirm the promtool tests
run and are not skipped; confirm `REQUIRE_PROMTOOL=1` without promtool fails. Real CI behaviour is UNVERIFIED until the
workflow runs on GitHub, which needs a push (needs the human's approval).

## Amendments after plan review (all three accepted)
1. promtool asset is `prometheus-3.16.0.linux-amd64.tar.gz` from
   `https://github.com/prometheus/prometheus/releases/download/v3.16.0/`, the same asset the pinned SHA-256 belongs to.
   After install the step asserts `promtool --version` starts with `promtool, version 3.16.0 ` (fails otherwise).
2. The negative test runs pytest in a subprocess with an empty `PATH` directory and a temporary `HOME`, so neither the
   PATH lookup nor `~/.local/bin` can find promtool.
3. `bash scripts/check.sh` is run after each file change in this step and all four stages are reported.

## Found while replaying the workflow (2026-11-06)
`pip install -e .` failed on a clean checkout: pyproject had no `[build-system]` and setuptools refused to guess among the
top-level directories. Fixed by adding `[build-system]` (setuptools>=68, as in the sibling repos) and
`[tool.setuptools.packages.find] include = ["agent_slo*"]`. The README's install command was wrong before this fix.
Replay result after the fix: fresh venv, pinned tools, promtool from the verified tarball, `REQUIRE_PROMTOOL=1`:
ruff check, ruff format, mypy and pytest (155 tests, none skipped) all pass. Unverified: Python 3.11 (not available
locally; syntax checked with `ast.parse(feature_version=(3, 11))`) and the workflow on GitHub itself.

## Change after first CI run
The runner is pinned to `ubuntu-24.04` instead of `ubuntu-latest` (GitHub announced the `ubuntu-latest` label will move
to Ubuntu 26), so the OS no longer changes without a commit.
