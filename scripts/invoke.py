"""Call the deployed agent with your SSO (IAM) credentials.

    python scripts/invoke.py "Why is account 12_34 failing CPM change?" --env pc-prod --ticket INC0012345 --tier L2
"""
from __future__ import annotations

import argparse
import json
import uuid

import yaml

from common import ROOT, load_env, session


def main() -> None:
    load_env()
    p = argparse.ArgumentParser()
    p.add_argument("prompt")
    p.add_argument("--ticket", default="")
    p.add_argument("--tier", default="L1", choices=["L1", "L2", "L3", "L4", "SR"])
    p.add_argument("--env", default="", choices=["", "pc-preprod", "pc-prod", "onprem"],
                   help="pin the CyberArk environment")
    p.add_argument("--engineer", default="cli-user")
    p.add_argument("--session", default=str(uuid.uuid4()) + "-session")
    a = p.parse_args()

    cfg = yaml.safe_load((ROOT / ".bedrock_agentcore.yaml").read_text())
    agent = cfg["agents"][cfg.get("default_agent", "cyberark_engineer")]
    arn = agent["bedrock_agentcore"]["agent_arn"]

    client = session().client("bedrock-agentcore")
    resp = client.invoke_agent_runtime(
        agentRuntimeArn=arn, runtimeSessionId=a.session,
        payload=json.dumps({"prompt": a.prompt, "ticket_id": a.ticket, "tier": a.tier,
                            "engineer_id": a.engineer, "environment": a.env}).encode())
    body = json.loads(resp["response"].read())
    print(body.get("answer", body))
    if body.get("blocked_calls"):
        print("\nBlocked tool calls:", *body["blocked_calls"], sep="\n  ")


if __name__ == "__main__":
    main()
