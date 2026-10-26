"""Shared helpers for the setup scripts: .env loading, outputs file, IAM roles."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUTS = ROOT / "outputs" / "resources.json"


def load_env() -> dict[str, str]:
    env_file = ROOT / ".env"
    if not env_file.exists():
        raise SystemExit("Create .env first:  cp .env.example .env  and fill in the REPLACE values.")
    values: dict[str, str] = {}
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip()
    for k, v in values.items():
        os.environ.setdefault(k, v)
    return values


def outputs() -> dict:
    return json.loads(OUTPUTS.read_text()) if OUTPUTS.exists() else {}


def save_output(**kv) -> None:
    data = outputs()
    data.update(kv)
    OUTPUTS.parent.mkdir(exist_ok=True)
    OUTPUTS.write_text(json.dumps(data, indent=2))
    # mirror into .env so the agent deploy picks them up
    env_file = ROOT / ".env"
    lines = env_file.read_text().splitlines()
    keys_done = set()
    mapping = {"gateway_mcp_url": "GATEWAY_MCP_URL", "memory_id": "MEMORY_ID",
               "kb_product_id": "KB_PRODUCT_ID", "kb_ops_id": "KB_OPS_ID",
               "guardrail_id": "GUARDRAIL_ID", "guardrail_version": "GUARDRAIL_VERSION"}
    for i, line in enumerate(lines):
        for out_key, env_key in mapping.items():
            if out_key in kv and line.startswith(env_key + "="):
                lines[i] = f"{env_key}={kv[out_key]}"
                keys_done.add(out_key)
    for out_key, env_key in mapping.items():
        if out_key in kv and out_key not in keys_done:
            lines.append(f"{env_key}={kv[out_key]}")
    env_file.write_text("\n".join(lines) + "\n")


def session():
    import boto3
    return boto3.Session(region_name=os.environ.get("AWS_REGION", "ap-southeast-2"))


def ensure_role(name: str, service: str, policy: dict) -> str:
    """Create (or update) an IAM role trusted by ``service`` with one inline policy."""
    iam = session().client("iam")
    account = os.environ["AWS_ACCOUNT_ID"]
    trust = {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Principal": {"Service": service}, "Action": "sts:AssumeRole",
        "Condition": {"StringEquals": {"aws:SourceAccount": account}}}]}
    try:
        arn = iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust),
                              Description=f"{os.environ.get('PROJECT')} {service}")["Role"]["Arn"]
        time.sleep(10)  # IAM propagation
    except iam.exceptions.EntityAlreadyExistsException:
        arn = iam.get_role(RoleName=name)["Role"]["Arn"]
    iam.put_role_policy(RoleName=name, PolicyName="inline", PolicyDocument=json.dumps(policy))
    return arn


def step(msg: str) -> None:
    print(f"\n==> {msg}", flush=True)
