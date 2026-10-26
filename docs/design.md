# CyberArk Engineer Agent — Solution Design

Repository copy of the design, kept in sync with the code. Where this document and the code disagree, the code wins: `agent/catalogue.py` (tools), `policy/cyberark_agent.cedar` (authorisation) and `agent/prompts/` (behaviour).

## 1. Summary

The agent is a **CyberArk troubleshooting engineer** for three environments, built only on **Amazon Bedrock** and **Amazon Bedrock AgentCore** in account `058264514744`, region `ap-southeast-2`.

| `environment` | What it is | Write rule |
| --- | --- | --- |
| `pc-preprod` | Privilege Cloud pre-production tenant | L1 fixes allowed; CPM change/reconcile need approval |
| `pc-prod` | Privilege Cloud production tenant | Every write needs an approval token |
| `onprem` | Self-hosted PAM: Primary Vault + DR Vault (PVWA, CPM, PSM) | Every write needs an approval token; DR Vault is diagnose-only, failover is human-only |

What it does:
- Answers CyberArk product and configuration questions with citations from two Knowledge Bases.
- Diagnoses L1–L4 incidents with read-only tools: account/user state, component health, logs and the known-error database.
- Executes a small catalogue of fixes, approval-gated outside pre-prod.
- Packages L3/L4 incidents for engineers or CyberArk Support.

Service requests (new safes, onboarding) are implemented but **disabled**: `ENABLE_SERVICE_REQUESTS=false` plus a Cedar `forbid`.

Guiding rule: **the agent never sees a privileged credential and never makes an unapproved change in production.**

## 2. Architecture

```
Engineer (IAM Identity Center / SSO)
   │ SigV4 InvokeAgentRuntime
   ▼
AgentCore Runtime ── container built by CodeBuild from agent/
   └─ Supervisor (Strands Agents SDK, Claude on Bedrock, Bedrock Guardrail)
        ├─ knowledge_advisor      → Bedrock Knowledge Bases (kb-cyberark-product, kb-cyberark-ops)
        ├─ diagnostics_engineer   ┐
        ├─ remediation_engineer   ├→ AgentCore Gateway (MCP, Cognito M2M JWT in, IAM role out)
        ├─ escalation_packager    ┘     │  AgentCore Policy (Cedar) evaluates every tool call
        └─ (service_request_fulfiller, off)
                                        ├─ target pam      → Lambda pam_tools  → PVWA REST API per environment
                                        ├─ target identity → Lambda pam_tools  → CyberArk Identity (PC tenants)
                                        ├─ target logs     → Lambda logs_query → CloudWatch Logs Insights
                                        └─ target itsm     → OpenAPI → ServiceNow (optional)
AgentCore Memory: per-ticket session, environment facts, engineer preferences, incident summaries
AgentCore Observability: traces to CloudWatch. AgentCore Evaluations + evals/run_evals.py: quality gates
Approvals: scripts/approve.py (engineer's IAM/SSO) → Lambda approvals → HMAC token bound to tool + args + environment (ITSM optional)
```

| Need | Service | Code |
| --- | --- | --- |
| Reasoning | Amazon Bedrock (Claude inference profiles, configurable) | `agent/specialists.py` |
| Content safety, secret masking | Bedrock Guardrails | `guardrails/guardrail.json` |
| Grounded answers | Bedrock Knowledge Bases on S3 Vectors | `scripts/setup_kb.py`, `agent/knowledge.py` |
| Hosting | AgentCore Runtime | `agent/app.py`, `scripts/deploy_agent.sh` |
| Tools | AgentCore Gateway | `scripts/setup_gateway.py`, `gateway/` |
| Outbound auth | AgentCore Identity (OAuth2 provider `gateway-oauth`) | `scripts/setup_gateway.py` |
| Tool authorisation | AgentCore Policy | `policy/cyberark_agent.cedar`, `scripts/setup_policy.py` |
| Context | AgentCore Memory | `scripts/setup_memory.py` |

## 3. Agents

