"""Approval minting endpoint.

Called by your ITSM (e.g. a ServiceNow business rule or Flow on approval)
through API Gateway/Lambda function URL with IAM or mTLS auth. It mints an
approval token bound to the exact tool + arguments and posts it back to the
ticket, where the agent picks it up on the next turn.

Request body:
    {"ticket": "CHG0012345", "approver": "jane.smith", "tool": "pam___reconcile_account",
     "args": {"account_id": "12_34"}, "ttl_seconds": 3600}
"""
from __future__ import annotations

import json
import os

from safety import mint_approval  # vendored copy of agent/safety.py

APPROVAL_KEY_ARN = os.environ["APPROVAL_KEY_ARN"]
ALLOWED_TOOLS = {"pam___change_account", "pam___reconcile_account", "pam___add_safe",
                 "pam___add_safe_member", "pam___add_account"}
_key: bytes | None = None


def _signing_key() -> bytes:
    global _key
    if _key is None:
        import boto3
        raw = boto3.client("secretsmanager").get_secret_value(SecretId=APPROVAL_KEY_ARN)["SecretString"]
        _key = json.loads(raw)["key"].encode()
    return _key


def handler(event: dict, context) -> dict:
    body = json.loads(event.get("body") or "{}") if "body" in event else event
    tool = body.get("tool")
    if tool not in ALLOWED_TOOLS:
        return {"statusCode": 400, "body": json.dumps({"error": "tool not approvable"})}
    for f in ("ticket", "approver", "args"):
        if not body.get(f):
            return {"statusCode": 400, "body": json.dumps({"error": f"{f} required"})}
    ttl = min(int(body.get("ttl_seconds", 3600)), 8 * 3600)
    token = mint_approval(_signing_key(), tool, body["args"], body["ticket"], body["approver"], ttl)
    return {"statusCode": 200, "body": json.dumps({"approval_token": token, "expires_in": ttl})}
