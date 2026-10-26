# Troubleshooting

Start with `python3 scripts/preflight.py`. Fix every FAIL, then re-run `bash scripts/cloudshell_deploy.sh`. Every step is safe to re-run and skips what already exists.

## Getting help from Amazon Q Developer (AWS-only)

1. In CloudShell, from the repo folder, run `python3 scripts/support_bundle.py`. It writes `support-bundle-<time>.md` with secrets redacted and account and tenant names masked. Open it and check it before sharing.
2. Ask Amazon Q Developer, using either of these:
   - **In CloudShell:** run `q chat` from the repo folder. The project rules in `.amazonq/rules/` load automatically; if they don't, type `/context add AGENTS.md docs/TROUBLESHOOTING.md`. Then ask: *"Read support-bundle-….md and tell me the smallest fix for the first failure."* If `q` is not installed in your CloudShell, use the next option.
   - **In the console:** open the Amazon Q panel (the Q icon on the right edge of the AWS console), paste the bundle, and ask the same question.
3. Never paste passwords, client secrets, tokens or Secrets Manager values. The bundle never contains them.

## By stage

| Stage / symptom | Likely cause | Fix |
| --- | --- | --- |
| Preflight: `model MODEL_* … AccessDeniedException` | Model not enabled in Sydney, or an SCP blocks it | Bedrock console → Model access → enable it; ask your cloud team about SCPs |
| Preflight: `ValidationException … on-demand throughput isn't supported` | Base model ID used where an inference profile is required | Use the `apac.` or `global.` inference-profile ID from `aws bedrock list-inference-profiles` |
| Preflight: `boto3 knows AgentCore/S3 Vectors APIs` FAIL | CloudShell's boto3 is older than the APIs | `.venv/bin/pip install -U boto3 botocore` and run the scripts with `.venv/bin/python` |
| `setup_guardrail.py`: `ValidationException` on a filter | A guardrail option is unavailable in the region | Remove that entry from `guardrails/guardrail.json` and re-run |
| `setup_kb.py`: `s3vectors` errors / `Unknown service` | S3 Vectors not in region or old boto3 | Upgrade boto3. If the region lacks S3 Vectors, change storage to OpenSearch Serverless (ask Q to patch `create_kb`) and set `KB_SEARCH_TYPE=HYBRID` |
| `setup_kb.py`: `AccessDenied` creating the KB | IAM role not yet propagated | Wait 30 s and re-run |
| `sync_kb.py`: ingestion `FAILED` | Unsupported file, file > 50 MB, or sidecar JSON invalid | Check `failureReasons`; re-run `kb/make_metadata.py` |
| `setup_memory.py`: `ValidationException` on a strategy | Strategy name or shape changed in the API | Check `aws bedrock-agentcore-control create-memory help` and adjust `memoryStrategies` |
| `setup_gateway.py`: `skipping <env>` | URL is a placeholder or the secret doesn't exist | Fill `.env` / create the secret (README step 2), then re-run |
| `setup_gateway.py`: Lambda `InvalidParameterValueException` (role) | New IAM role not yet assumable | Re-run; the script already retries 6 times |
| `setup_gateway.py`: `create_gateway` parameter error | Gateway API evolved | `aws bedrock-agentcore-control create-gateway help`; patch the call |
| `setup_policy.py` fails | Policy API names differ | Create the engine in the console (AgentCore → Policy), paste `policy/cyberark_agent.cedar` with your Gateway ARN, attach in LOG_ONLY, and record `policy_engine_id`/`policy_engine_arn` in `outputs/resources.json` |
| `deploy_agent.sh`: `agentcore: command not found` | Toolkit not installed in the venv | `.venv/bin/pip install -r requirements-deploy.txt` and `export PATH=$PWD/.venv/bin:$PATH` |
| `deploy_agent.sh`: CodeBuild failure | Dependency could not install, or there's a syntax error | Open the CodeBuild project log in the console; the bundle shows the deploy log tail |
| `invoke.py`: `AccessDeniedException` | Your SSO role can't invoke the runtime | Attach `iam/engineer-invoke-policy.json` to your permission set |
| Answer: "Say which environment" | No `--env` and the prompt didn't name one | Add `--env pc-preprod` (or `pc-prod`/`onprem`) |
| Tool error: `Identity token request failed … 401` | Wrong ISPSS service user or secret | Check the user is an OAuth2 confidential client / service user; re-create the secret |
| Tool error: `PVWA logon failed … 403` / `ITATS` | On-prem vault user locked or wrong auth method | Check `auth_method` in the secret (`CyberArk`/`LDAP`); unlock the user in PrivateArk |
| Tool error: `URLError` / timeout for `onprem` | Lambda has no route to PVWA | Set `PAM_LAMBDA_SUBNET_IDS`/`SECURITY_GROUP_IDS`, check VPN/DX routes and the SG egress on 443 |
| Tool error: `CERTIFICATE_VERIFY_FAILED` (onprem) | PVWA uses an internal CA | Upload the CA PEM to S3, set `ONPREM_CA_BUNDLE_S3`, re-run `setup_gateway.py` |
| Tool result `APPROVAL_REQUIRED` | Working as designed | Approve: `python3 scripts/approve.py --ticket … --tool … --args '…'`, then give the agent the token |
| Tool result `APPROVAL_INVALID` | Arguments changed after approval, expired, or wrong environment | Approve again with the exact arguments the agent shows |
| `logs___query` returns nothing | Log group missing or named differently | Ship logs to `/cyberark/<environment>/<component>` (docs/deployment.md) or set `LOG_GROUP_PREFIX` |
| Answers say "not in my sources" | Knowledge base empty or filters too narrow | Load documents and run `sync_kb.py`; check the sidecar `product` values |

## Useful commands

```bash
agentcore status                                  # runtime state and log group
aws logs tail /aws/lambda/cyberark-engineer-pam-tools --since 30m --region ap-southeast-2
aws logs tail /aws/lambda/cyberark-engineer-logs-query --since 30m --region ap-southeast-2
python3 -m unittest discover -s tests -p "test_*.py"   # after any code change
```
