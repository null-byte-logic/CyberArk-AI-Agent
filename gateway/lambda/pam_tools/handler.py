"""AgentCore Gateway Lambda target: CyberArk PAM + Identity tools across environments.

Environments (configured in PAM_ENVIRONMENTS, JSON):
    pc-preprod  Privilege Cloud pre-production tenant
    pc-prod     Privilege Cloud production tenant
    onprem      Self-hosted PAM (Primary Vault + DR Vault) via PVWA, reached over VPN/Direct Connect

Every tool takes a required ``environment`` argument, so a call can never hit
an environment implicitly.

Gateway passes the tool arguments as ``event`` and the tool name in
``context.client_context.custom["bedrockAgentCoreToolName"]`` as
``<target>___<tool>``. The same function serves the ``pam`` and ``identity``
targets; dispatch uses the full name.

Security properties enforced here (in addition to AgentCore Policy):
* Only allow-listed tools/endpoints; there is no password-retrieve or delete route.
* Writes that need approval verify an HMAC token bound to tool + exact arguments
  (including ``environment``). In production environments every write needs one.
* Service-request tools are disabled unless ENABLE_SERVICE_REQUESTS=true.
* Tier-0 safes are refused for writes. Responses are redacted.
"""
from __future__ import annotations

import json
import logging
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from safety import ApprovalError, redact_obj, verify_approval  # vendored copy of agent/safety.py

log = logging.getLogger()
log.setLevel(logging.INFO)

ENVIRONMENTS: dict[str, dict] = json.loads(os.environ.get("PAM_ENVIRONMENTS", "{}"))
APPROVAL_KEY_ARN = os.environ.get("APPROVAL_KEY_ARN", "")
ENABLE_SR = os.environ.get("ENABLE_SERVICE_REQUESTS", "false").lower() == "true"
TIER0_PREFIX = os.environ.get("TIER0_SAFE_PREFIX", "T0-")
TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "20"))

_tokens: dict[str, dict[str, Any]] = {}
_secrets: dict[str, Any] = {}


# --------------------------------------------------------------------------
# Secrets, HTTP, auth
# --------------------------------------------------------------------------

def _secret(arn: str) -> Any:
    if arn not in _secrets:
        import boto3
        raw = boto3.client("secretsmanager").get_secret_value(SecretId=arn)["SecretString"]
        try:
            _secrets[arn] = json.loads(raw)
        except json.JSONDecodeError:
            _secrets[arn] = raw
    return _secrets[arn]


