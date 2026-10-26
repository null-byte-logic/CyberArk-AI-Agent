"""Add the permissions the agent needs beyond the toolkit's default execution role:
Knowledge Base retrieve, Guardrail apply, Memory, and AgentCore Identity token fetch."""
from __future__ import annotations

import json

import yaml

from common import ROOT, load_env, outputs, session, step


def main() -> None:
    env = load_env()
    region, account = env["AWS_REGION"], env["AWS_ACCOUNT_ID"]
    cfg = yaml.safe_load((ROOT / ".bedrock_agentcore.yaml").read_text())
    agent_cfg = cfg["agents"][cfg.get("default_agent", "cyberark_engineer")]
    role_arn = agent_cfg["aws"]["execution_role"]
    role_name = role_arn.split("/")[-1]
    out = outputs()
    policy = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": ["bedrock:Retrieve"],
         "Resource": [f"arn:aws:bedrock:{region}:{account}:knowledge-base/{out['kb_product_id']}",
                      f"arn:aws:bedrock:{region}:{account}:knowledge-base/{out['kb_ops_id']}"]},
        {"Effect": "Allow", "Action": ["bedrock:ApplyGuardrail"], "Resource": out["guardrail_arn"]},
        {"Effect": "Allow", "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
         "Resource": [f"arn:aws:bedrock:{region}:{account}:inference-profile/*",
                      "arn:aws:bedrock:*::foundation-model/*"]},
        {"Effect": "Allow", "Action": ["bedrock-agentcore:CreateEvent", "bedrock-agentcore:ListEvents",
                                       "bedrock-agentcore:GetEvent", "bedrock-agentcore:RetrieveMemoryRecords",
                                       "bedrock-agentcore:ListMemoryRecords"],
         "Resource": f"arn:aws:bedrock-agentcore:{region}:{account}:memory/{out['memory_id']}"},
        {"Effect": "Allow", "Action": ["bedrock-agentcore:GetWorkloadAccessToken",
                                       "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                                       "bedrock-agentcore:GetResourceOauth2Token"], "Resource": "*"},
        {"Effect": "Allow", "Action": ["secretsmanager:GetSecretValue"],
         "Resource": f"arn:aws:secretsmanager:{region}:{account}:secret:bedrock-agentcore-identity!*"},
    ]}
    step(f"Granting agent permissions on {role_name}")
    session().client("iam").put_role_policy(RoleName=role_name, PolicyName="cyberark-engineer-extra",
                                            PolicyDocument=json.dumps(policy))
    print("done")


if __name__ == "__main__":
    main()
