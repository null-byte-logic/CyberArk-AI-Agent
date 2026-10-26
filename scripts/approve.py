"""Approve a planned write action without any ITSM: AWS-only approval flow.

The agent answers APPROVAL_REQUIRED with the exact tool and arguments. An
authorised engineer runs this script with their own SSO session; it invokes the
approvals Lambda (IAM-authorised) and prints a token bound to that exact tool,
arguments and environment. Paste the token back to the agent.

    python3 scripts/approve.py --ticket INC0012345 --tool pam___reconcile_account \
        --args '{"environment": "pc-prod", "account_id": "12_34"}'

Who may approve is controlled by IAM: only principals allowed
lambda:InvokeFunction on cyberark-engineer-approvals can mint tokens. Every
approval is recorded in CloudTrail and the Lambda's CloudWatch log.
"""
from __future__ import annotations

import argparse
import json

from common import load_env, outputs, session


def main() -> None:
    load_env()
    p = argparse.ArgumentParser()
    p.add_argument("--ticket", required=True)
    p.add_argument("--tool", required=True)
    p.add_argument("--args", required=True, help="JSON object, exactly as the agent proposed it")
    p.add_argument("--ttl", type=int, default=3600)
    a = p.parse_args()

    args = json.loads(a.args)
    args.pop("approval_token", None)
    sess = session()
    approver = sess.client("sts").get_caller_identity()["Arn"]
    print("Approving as:", approver)
    print("Ticket:", a.ticket, "\nTool:", a.tool, "\nArguments:", json.dumps(args, indent=2))
    if input("Type APPROVE to continue: ").strip() != "APPROVE":
        raise SystemExit("cancelled")

    fn = outputs().get("lambda_arns", {}).get("approvals", "cyberark-engineer-approvals")
    resp = sess.client("lambda").invoke(FunctionName=fn, Payload=json.dumps({
        "ticket": a.ticket, "approver": approver, "tool": a.tool, "args": args,
        "ttl_seconds": a.ttl}).encode())
    body = json.loads(json.loads(resp["Payload"].read())["body"])
    if "approval_token" not in body:
        raise SystemExit(f"Approval failed: {body}")
    print(f"\nApproval token (valid {body['expires_in']}s, only for these exact arguments):\n")
    print(body["approval_token"])


if __name__ == "__main__":
    main()
