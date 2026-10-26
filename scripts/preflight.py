"""Read-only checks before (and after) deployment. Creates nothing.

    python3 scripts/preflight.py            # all checks
    python3 scripts/preflight.py --no-models   # skip model invocation checks

Every FAIL prints a "Fix:" line. The output contains no secrets; if you need
help, ask Amazon Q Developer (see docs/TROUBLESHOOTING.md).
"""
from __future__ import annotations

import json
import shutil
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

PLACEHOLDER_MARKERS = ("REPLACE", "YOUR_")
REQUIRED = ["AWS_REGION", "AWS_ACCOUNT_ID", "PROJECT", "KB_BUCKET", "MODEL_SUPERVISOR", "MODEL_FAST", "MODEL_DEEP"]
ENVIRONMENTS = {
    "pc-preprod": {"url": "PC_PREPROD_PVWA_URL", "token": "PC_PREPROD_TOKEN_URL",
                   "secret": "PC_PREPROD_SECRET_NAME", "keys": ("client_id", "client_secret")},
    "pc-prod": {"url": "PC_PROD_PVWA_URL", "token": "PC_PROD_TOKEN_URL",
                "secret": "PC_PROD_SECRET_NAME", "keys": ("client_id", "client_secret")},
    "onprem": {"url": "ONPREM_PVWA_URL", "token": None,
               "secret": "ONPREM_SECRET_NAME", "keys": ("username", "password")},
}
NEW_SERVICES = ("bedrock-agentcore-control", "bedrock-agentcore", "s3vectors", "bedrock-agent",
                "bedrock-agent-runtime", "bedrock-runtime")
EMBED_MODEL = "amazon.titan-embed-text-v2:0"


@dataclass
class Result:
    status: str  # PASS | FAIL | WARN | SKIP
    check: str
    detail: str = ""
    fix: str = ""

    def line(self) -> str:
        s = f"[{self.status}] {self.check}"
        if self.detail:
            s += f" — {self.detail}"
        if self.fix and self.status in ("FAIL", "WARN"):
            s += f"\n       Fix: {self.fix}"
        return s


def is_placeholder(v: str | None) -> bool:
    return not v or any(m in v for m in PLACEHOLDER_MARKERS)


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def configured_environments(env: dict[str, str]) -> list[str]:
    return [name for name, spec in ENVIRONMENTS.items() if not is_placeholder(env.get(spec["url"]))]


# ---------------------------------------------------------------- checks (pure where possible)

def check_env_values(env: dict[str, str]) -> list[Result]:
    res = []
    missing = [k for k in REQUIRED if is_placeholder(env.get(k))]
    if missing:
        res.append(Result("FAIL", ".env required values", ", ".join(missing),
                          "Edit .env and replace these values (see .env.example comments)."))
    else:
        res.append(Result("PASS", ".env required values"))
    envs = configured_environments(env)
    if not envs:
        res.append(Result("FAIL", "CyberArk environments", "none configured",
                          "Set at least PC_PREPROD_PVWA_URL and PC_PREPROD_TOKEN_URL in .env."))
    else:
        res.append(Result("PASS", "CyberArk environments", ", ".join(envs)))
    for name in envs:
        spec = ENVIRONMENTS[name]
        url = env.get(spec["url"], "")
        if not url.startswith("https://") or not url.rstrip("/").endswith("/PasswordVault"):
            res.append(Result("FAIL", f"{name} PVWA URL format", url,
                              "Use https://<host>/PasswordVault (no /API, no trailing slash)."))
        if spec["token"]:
            tok = env.get(spec["token"], "")
            if is_placeholder(tok) or "/oauth2/platformtoken" not in tok:
                res.append(Result("FAIL", f"{name} Identity token URL", tok or "empty",
                                  "Use https://<identity-tenant-id>.id.cyberark.cloud/oauth2/platformtoken"))
    if "onprem" in envs and not env.get("PAM_LAMBDA_SUBNET_IDS"):
        res.append(Result("WARN", "onprem connectivity", "PAM_LAMBDA_SUBNET_IDS empty",
                          "onprem calls fail until the Lambda runs in subnets that reach PVWA (docs/deployment.md)."))
    if env.get("ENABLE_SERVICE_REQUESTS", "false").lower() == "true":
        res.append(Result("WARN", "service requests", "enabled",
                          "Troubleshooting-only scope expects false; also remove the Cedar forbid if intended."))
    return res


def check_secret_shape(name: str, secret_string: str, keys: tuple[str, ...]) -> Result:
    try:
        data = json.loads(secret_string)
    except json.JSONDecodeError:
        return Result("FAIL", f"secret {name}", "not JSON", f"Store JSON with keys {list(keys)}.")
    missing = [k for k in keys if not data.get(k)]
    if missing:
        return Result("FAIL", f"secret {name}", f"missing keys {missing}", f"Store JSON with keys {list(keys)}.")
    return Result("PASS", f"secret {name}", "keys present (values not shown)")


def explain_model_error(code: str, msg: str) -> str:
    if code == "AccessDeniedException":
        return "Enable this model in Bedrock console → Model access (Sydney), or check SCPs/IAM allow bedrock:InvokeModel."
    if code == "ValidationException" and "on-demand" in msg.lower():
        return "Use an inference-profile ID (e.g. apac.<model> or global.<model>) instead of the base model ID."
    if code == "ValidationException" and "tool" in msg.lower():
        return "This model does not support tool use through Converse; pick another model."
    if code == "ResourceNotFoundException":
        return "Model/profile ID not found in ap-southeast-2; list them with `aws bedrock list-inference-profiles`."
    if code == "ThrottlingException":
        return "Throttled; retry later or request a quota increase."
    return "See the error message; check the model ID and region."