def _http(method: str, url: str, headers: dict[str, str], body: Any = None,
          form: dict[str, str] | None = None, cafile: str | None = None) -> tuple[int, Any]:
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers = {**headers, "Content-Type": "application/x-www-form-urlencoded"}
    elif body is not None:
        data = json.dumps(body).encode()
        headers = {**headers, "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    ctx = ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
            raw = resp.read().decode() or "null"
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as err:
        return err.code, {"error": err.read().decode(errors="replace")[:2000]}


def _cafile(cfg: dict) -> str | None:
    """Private CA for on-prem PVWA: downloaded once from S3 to /tmp."""
    uri = cfg.get("ca_bundle_s3")
    if not uri:
        return None
    local = "/tmp/pvwa-ca.pem"
    if not os.path.exists(local):
        import boto3
        bucket, key = uri.removeprefix("s3://").split("/", 1)
        boto3.client("s3").download_file(bucket, key, local)
    return local


def _cfg(env: str) -> dict:
    if env not in ENVIRONMENTS:
        raise ValueError(f"environment must be one of {sorted(ENVIRONMENTS)}")
    return ENVIRONMENTS[env]


def _logon(env: str) -> str:
    cfg, now = _cfg(env), time.time()
    cached = _tokens.get(env)
    if cached and now < cached["exp"] - 60:
        return cached["value"]
    creds = _secret(cfg["secret_arn"])
    if cfg["mode"] == "privilege-cloud":
        status, body = _http("POST", cfg["identity_token_url"], {}, form={
            "grant_type": "client_credentials",
            "client_id": creds["client_id"], "client_secret": creds["client_secret"]})
        if status != 200:
            raise RuntimeError(f"Identity token request failed for {env}: HTTP {status}")
        value, ttl = f"Bearer {body['access_token']}", int(body.get("expires_in", 900))
    else:
        method = creds.get("auth_method", "CyberArk")  # CyberArk | LDAP | RADIUS
        status, body = _http("POST", f"{cfg['pvwa_base_url']}/API/auth/{method}/Logon", {},
                             body={"username": creds["username"], "password": creds["password"],
                                   "concurrentSession": True}, cafile=_cafile(cfg))
        if status != 200:
            raise RuntimeError(f"PVWA logon failed for {env}: HTTP {status}")
        value, ttl = (body if isinstance(body, str) else str(body)), int(creds.get("session_seconds", 900))
    _tokens[env] = {"value": value, "exp": now + ttl}
    return value


def _api(env: str, method: str, path: str, body: Any = None) -> Any:
    cfg = _cfg(env)
    url = f"{cfg['pvwa_base_url']}/API{path}"
    status, data = _http(method, url, {"Authorization": _logon(env)}, body=body, cafile=_cafile(cfg))
    if status == 401:
        _tokens.pop(env, None)
        status, data = _http(method, url, {"Authorization": _logon(env)}, body=body, cafile=_cafile(cfg))
    return {"environment": env, "status": status, "data": data}


def _identity(env: str, path: str, body: Any = None) -> Any:
    cfg = _cfg(env)
    if cfg["mode"] != "privilege-cloud":
        return {"environment": env, "status": 400,
                "data": {"error": "CyberArk Identity tools apply to Privilege Cloud tenants only."}}
    base = cfg["identity_token_url"].split("/oauth2/")[0]
    status, data = _http("POST", f"{base}{path}", {"Authorization": _logon(env)}, body=body or {})
    return {"environment": env, "status": status, "data": data}


def _q(v: Any) -> str:
    return urllib.parse.quote(str(v), safe="")


# --------------------------------------------------------------------------
# PAM tools
# --------------------------------------------------------------------------

def get_account(a): return _api(a["environment"], "GET", f"/Accounts/{_q(a['account_id'])}")


def search_accounts(a):
    params = {"search": a.get("search", ""), "limit": str(min(int(a.get("limit", 25)), 100))}
    if a.get("safe_name"):
        params["filter"] = f"safeName eq {a['safe_name']}"
    return _api(a["environment"], "GET", "/Accounts?" + urllib.parse.urlencode(params))


def get_account_activity(a): return _api(a["environment"], "GET", f"/Accounts/{_q(a['account_id'])}/Activities")
def get_user(a): return _api(a["environment"], "GET", f"/Users/{_q(a['user_id'])}")


def search_users(a):
    return _api(a["environment"], "GET", "/Users?" + urllib.parse.urlencode({"search": a.get("search", "")}))


def get_safe_members(a): return _api(a["environment"], "GET", f"/Safes/{_q(a['safe_name'])}/Members")
def get_platform(a): return _api(a["environment"], "GET", f"/Platforms/{_q(a['platform_id'])}")


def system_health(a):
    comp = a.get("component", "CPM")  # PVWA | SessionManagement | CPM | PTA | AIM
    return _api(a["environment"], "GET", f"/ComponentsMonitoringDetails/{_q(comp)}")


def verify_account(a): return _api(a["environment"], "POST", f"/Accounts/{_q(a['account_id'])}/Verify")


def change_account(a):
    return _api(a["environment"], "POST", f"/Accounts/{_q(a['account_id'])}/Change", {"ChangeEntireGroup": False})


def reconcile_account(a): return _api(a["environment"], "POST", f"/Accounts/{_q(a['account_id'])}/Reconcile")
def activate_user(a): return _api(a["environment"], "POST", f"/Users/{_q(a['user_id'])}/Activate")


PERMISSION_PROFILES = {
    "EndUser": {"useAccounts": True, "listAccounts": True, "viewAuditLog": True, "viewSafeMembers": True},
    "Approver": {"listAccounts": True, "viewAuditLog": True, "viewSafeMembers": True,
                 "requestsAuthorizationLevel1": True},
    "Auditor": {"listAccounts": True, "viewAuditLog": True, "viewSafeMembers": True},
    "AccountsManager": {"listAccounts": True, "addAccounts": True, "updateAccountContent": True,
                        "updateAccountProperties": True, "initiateCPMAccountManagementOperations": True,
                        "viewAuditLog": True, "viewSafeMembers": True},
}


def add_safe(a):
    body = {"safeName": a["safe_name"], "description": a.get("description", ""),
            "managingCPM": a["managing_cpm"], "numberOfDaysRetention": int(a.get("retention_days", 7))}
    return _api(a["environment"], "POST", "/Safes", body)


def add_safe_member(a):
    profile = a["permission_profile"]
    if profile not in PERMISSION_PROFILES:
        return {"status": 400, "data": {"error": f"Unknown profile {profile}. Use one of {list(PERMISSION_PROFILES)}"}}
    body = {"memberName": a["member_name"], "searchIn": a.get("search_in", "Vault"),
            "memberType": a.get("member_type", "Group"), "permissions": PERMISSION_PROFILES[profile]}
    return _api(a["environment"], "POST", f"/Safes/{_q(a['safe_name'])}/Members", body)


def add_account(a):
    body = {"safeName": a["safe_name"], "platformId": a["platform_id"], "address": a["address"],
            "userName": a["user_name"], "secretType": "password",
            "secretManagement": {"automaticManagementEnabled": True}}
    if a.get("name"):
        body["name"] = a["name"]
    return _api(a["environment"], "POST", "/Accounts", body)


# --------------------------------------------------------------------------
# CyberArk Identity tools (Privilege Cloud tenants). Verify paths against
# your tenant's Identity API reference before enabling in production.
# --------------------------------------------------------------------------

def identity_get_user(a):
    return _identity(a["environment"], "/CDirectoryService/GetUserByName", {"username": a["username"]})


def identity_unlock_user(a):
    uid = _q(a["user_uuid"])
    return _identity(a["environment"], f"/UserMgmt/SetCloudLock?user={uid}&lockUser=false")


# name: (function, approval_always, is_service_request)
TOOLS: dict[str, tuple[Callable[[dict], Any], bool, bool]] = {
    "pam___get_account": (get_account, False, False),
    "pam___search_accounts": (search_accounts, False, False),
    "pam___get_account_activity": (get_account_activity, False, False),
    "pam___get_user": (get_user, False, False),
    "pam___search_users": (search_users, False, False),
    "pam___get_safe_members": (get_safe_members, False, False),
    "pam___get_platform": (get_platform, False, False),
    "pam___system_health": (system_health, False, False),
    "pam___verify_account": (verify_account, False, False),
    "pam___activate_user": (activate_user, False, False),
    "pam___change_account": (change_account, True, False),
    "pam___reconcile_account": (reconcile_account, True, False),
    "pam___add_safe": (add_safe, True, True),
    "pam___add_safe_member": (add_safe_member, True, True),
    "pam___add_account": (add_account, True, True),
    "identity___get_user": (identity_get_user, False, False),
    "identity___unlock_user": (identity_unlock_user, False, False),
}
READ_ONLY = {"pam___get_account", "pam___search_accounts", "pam___get_account_activity", "pam___get_user",
             "pam___search_users", "pam___get_safe_members", "pam___get_platform", "pam___system_health",
             "identity___get_user"}


def _tool_name(context) -> str:
    try:
        return context.client_context.custom["bedrockAgentCoreToolName"]
    except Exception:
        return ""


def handler(event: dict, context) -> dict:
    args = dict(event or {})
    tool = _tool_name(context) or args.pop("__tool", "")
    if tool not in TOOLS:
        return {"error": f"Tool '{tool}' is not available."}
    fn, approval_always, is_sr = TOOLS[tool]

    env = args.get("environment")
    if env not in ENVIRONMENTS:
        return {"error": f"environment is required: one of {sorted(ENVIRONMENTS)}"}
    if is_sr and not ENABLE_SR:
        return {"error": "Service-request tools are disabled; this agent is configured for troubleshooting only."}

    is_write = tool not in READ_ONLY
    safe = str(args.get("safe_name", ""))
    if is_write and safe.startswith(TIER0_PREFIX):
        return {"error": "Tier-0 safes are human-only. Escalate to the PAM team."}

    needs_approval = is_write and (approval_always or ENVIRONMENTS[env].get("production", False))
    if needs_approval:
        token = args.pop("approval_token", None)
        if not token:
            return {"error": "APPROVAL_REQUIRED", "tool": tool, "args": args}
        try:
            approval = verify_approval(_secret(APPROVAL_KEY_ARN)["key"].encode(), token, tool, args)
        except ApprovalError as exc:
            return {"error": f"APPROVAL_INVALID: {exc}"}
        log.info(json.dumps({"event": "approved_write", "tool": tool, "environment": env,
                             "ticket": approval["ticket"], "approver": approval["approver"]}))
    else:
        args.pop("approval_token", None)

    try:
        result = fn(args)
    except KeyError as exc:
        return {"error": f"Missing argument {exc}"}
    except Exception as exc:
        log.exception("tool failed")
        return {"error": f"{type(exc).__name__}: {exc}"}
    return redact_obj(result)
