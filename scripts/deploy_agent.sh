#!/usr/bin/env bash
# Deploy the agent to AgentCore Runtime with the AgentCore starter toolkit.
# The toolkit builds the ARM64 container in CodeBuild, so no local Docker is needed
# (works in CloudShell). Runtime inbound auth stays AWS IAM (SigV4): engineers call
# it with their IAM Identity Center (SSO) session — see iam/engineer-invoke-policy.json.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a

for v in GATEWAY_MCP_URL MEMORY_ID KB_PRODUCT_ID KB_OPS_ID GUARDRAIL_ID MODEL_SUPERVISOR MODEL_FAST MODEL_DEEP; do
  if [[ -z "${!v:-}" || "${!v}" == REPLACE* ]]; then echo "Set $v in .env first"; exit 1; fi
done

agentcore configure \
  --entrypoint agent/app.py \
  --name cyberark_engineer \
  --region "$AWS_REGION" \
  --requirements-file requirements.txt \
  --disable-memory \
  --non-interactive

agentcore launch \
  --auto-update-on-conflict \
  --env AWS_REGION="$AWS_REGION" \
  --env GATEWAY_MCP_URL="$GATEWAY_MCP_URL" \
  --env GATEWAY_OAUTH_PROVIDER=gateway-oauth \
  --env GATEWAY_SCOPE=cyberark-agent/invoke \
  --env MEMORY_ID="$MEMORY_ID" \
  --env KB_PRODUCT_ID="$KB_PRODUCT_ID" \
  --env KB_OPS_ID="$KB_OPS_ID" \
  --env KB_SEARCH_TYPE=SEMANTIC \
  --env GUARDRAIL_ID="$GUARDRAIL_ID" \
  --env GUARDRAIL_VERSION="$GUARDRAIL_VERSION" \
  --env MODEL_SUPERVISOR="$MODEL_SUPERVISOR" \
  --env MODEL_FAST="$MODEL_FAST" \
  --env MODEL_DEEP="$MODEL_DEEP" \
  --env ENABLE_SERVICE_REQUESTS="${ENABLE_SERVICE_REQUESTS:-false}"

python3 scripts/grant_runtime_role.py
agentcore status