# ---------------------------------------------------------------- AWS checks

def run(env: dict[str, str], check_models: bool = True) -> list[Result]:
    res = check_env_values(env)
    try:
        import boto3
        import botocore
    except ImportError:
        res.append(Result("FAIL", "boto3", "not installed", "pip install -r requirements-deploy.txt"))
        return res
    region = env.get("AWS_REGION", "ap-southeast-2")
    sess = boto3.Session(region_name=region)
    res.append(Result("PASS", "boto3/botocore", f"{boto3.__version__}/{botocore.__version__}"))

    available = set(sess.get_available_services())
    missing_svc = [s for s in NEW_SERVICES if s not in available]
    res.append(Result("FAIL" if missing_svc else "PASS", "boto3 knows AgentCore/S3 Vectors APIs",
                      ", ".join(missing_svc) if missing_svc else "",
                      "Upgrade: .venv/bin/pip install -U boto3 botocore"))

    try:
        ident = sess.client("sts").get_caller_identity()
        acct = ident["Account"]
        ok = acct == env.get("AWS_ACCOUNT_ID")
        res.append(Result("PASS" if ok else "FAIL", "AWS account", f"{acct} as {ident['Arn'].split('/')[-2] if '/' in ident['Arn'] else ident['Arn']}",
                          "Switch to the ai-sandbox account/role, or fix AWS_ACCOUNT_ID in .env."))
    except Exception as exc:
        res.append(Result("FAIL", "AWS credentials", str(exc)[:200],
                          "Open CloudShell from the console, or run `aws sso login`."))
        return res

    res.append(Result("PASS" if shutil.which("agentcore") else "WARN", "agentcore CLI",
                      "found" if shutil.which("agentcore") else "not on PATH",
                      "Installed by cloudshell_deploy.sh into .venv; or pip install bedrock-agentcore-starter-toolkit"))

    if check_models:
        rt = sess.client("bedrock-runtime")
        tool = {"toolSpec": {"name": "ping", "description": "Return pong.",
                             "inputSchema": {"json": {"type": "object", "properties": {}}}}}
        seen = set()
        for key in ("MODEL_SUPERVISOR", "MODEL_FAST", "MODEL_DEEP"):
            mid = env.get(key)
            if is_placeholder(mid) or mid in seen:
                continue
            seen.add(mid)
            try:
                rt.converse(modelId=mid, messages=[{"role": "user", "content": [{"text": "Call the ping tool."}]}],
                            toolConfig={"tools": [tool]}, inferenceConfig={"maxTokens": 64})
                res.append(Result("PASS", f"model {key}", f"{mid} (tool use OK)"))
            except botocore.exceptions.ClientError as exc:
                code, msg = exc.response["Error"]["Code"], exc.response["Error"]["Message"]
                res.append(Result("FAIL", f"model {key}", f"{mid}: {code}: {msg[:160]}", explain_model_error(code, msg)))
        try:
            rt.invoke_model(modelId=EMBED_MODEL, body=json.dumps({"inputText": "ping"}))
            res.append(Result("PASS", "embedding model", EMBED_MODEL))
        except botocore.exceptions.ClientError as exc:
            code, msg = exc.response["Error"]["Code"], exc.response["Error"]["Message"]
            res.append(Result("FAIL", "embedding model", f"{code}: {msg[:160]}", explain_model_error(code, msg)))

    sm = sess.client("secretsmanager")
    for name in configured_environments(env):
        spec = ENVIRONMENTS[name]
        sname = env.get(spec["secret"], "")
        try:
            val = sm.get_secret_value(SecretId=sname)["SecretString"]
            res.append(check_secret_shape(sname, val, spec["keys"]))
        except botocore.exceptions.ClientError as exc:
            res.append(Result("FAIL", f"secret {sname}", exc.response["Error"]["Code"],
                              f"Create it: see README 'Create the environment secrets' ({name})."))

    for name in configured_environments(env):
        spec = ENVIRONMENTS[name]
        if not spec["token"]:
            continue  # on-prem is private; tested from the Lambda, not CloudShell
        for k in (spec["url"], spec["token"]):
            url = env.get(k, "")
            try:
                urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=8)
                res.append(Result("PASS", f"{name} reachable", url.split("/")[2]))
            except urllib.error.HTTPError:
                res.append(Result("PASS", f"{name} reachable", url.split("/")[2] + " (HTTP response)"))
            except Exception as exc:
                res.append(Result("WARN", f"{name} reachable", f"{url.split('/')[2]}: {type(exc).__name__}",
                                  "Check the tenant hostname; CloudShell has internet access, so DNS errors mean a typo."))
    return res


def main() -> int:
    env_file = ROOT / ".env"
    if not env_file.exists():
        print("[FAIL] .env missing\n       Fix: cp .env.example .env and fill in the values.")
        return 1
    results = run(read_env(env_file), check_models="--no-models" not in sys.argv)
    for r in results:
        print(r.line())
    fails = sum(r.status == "FAIL" for r in results)
    print(f"\n{fails} failing check(s)." if fails else "\nAll required checks passed.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
