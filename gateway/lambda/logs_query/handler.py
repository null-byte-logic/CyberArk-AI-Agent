"""AgentCore Gateway Lambda target: query forwarded CyberArk component logs
with CloudWatch Logs Insights. Assumes logs are shipped to log groups named
``/cyberark/<environment>/<component>`` by the CloudWatch agent, for example:

    /cyberark/onprem/vault      Primary Vault ITALOG / trace
    /cyberark/onprem/dr-vault   DR Vault ITALOG
    /cyberark/onprem/padr       PADR.log (DR replication) on the DR Vault
    /cyberark/pc-prod/connector Privilege Cloud connector hosts (CPM/PSM logs)

Adjust LOG_GROUP_PREFIX if yours differ.
"""
from __future__ import annotations

import json
import os
import re
import time

from safety import redact  # vendored copy of agent/safety.py

LOG_GROUP_PREFIX = os.environ.get("LOG_GROUP_PREFIX", "/cyberark/")
COMPONENTS = {"vault", "dr-vault", "padr", "pvwa", "cpm", "psm", "psmp", "pta", "connector", "conjur", "epm"}
ENVIRONMENTS = {"pc-preprod", "pc-prod", "onprem"}
MAX_HOURS = 72
MAX_LINES = 200
_SAFE_TEXT = re.compile(r"^[\w .:\-/@]{0,120}$")


def build_query(text: str, host: str, limit: int) -> str:
    q = ["fields @timestamp, @logStream, @message"]
    if text:
        q.append(f"filter @message like /{re.escape(text)}/")
    if host:
        q.append(f"filter @logStream like /{re.escape(host)}/")
    q.append("sort @timestamp desc")
    q.append(f"limit {limit}")
    return " | ".join(q)


def handler(event: dict, context) -> dict:
    environment = str(event.get("environment", ""))
    if environment not in ENVIRONMENTS:
        return {"error": f"environment must be one of {sorted(ENVIRONMENTS)}"}
    component = str(event.get("component", "")).lower()
    if component not in COMPONENTS:
        return {"error": f"component must be one of {sorted(COMPONENTS)}"}
    text = str(event.get("contains", ""))
    host = str(event.get("host", ""))
    if not (_SAFE_TEXT.match(text) and _SAFE_TEXT.match(host)):
        return {"error": "contains/host may only hold letters, digits and . : - / @"}
    hours = min(max(int(event.get("hours", 24)), 1), MAX_HOURS)
    limit = min(max(int(event.get("limit", 50)), 1), MAX_LINES)

    import boto3  # imported after validation so bad input never touches AWS
    logs = boto3.client("logs")
    end = int(time.time())
    qid = logs.start_query(logGroupName=f"{LOG_GROUP_PREFIX}{environment}/{component}",
                           startTime=end - hours * 3600, endTime=end,
                           queryString=build_query(text, host, limit))["queryId"]
    for _ in range(40):
        res = logs.get_query_results(queryId=qid)
        if res["status"] in ("Complete", "Failed", "Cancelled", "Timeout"):
            break
        time.sleep(0.5)
    rows = [{f["field"]: f["value"] for f in r} for r in res.get("results", [])]
    return {"status": res["status"], "count": len(rows),
            "lines": [redact(json.dumps(r)) for r in rows]}
