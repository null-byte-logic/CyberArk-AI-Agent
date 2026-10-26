#!/usr/bin/env bash
# One-shot deployment from AWS CloudShell (Sydney), using your SSO session.
# Creates billable resources. Safe to re-run: every step reuses what exists.
# Output is also written to logs/deploy-<time>.log (used by scripts/support_bundle.py).
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p logs
LOG="logs/deploy-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1
trap 'echo; echo "Deployment stopped. Run: python3 scripts/support_bundle.py  and ask Amazon Q Developer (docs/TROUBLESHOOTING.md)."' ERR

[[ -f .env ]] || { cp .env.example .env; echo "Created .env — fill in the REPLACE/YOUR_ values, then re-run."; exit 1; }
set -a; source .env; set +a

python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements-deploy.txt
PY=.venv/bin/python
export PATH="$PWD/.venv/bin:$PATH"

echo "==> Preflight"
$PY scripts/preflight.py

$PY scripts/setup_guardrail.py
$PY scripts/setup_kb.py
$PY scripts/setup_memory.py
$PY scripts/setup_gateway.py
$PY scripts/setup_policy.py            # LOG_ONLY first; re-run with --enforce later

bash scripts/deploy_agent.sh

echo
echo "Deployed. Log: $LOG"
echo "Try:  .venv/bin/python scripts/invoke.py \"Why would a CPM show disconnected?\" --env pc-preprod --ticket TEST001 --tier L2"
