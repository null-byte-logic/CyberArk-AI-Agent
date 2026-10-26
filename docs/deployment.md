# Deployment guide

## What gets created (all in ap-southeast-2)

| Resource | Name | Script |
| --- | --- | --- |
| Bedrock Guardrail | `cyberark-engineer-guardrail` | `setup_guardrail.py` |
| S3 bucket (SSE-KMS, versioned, public access blocked) | `cyberark-engineer-kb-058264514744` | `setup_kb.py` |
| S3 Vectors bucket + 2 indexes | `cyberark-engineer-vectors-058264514744` | `setup_kb.py` |
| Knowledge Bases | `kb-cyberark-product`, `kb-cyberark-ops` | `setup_kb.py` |
| AgentCore Memory | `cyberark_engineer_memory` | `setup_memory.py` |
| Secrets Manager | `cyberark-engineer/approval-signing-key` | `setup_gateway.py` |
| Lambda | `cyberark-engineer-pam-tools`, `-logs-query`, `-approvals` | `setup_gateway.py` |
| Cognito user pool (Gateway M2M auth) | `cyberark-engineer-gateway` | `setup_gateway.py` |
| AgentCore Identity OAuth2 provider | `gateway-oauth` | `setup_gateway.py` |
| AgentCore Gateway + targets `pam`, `logs` | `cyberark-engineer-gateway` | `setup_gateway.py` |
| AgentCore Policy engine | `cyberark_engineer_policy` | `setup_policy.py` |
| AgentCore Runtime | `cyberark_engineer` | `deploy_agent.sh` |

Resource IDs are recorded in `outputs/resources.json` (git-ignored) and copied into `.env`.

## Knowledge base layout

```
s3://cyberark-engineer-kb-058264514744/
  product/<product>/<version>/<doc_type>/<file>          # vendor docs
  internal/v0.1/bau/<product>/<doc_type>/<file>          # your runbooks, SOPs, standards, KEDB, RCAs
```
`product` is one of pam-selfhosted, privilege-cloud, epm, identity, conjur, secrets-hub, sia, sca or general. `doc_type` is one of guide, api, release-notes, known-issues, runbook, sop, standard, kedb or rca.

Write one resolved ticket per KEDB file, with the exact error code in the first line. The Diagnostics agent searches the KEDB first.

Before loading vendor PDFs, check that your CyberArk agreement allows them in an internal index.

## Environments and connectivity

| Environment | API path from the PAM Lambda | Auth |
| --- | --- | --- |
| `pc-preprod` | Internet → `https://<preprod>.privilegecloud.cyberark.cloud/PasswordVault/API` | ISPSS service user, OAuth2 client credentials |
| `pc-prod` | Internet → `https://<prod>.privilegecloud.cyberark.cloud/PasswordVault/API` | ISPSS service user, OAuth2 client credentials |
| `onprem` | VPC → VPN / Direct Connect → PVWA `/PasswordVault/API` | Vault user (CyberArk or LDAP auth), session token cached ~15 min |

For on-prem, set `PAM_LAMBDA_SUBNET_IDS` and `PAM_LAMBDA_SECURITY_GROUP_IDS` to private subnets that route to PVWA on 443 **and** have a NAT gateway. The same function also calls Privilege Cloud, Secrets Manager and CloudWatch. If PVWA uses an internal CA, upload the PEM to S3 and set `ONPREM_CA_BUNDLE_S3`. Allow the subnets' egress IPs on the PVWA load balancer and in Privilege Cloud's allowed-IP list if you use one.

The DR Vault has no REST API and is passive, so the agent only diagnoses it through logs. Failover stays a human runbook.

## CyberArk component logs

`logs___query` reads CloudWatch log groups named `/cyberark/<environment>/<component>`. Ship them with the CloudWatch agent:

| Log group | Source host | Files |
| --- | --- | --- |
| `/cyberark/onprem/vault` | Primary Vault | `Server\Logs\ITALog.log`, `trace.d*` |
| `/cyberark/onprem/dr-vault` | DR Vault | `Server\Logs\ITALog.log` |
| `/cyberark/onprem/padr` | DR Vault | `PADR\Logs\PADR.log` |
| `/cyberark/onprem/pvwa` | PVWA servers | `CyberArk.WebApplication.log`, `CyberArk.WebConsole.log` |
| `/cyberark/onprem/cpm` | CPM servers | `Logs\pm.log`, `pm_error.log`, `Casos.Error.log` |
| `/cyberark/onprem/psm` | PSM servers | `Logs\PSMConsole.log`, `PSMTrace.log` |
| `/cyberark/pc-<env>/connector` | Privilege Cloud connector servers | CPM and PSM logs as above, plus `Connector Management Agent` logs |

Set `LOG_GROUP_PREFIX` on the Lambda if your names differ. Mask passwords at the agent if any trace level logs them; the Lambda redacts too.

## Approvals (AWS-only)

Approvals need no ITSM. The agent replies `APPROVAL_REQUIRED` with the exact tool and arguments. An engineer whose IAM role may invoke the `cyberark-engineer-approvals` Lambda runs `scripts/approve.py`, which records their identity as approver and returns a token bound to those exact arguments and environment. CloudTrail records each approval. Restrict who can approve through that IAM permission, for example a separate "PAM approver" permission set in IAM Identity Center.

## ITSM (optional, off by default; not needed for an AWS-only deployment)

1. Upload `gateway/openapi/itsm-servicenow.yaml` (with your instance URL) to the KB bucket under `config/`.
2. Create an AgentCore Identity OAuth2 credential provider for ServiceNow (client credentials).
3. Add a Gateway target named `itsm` with an OpenAPI schema from S3 and that credential provider.
4. In ServiceNow, when an approval for an agent plan is approved, call the approvals function URL (IAM auth) with `{ticket, approver, tool, args}`. Then write the returned `approval_token` to the ticket's work notes. The agent picks it up on the next turn.

## Engineer access (SSO)

Runtime inbound auth is AWS IAM, so engineers use their existing IAM Identity Center session. Add `iam/engineer-invoke-policy.json` as an inline policy on their permission set. A chat portal or Teams bot calls the same `InvokeAgentRuntime` API with its own role.

## Teardown

Delete in reverse order: Runtime (`agentcore destroy`), Policy engine, Gateway targets and Gateway, Identity provider `gateway-oauth`, Cognito pool, Lambdas, Memory, Knowledge Bases, S3 Vectors indexes and bucket, KB bucket and Guardrail.
