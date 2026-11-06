# Step 8: Dependabot for GitHub Actions and pip

## Scope
One file, `.github/dependabot.yml`, plus a README sentence. No code, no new dependencies.

## Design
Same shape as the sibling repos (agent-harness, slo-toolkit): version 2; ecosystems `pip` and `github-actions`, directory `/`,
weekly, `open-pull-requests-limit: 5`. Additions: a `commit-message` prefix (`ci` for actions, `deps` for pip) and labels
are NOT added (labels must already exist, otherwise Dependabot errors).

## What it will and will not cover (to be stated in a file comment and the README)
- github-actions: the two actions are pinned by full SHA with a version comment, which Dependabot updates.
- pip: reads `pyproject.toml`. The only runtime dependency is slo-toolkit as a git URL pinned to a commit; whether
  Dependabot proposes bumps for that is UNVERIFIED and bumping it would need the human's review anyway.
- NOT covered: pytest, ruff and mypy versions pinned inside `ci.yml`, and the promtool version and checksum there. Those
  are bumped by hand (ruff in particular changes `ruff format` output).

## Verification (revised after plan review)
No YAML parser exists in this environment (no ruby, yq or PyYAML; Node has none built in), so syntax and Dependabot schema
validity are UNVERIFIED locally; the file mirrors the sibling repos' configs key for key. Dependabot update jobs are not
ordinary workflow runs, so `gh run list` is not a valid check. After the push, report what GitHub shows: whether
Dependabot opens PRs (`gh pr list --author "app/dependabot"`), and say plainly if nothing can be confirmed yet.
`bash scripts/check.sh` after the change.

## Amendments after plan review (all four accepted)
1. A Dependabot PR for the slo-toolkit pin is a design change; the config comment and README say to check compatibility and
   update `REQUIREMENTS.md` with the pin. It is not ignored, because updates to the author's own library are worth seeing.
2. Remote behaviour is verified only as far as GitHub exposes it (see above).
3. YAML/schema validation is stated as unverified locally (see above).
4. The limit of 5 is per ecosystem, so up to 10 PRs can be open; this matches the sibling repos and is stated in the file.
