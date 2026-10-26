# AGENTS.md — briefing for AI assistants (Amazon Q Developer and others)

Read this first. It is everything you need to help the owner deploy, debug or change this repo without any other context.

## What this repo is

A CyberArk **troubleshooting** agent that runs entirely in the owner's AWS account (`ap-southeast-2`) on **Amazon Bedrock** + **Amazon Bedrock AgentCore**. **Constraint: AWS-only.** Use Bedrock, AgentCore and the AWS services they depend on: IAM, S3, S3 Vectors, Secrets Manager, CloudWatch, Lambda, Cognito and CodeBuild/ECR. Never propose third-party services or non-AWS AI providers. It diagnoses incidents in three CyberArk environments:

| `environment` | Meaning |
| --- | --- |
| `pc-preprod` | Privilege Cloud pre-production tenant |
| `pc-prod` | Privilege Cloud production tenant (every write needs approval) |
| `onprem` | Self-hosted PAM, Primary Vault + DR Vault (every write needs approval; DR is diagnose-only) |

Full design: `docs/design.md`. Deployment details: `docs/deployment.md`. Known errors and fixes: `docs/TROUBLESHOOTING.md`.

## How it is deployed (owner runs these in AWS CloudShell, Sydney)

```bash
cp .env.example .env              # fill REPLACE / YOUR_ values (no secrets in .env)
python3 scripts/preflight.py      # read-only checks; fix every FAIL first
bash scripts/cloudshell_deploy.sh # idempotent; logs to logs/deploy-*.log
python3 scripts/support_bundle.py # redacted diagnostics for Amazon Q Developer
python3 scripts/approve.py ...     # AWS-only approval of a write the agent proposed
```
Setup order inside `cloudshell_deploy.sh`: guardrail → knowledge bases → memory → gateway (Lambdas, Cognito, targets, Identity provider) → policy (LOG_ONLY) → agent runtime (`agentcore configure/launch`, built in CodeBuild) → grant runtime role. Resource IDs are saved to `outputs/resources.json` and mirrored into `.env`. Every step skips what already exists, so the fix-and-re-run loop is safe.

## File map

| Path | Purpose |
| --- | --- |
| `agent/app.py` | AgentCore Runtime entrypoint; builds supervisor; payload `{prompt, ticket_id, tier, environment, engineer_id}` |
| `agent/specialists.py` | Specialist agents exposed as tools; model IDs from env vars |
| `agent/catalogue.py` | **Single source of truth** for tools: name, read/write, owners, tier, approval |
| `agent/guard.py` | In-agent checks before every tool call (pure Python, unit-tested) |
| `agent/safety.py` | Secret redaction + HMAC approval tokens (vendored into Lambdas) |
| `agent/knowledge.py` | Bedrock Knowledge Base retrieval tools |
| `agent/prompts/*.md` | System prompts; `_shared_rules.md` is appended to all |
| `gateway/lambda/pam_tools/` | PVWA + CyberArk Identity tools for all environments; `tool_schema.json`, `identity_tool_schema.json` |
| `gateway/lambda/logs_query/` | CloudWatch Logs Insights over `/cyberark/<environment>/<component>` |
| `gateway/lambda/approvals/` | Mints approval tokens; called by `scripts/approve.py` (IAM-authorised), optionally by an ITSM |
| `policy/cyberark_agent.cedar` | AgentCore Policy; `GATEWAY_ARN` is substituted at deploy |
| `guardrails/guardrail.json` | Bedrock Guardrail definition |
| `scripts/` | Setup per service, deploy, invoke, preflight, support bundle |
| `evals/` | Golden cases + runner (red-team cases must all pass) |
| `tests/` | Standard-library unit tests: `python3 -m unittest discover -s tests -p "test_*.py"` |

## Invariants — do not break these

1. **No credential exposure.** Never add an endpoint that retrieves a password or secret: no `/Password/Retrieve` or `/Secret/Retrieve`, no delete of safes, accounts or platforms. A test checks for this.
2. **Every CyberArk tool takes a required `environment` argument as its first required field.**
3. **Adding or renaming a tool means changing four places together:** `agent/catalogue.py`, the Lambda `TOOLS` dict, the Lambda `tool_schema.json`, and `policy/cyberark_agent.cedar`. Tests fail if they drift.
4. **Writes in `pc-prod` and `onprem` always need an approval token**, enforced in the guard, the Lambda and Cedar.
5. **Tier-0 safes (`T0-*`) are human-only.**
6. **No secrets in git or `.env`.** CyberArk credentials live in Secrets Manager (`cyberark-engineer/<environment>`); AWS credentials come from the SSO session.
7. Tool names on the Gateway are `<target>___<tool>`, with three underscores.
8. Keep `agent/safety.py` dependency-free; it is zipped into the Lambdas.

## How to help debug

- Ask the owner for the output of `python3 scripts/support_bundle.py` (redacted). If they paste raw output, remind them not to include passwords or tokens.
- Match errors against `docs/TROUBLESHOOTING.md` first.
- The setup scripts call new AWS APIs: `bedrock-agentcore-control`, `s3vectors`, AgentCore Policy. If boto3 rejects a parameter name, check the current boto3 documentation for that client and patch the script. Do not guess; ask the owner to run `python3 -c "import boto3; print(boto3.__version__)"` and `aws <service> help`.
- Prefer small, re-runnable fixes. Every setup script is idempotent and keyed off `outputs/resources.json`.
- After any code change: run the unit tests, then re-run only the affected setup script.

## Models

`MODEL_SUPERVISOR`, `MODEL_FAST` and `MODEL_DEEP` are any Bedrock model or inference-profile IDs that support the Converse API with tool use. Claude models are the default design. Any other tool-capable Bedrock model can be swapped in through `.env` without code changes; `scripts/preflight.py` verifies tool use for each.
