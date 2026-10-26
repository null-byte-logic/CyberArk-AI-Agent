# Project rules for Amazon Q Developer

This repository is the CyberArk Engineer troubleshooting agent, deployed only on Amazon Bedrock, Amazon Bedrock AgentCore and the AWS services they depend on (IAM, S3, S3 Vectors, Secrets Manager, CloudWatch, Lambda, Cognito, CodeBuild/ECR). Account `058264514744`, region `ap-southeast-2`.

Before answering, read `AGENTS.md` (architecture, file map, invariants) and `docs/TROUBLESHOOTING.md` (known errors). The owner shares diagnostics as `support-bundle-*.md` files produced by `scripts/support_bundle.py`.

Rules:
- Keep the solution AWS-only. Do not suggest third-party services, SaaS, or non-AWS AI providers.
- Never ask for or print passwords, client secrets, tokens or Secrets Manager values.
- Never add password-retrieve, secret-retrieve or delete endpoints to the CyberArk tools.
- Every CyberArk tool keeps a required `environment` argument (`pc-preprod`, `pc-prod`, `onprem`).
- Tool changes must update `agent/catalogue.py`, the Lambda `TOOLS` dict, its `tool_schema.json` and `policy/cyberark_agent.cedar` together; run `python3 -m unittest discover -s tests -p "test_*.py"`.
- Prefer the smallest fix, then re-run only the affected script (they are idempotent).
