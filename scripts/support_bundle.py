"""Collect a REDACTED diagnostics bundle to share with Amazon Q Developer (or a colleague).

    python3 scripts/support_bundle.py            # writes support-bundle-<time>.md
    python3 scripts/support_bundle.py --hours 3  # look further back in CloudWatch

What it contains: repo version, tool versions, which .env settings are set
(values hidden except safe ones), created resource IDs (account number masked),
preflight results, the tail of the latest deploy log, and recent ERROR lines from
the agent runtime and tool Lambdas. Everything passes through secret redaction.
What it never contains: passwords, client secrets, tokens, or Secrets Manager values.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "scripts"))

from safety import redact  # noqa: E402

SAFE_TO_SHOW = {"AWS_REGION", "PROJECT", "MODEL_SUPERVISOR", "MODEL_FAST", "MODEL_DEEP", "KB_PRODUCT_PREFIX",
                "KB_OPS_PREFIX", "ENABLE_SERVICE_REQUESTS", "GUARDRAIL_VERSION", "KB_SEARCH_TYPE"}


def mask_account(text: str, account: str) -> str:
    if account and len(account) == 12:
        text = text.replace(account, "********" + account[-4:])
    return text


def mask_hosts(text: str) -> str:
    """Hide tenant / internal hostnames but keep their shape."""
    text = re.sub(r"https://([\w-]+)\.privilegecloud\.cyberark\.cloud", r"https://<pc-tenant>.privilegecloud.cyberark.cloud", text)
    text = re.sub(r"https://([\w-]+)\.id\.cyberark\.cloud", r"https://<identity-tenant>.id.cyberark.cloud", text)
    return text


def clean(text: str, account: str) -> str:
    return mask_hosts(mask_account(redact(text), account))


def describe_env(env: dict[str, str]) -> list[str]:
    rows = []
    for k, v in env.items():
        if k in SAFE_TO_SHOW:
            shown = v or "(empty)"
        elif not v:
            shown = "(empty)"
        elif "REPLACE" in v or "YOUR_" in v:
            shown = "(placeholder)"
        else:
            shown = "(set)"
        rows.append(f"| {k} | {shown} |")
    return rows


def sh(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd=ROOT).stdout.strip()
    except Exception as exc:
        return f"(could not run {cmd[0]}: {exc})"


def recent_errors(sess, group_prefix: str, hours: float, limit: int = 40) -> list[str]:
    logs = sess.client("logs")
    out: list[str] = []
    groups = logs.describe_log_groups(logGroupNamePrefix=group_prefix).get("logGroups", [])
    start = int((time.time() - hours * 3600) * 1000)
    for g in groups[:6]:
        try:
            ev = logs.filter_log_events(logGroupName=g["logGroupName"], startTime=start, limit=limit,
                                        filterPattern="?ERROR ?Error ?Exception ?Traceback ?denied ?Denied")
        except Exception as exc:
            out.append(f"## {g['logGroupName']}\n(could not read: {exc})")
            continue
        lines = [e["message"].rstrip() for e in ev.get("events", [])]
        if lines:
            out.append(f"### {g['logGroupName']}\n```\n" + "\n".join(lines[-limit:]) + "\n```")
    return out


def build(env: dict[str, str], resources: dict, preflight_lines: list[str], deploy_tail: str,
          errors: list[str], versions: str) -> str:
    acct = env.get("AWS_ACCOUNT_ID", "")
    parts = [
        "# CyberArk Engineer agent — support bundle",
        f"Generated {dt.datetime.now().isoformat(timespec='seconds')}. Secrets redacted; account and tenant names masked.",
        "",
        "Context for the assistant: read AGENTS.md and docs/TROUBLESHOOTING.md in this repo. "
        "Find the first failing step below and propose the smallest fix; every setup script is safe to re-run.",
        "",
        "## Versions", "```", versions, "```",
        "## .env settings (values hidden)", "| Key | Value |", "| --- | --- |", *describe_env(env),
        "## Created resources (outputs/resources.json)", "```json", json.dumps(resources, indent=2), "```",
        "## Preflight", "```", *preflight_lines, "```",
        "## Latest deploy log (last 150 lines)", "```", deploy_tail or "(no logs/deploy-*.log found)", "```",
        "## Recent errors in CloudWatch", *(errors or ["(none found)"]),
    ]
    return clean("\n".join(parts), acct)


def main() -> int:
    import preflight
    hours = float(sys.argv[sys.argv.index("--hours") + 1]) if "--hours" in sys.argv else 1.0
    env_file = ROOT / ".env"
    env = preflight.read_env(env_file) if env_file.exists() else {}
    res_file = ROOT / "outputs" / "resources.json"
    resources = json.loads(res_file.read_text()) if res_file.exists() else {}

    pf = [r.line() for r in preflight.run(env, check_models=False)] if env else ["(.env missing)"]
    logs_dir = ROOT / "logs"
    latest = sorted(logs_dir.glob("deploy-*.log"))[-1] if logs_dir.exists() and list(logs_dir.glob("deploy-*.log")) else None
    tail = "\n".join(latest.read_text(errors="replace").splitlines()[-150:]) if latest else ""

    versions = "\n".join([
        "repo commit: " + sh(["git", "rev-parse", "--short", "HEAD"]),
        "python: " + sys.version.split()[0],
        "aws cli: " + sh(["aws", "--version"]),
        "agentcore: " + (sh(["agentcore", "--version"]) or "not found"),
        "agentcore status:\n" + (sh(["agentcore", "status"]) or "(not deployed)"),
    ])

    errors: list[str] = []
    try:
        import boto3
        sess = boto3.Session(region_name=env.get("AWS_REGION", "ap-southeast-2"))
        errors += recent_errors(sess, "/aws/bedrock-agentcore/runtimes/", hours)
        errors += recent_errors(sess, "/aws/lambda/cyberark-engineer-", hours)
    except Exception as exc:
        errors.append(f"(could not read CloudWatch: {exc})")

    out = ROOT / f"support-bundle-{dt.datetime.now():%Y%m%d-%H%M%S}.md"
    out.write_text(build(env, resources, pf, tail, errors, versions))
    print(f"Wrote {out.name}. Review it, then paste it into Amazon Q Developer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
