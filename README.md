# CyberArk Engineer Agent

An agentic AI that works as a CyberArk engineer. It answers product questions across the CyberArk Identity Security Platform, troubleshoots L1–L4 incidents and fulfils service requests. It is built only on **Amazon Bedrock** (Claude models, Knowledge Bases, Guardrails) and **Amazon Bedrock AgentCore** (Runtime, Gateway, Identity, Memory, Policy, Observability, Evaluations).

**Scope: troubleshooting** these three CyberArk environments. The agent is deployed in account `058264514744`, region **ap-southeast-2 (Sydney)**, from **CloudShell** with your AWS SSO session.

| `environment` | What it is | Writes |
| --- | --- | --- |
| `pc-preprod` | Privilege Cloud pre-production tenant | L1 fixes allowed; CPM change/reconcile need approval |
| `pc-prod` | Privilege Cloud production tenant | Every write needs an approval token |
| `onprem` | Self-hosted PAM: Primary Vault + DR Vault (PVWA, CPM, PSM) | Every write needs an approval token; the DR Vault is diagnose-only |

Every CyberArk tool takes a required `environment` argument, and a ticket is pinned to one environment. Service-request tools such as new safes and onboarding exist in the code but are **off** (`ENABLE_SERVICE_REQUESTS=false` plus a Cedar `forbid`).

Design document: *CyberArk Engineer Agent on Amazon Bedrock AgentCore — Solution Design* (shared separately).

## How it works

```
Engineer / ITSM ──SigV4 (SSO)──► AgentCore Runtime
                                  └─ Supervisor (Strands)
                                      ├─ knowledge_advisor ─► Bedrock Knowledge Bases (product + ops)
                                      ├─ diagnostics_engineer ┐
                                      ├─ remediation_engineer ├─► AgentCore Gateway (MCP)
                                      ├─ service_request_fulfiller   ├─ Policy (Cedar) on every call
                                      └─ escalation_packager ┘       ├─ pam   → Lambda → Privilege Cloud API
                                                                     ├─ logs  → Lambda → CloudWatch Logs Insights
                                                                     └─ itsm  → OpenAPI → ServiceNow (optional)
             Memory: ticket context, environment facts, preferences, incident summaries
             Guardrails on every model call; Observability traces in CloudWatch
```

## Safety model

| Layer | Where | What it stops |
| --- | --- | --- |
| Tool catalogue | `agent/catalogue.py` | Each specialist only loads its own tools; the Knowledge Advisor has none |
| Agent guard | `agent/guard.py` | Writes without a ticket, without diagnosis, above tier, without approval, over the limit |
| AgentCore Policy | `policy/cyberark_agent.cedar` | Default deny; approval token required; Tier-0 safes and unknown permission profiles forbidden |
| Lambda checks | `gateway/lambda/pam_tools/handler.py` | Allow-listed endpoints only (no password retrieve/delete); `environment` required; HMAC approval bound to exact tool + arguments + environment; every write in `pc-prod`/`onprem` needs approval; service requests off; Tier-0 block; response redaction |
| Bedrock Guardrails | `guardrails/guardrail.json` | Prompt attacks, credential leakage, "bypass PAM controls" requests |
| Approvals | `gateway/lambda/approvals/handler.py` | Approval tokens are minted only by your ITSM approval flow |

## Repository layout

```
agent/              Runtime entrypoint, supervisor + specialists, prompts, guard, KB tools
gateway/lambda/     pam_tools, logs_query, approvals (Gateway Lambda targets)
gateway/openapi/    ITSM (ServiceNow) OpenAPI target
policy/             AgentCore Policy (Cedar)
guardrails/         Bedrock Guardrail definition
kb/                 Metadata sidecar generator for Knowledge Base documents
evals/              Golden set (Q&A, L1–L4, SR, red team) + runner
scripts/            CloudShell deployment, setup per service, invoke
iam/                Policy to attach to the engineers' SSO permission set
tests/              Unit tests (standard library only)
```

## Deploy (CloudShell, Sydney)

1. Upload the repo (or `git clone` it) into CloudShell in **ap-southeast-2**.
2. Create the environment secrets in CloudShell. Values never go in git. Use a separate service user per environment:
   ```bash
   aws secretsmanager create-secret --region ap-southeast-2 --name cyberark-engineer/pc-preprod \
     --secret-string '{"client_id":"<ISPSS service user>","client_secret":"<password>"}'
   aws secretsmanager create-secret --region ap-southeast-2 --name cyberark-engineer/pc-prod \
     --secret-string '{"client_id":"<ISPSS service user>","client_secret":"<password>"}'
   aws secretsmanager create-secret --region ap-southeast-2 --name cyberark-engineer/onprem \
     --secret-string '{"username":"<vault user>","password":"<password>","auth_method":"CyberArk"}'
   ```
   Least privilege for every service user: membership in the Auditors group, or equivalent, for read/health. On in-scope safes, grant only *List accounts*, *View audit log*, *View safe members* and *Initiate CPM account management operations*. Never grant *Retrieve accounts* or *Use accounts*. You can start with `pc-preprod` alone, since environments without a URL or secret are skipped.
3. `cp .env.example .env` and fill in the `REPLACE`/`YOUR_` values. These are the Claude inference-profile IDs enabled in Sydney, plus both tenants' subdomains and Identity tenant IDs and the on-prem PVWA URL. On-prem also needs private subnets that can reach PVWA over VPN or Direct Connect (see `docs/deployment.md`).
4. Run `bash scripts/cloudshell_deploy.sh`. It creates the Guardrail, KB bucket, S3 Vectors store, two Knowledge Bases, Memory, Lambdas, Cognito M2M auth, the Gateway and its targets, and the Policy engine in LOG_ONLY mode. It then builds and launches the agent through CodeBuild.
5. Load documents:
   ```bash
   # put files under kb-source/ using the layout in kb/make_metadata.py
   .venv/bin/python kb/make_metadata.py kb-source
   aws s3 sync kb-source s3://$KB_BUCKET/
   .venv/bin/python scripts/sync_kb.py
   ```
6. Try it: `.venv/bin/python scripts/invoke.py "Why is the CPM showing disconnected?" --env pc-preprod --ticket INC0000001 --tier L2`
7. Run the golden set: `.venv/bin/python evals/run_evals.py`. Red-team cases must be 100%.
8. After a week of clean traces, run `.venv/bin/python scripts/setup_policy.py --enforce`.

Engineer access is your existing SSO. Attach `iam/engineer-invoke-policy.json` to the IAM Identity Center permission set engineers use.

See `docs/deployment.md` for details, ITSM wiring and teardown.

## Develop

```bash
python3 -m unittest discover -s tests -p "test_*.py"
```
The tests use only the standard library, so they run anywhere. The agent code needs `requirements.txt` (Strands, bedrock-agentcore, mcp).

## Status and things to verify

- AgentCore Policy and S3 Vectors APIs are recent. If a setup call is rejected because a parameter name changed, the script prints the step. You can create that resource in the console instead and record its ID in `outputs/resources.json`.
- `agent/guard.py` uses the Strands hook events `BeforeToolCallEvent`/`AfterToolCallEvent`. Check them against your installed `strands-agents` version.
- The old knowledge base `W0CYQGHFSK` (its role and its bucket `cyberark-ai-agent-knowledge-base` no longer exist) is not touched. Delete it once the new KBs work.