| Agent | Role | Tools | Model env var |
| --- | --- | --- | --- |
| Supervisor | Classify (question / incident / SR), environment, tier; route; write ticket notes | specialists, `itsm___get_ticket`, `itsm___update_ticket` | `MODEL_SUPERVISOR` |
| Knowledge Advisor | Product Q&A with citations | KB search only, no Gateway tools | `MODEL_FAST` |
| Diagnostics Engineer | Read-only investigation, hypothesis, recommended action | all read tools, KB | `MODEL_DEEP` |
| Remediation Engineer | Execute one catalogue fix, verify | verify / change / reconcile / activate / unlock | `MODEL_SUPERVISOR` |
| Escalation Packager | L3/L4 hand-off and vendor case text | read tools, KB, ticket update | `MODEL_DEEP` |

Remediation catalogue (the only fixes that can be executed): `REACTIVATE_VAULT_USER`, `UNLOCK_IDENTITY_USER`, `CPM_VERIFY`, `CPM_RECONCILE` (approval), `CPM_CHANGE` (approval). In `pc-prod` and `onprem` all of them need approval.

## 4. Tiers

| Tier | Examples | Agent behaviour |
| --- | --- | --- |
| L1 | Suspended vault user, Identity lockout, how-to | Diagnose, apply L1 fix (approval in prod) |
| L2 | CPM verify/change/reconcile failures, PSM launch errors, connector offline | Diagnose, apply catalogue fix with approval, else escalate |
| L3 | Many accounts failing after policy change, DR replication lag, LDAP/PVWA performance | Diagnose and recommend; engineer executes |
| L4 | Vault crash, failed upgrade, data corruption, security events | Vendor case pack immediately; no actions |

## 5. Knowledge

Bucket `cyberark-engineer-kb-058264514744`:
- `product/<product>/<version>/<doc_type>/` → `kb-cyberark-product`
- `internal/v0.1/bau/<product>/<doc_type>/` → `kb-cyberark-ops`

Metadata sidecars (`kb/make_metadata.py`) carry `product`, `version`, `doc_type`, `source` and `component`, which the agent uses as filters. Embeddings are Titan Text Embeddings V2 with 1024 dimensions, using hierarchical chunking (1500/300 tokens). The known-error database is one resolved ticket per file under `doc_type=kedb`.

## 6. Security layers

1. **Catalogue scoping**: each specialist only receives its tools.
2. **Agent guard** (`agent/guard.py`) blocks a write when any of these is missing or exceeded: environment, ticket, diagnosis, tier limit, approval, or the write cap.
3. **AgentCore Policy** (Cedar) works by default deny. It requires an approval token for change/reconcile and for any write in prod, and it forbids service-request tools and Tier-0 (`T0-*`) safes.
4. **Lambda checks** apply the same rules again in code. They verify approval with an HMAC over tool + exact args, including environment. They also redact responses, and there is no password-retrieve or delete endpoint at all.
5. **Bedrock Guardrail** covers prompt attacks, credential masking and a denied topic for bypassing PAM controls.
6. **Least-privilege CyberArk service users**, one per environment: list, audit and initiate CPM operations only, never retrieve or use.

## 7. Roadmap

| Phase | Scope | Exit criteria |
| --- | --- | --- |
| MVP | `pc-preprod` only, Q&A + diagnostics, Policy LOG_ONLY | Red-team evals 100%; engineers find ≥ 80% of diagnoses useful |
| 2 | Add `pc-prod` (read), approvals via ITSM, Policy ENFORCE | No Policy bypasses in 2 weeks of traces |
| 3 | Add `onprem` (VPN/DX connectivity, log shipping incl. PADR) | DR and Vault cases pass |
| 4 | Approved L2 fixes in prod, episodic memory promoted to KB | Change board sign-off |

## 8. Open decisions

- Approval channel: AWS-only `scripts/approve.py` by default; ITSM integration optional later.
- Where CyberArk component logs land (CloudWatch assumed; log group names in `docs/deployment.md`).
- Licence position on indexing CyberArk documentation.
- Network path from AWS to on-prem PVWA.
