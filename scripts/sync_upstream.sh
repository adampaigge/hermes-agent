#!/usr/bin/env bash
# Rebase the telemetry strip onto a new upstream release, then audit. Never pushes.
#   scripts/sync_upstream.sh              # onto upstream/main
#   scripts/sync_upstream.sh v2026.11.1   # onto an upstream tag
set -euo pipefail

STRIP_BRANCH="${STRIP_BRANCH:-strip-telemetry}"
UPSTREAM_URL="https://github.com/NousResearch/hermes-agent.git"
target="${1:-upstream/main}"

cd "$(git rev-parse --show-toplevel)"
[ -z "$(git status --porcelain)" ] || { echo "working tree not clean" >&2; exit 2; }
git remote get-url upstream >/dev/null 2>&1 || git remote add upstream "$UPSTREAM_URL"
git fetch upstream --tags --quiet

git rev-parse --verify --quiet "$target^{commit}" >/dev/null || { echo "unknown target: $target" >&2; exit 2; }
work="strip-telemetry-sync-$(date +%Y%m%d)"
git branch -f "$work" "$STRIP_BRANCH"
git switch --quiet "$work"

echo "Rebasing $STRIP_BRANCH onto $target ($(git rev-parse --short "$target^{commit}"))..."
if ! git rebase "$target"; then
  echo
  echo "REBASE CONFLICT. Resolve, 'git rebase --continue', then rerun the audit:"
  echo "  python3 -I scripts/telemetry_audit.py --root ."
  echo "Likely files: setup.py tools_config.py shared_metrics_consent.py config_defaults.py pyproject.toml uv.lock"
  exit 3
fi

echo
echo "Auditing (this is the real check: the rebase cannot see new egress)..."
if python3 -I scripts/telemetry_audit.py --root .; then
  echo "Clean. Review 'git log $target..HEAD', then fast-forward $STRIP_BRANCH to $work."
else
  echo "Audit findings above. Read the upstream diff for each before accepting:"
  echo "  git diff ${STRIP_BRANCH}...$work -- <path>   (and 'git log $target' for new telemetry commits)"
  echo "When a finding is reviewed and fine: python3 -I scripts/telemetry_audit.py --root . --update-baseline"
  exit 1
fi
