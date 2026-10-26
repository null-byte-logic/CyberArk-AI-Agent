#!/usr/bin/env bash
# One-shot deployment from AWS CloudShell (Sydney), using your SSO session.
# Creates billable resources. Safe to re-run: every step reuses what exists.
set -euo pipefail
cd "$(dirname "$0")/.."

[[ -f .env ]] || { cp .env.example .env; echo "Created .env — fill in the REPLACE values, then re-run."; exit 1; }
set -a; source .env; set +a

echo "Account: $(aws sts get-caller-identity --query Account --output text)  Region: $AWS_REGION"
if [[ "$(aws sts get-caller-identity --query Account --output text)" != "$AWS_ACCOUNT_ID" ]]; then
  echo "Your session is not in account $AWS_ACCOUNT_ID"; exit 1
fi

python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements-deploy.txt

PY=.venv/bin/python
$PY scripts/setup_guardrail.py
$PY scripts/setup_kb.py
$PY scripts/setup_memory.py
$PY scripts/setup_gateway.py
$PY scripts/setup_policy.py            # LOG_ONLY first; re-run with --enforce later

PATH="$PWD/.venv/bin:$PATH" bash scripts/deploy_agent.sh

echo
echo "Deployed. Try:  .venv/bin/python scripts/invoke.py \"How does CPM reconcile work in Privilege Cloud?\""
