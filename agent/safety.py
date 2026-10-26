"""Safety helpers shared by the agent and the Gateway Lambda targets.

Pure standard library so it can be vendored into Lambda packages and
unit-tested without AWS SDKs.

* ``redact``          – masks secrets before text reaches a model, memory or logs.
* ``mint_approval`` / ``verify_approval`` – an approval token bound to one
  tool, its exact arguments, a ticket and an expiry (HMAC-SHA256).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from typing import Any

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("PEM_PRIVATE_KEY", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)),
    ("AWS_ACCESS_KEY", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("BEARER", re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{16,}")),
    ("CONJUR_API_KEY", re.compile(r"\b[0-9a-z]{50,60}\b")),
    ("PASSWORD_FIELD", re.compile(
        r"(?i)(\"?(password|passwd|pwd|secret|newCredentials|apikey|api_key|client_secret)\"?\s*[:=]\s*)"
        r"(\"[^\"]*\"|'[^']*'|[^\s,;}]+)")),
)


def redact(text: str) -> str:
    """Return ``text`` with known secret shapes replaced by ``[REDACTED:<kind>]``."""
    if not text:
        return text
    out = text
    for kind, pattern in _PATTERNS:
        if kind == "PASSWORD_FIELD":
            out = pattern.sub(lambda m: f"{m.group(1)}\"[REDACTED:PASSWORD]\"", out)
        else:
            out = pattern.sub(f"[REDACTED:{kind}]", out)
    return out


def redact_obj(obj: Any) -> Any:
    """Recursively redact strings and drop secret-named keys in JSON-like data."""
    secret_keys = {"password", "secret", "newcredentials", "apikey", "api_key",
                   "client_secret", "token", "access_token", "refresh_token"}
    if isinstance(obj, dict):
        return {k: ("[REDACTED]" if k.lower() in secret_keys else redact_obj(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_obj(v) for v in obj]
    if isinstance(obj, str):
        return redact(obj)
    return obj


# --------------------------------------------------------------------------
# Approval tokens
# --------------------------------------------------------------------------

def _canonical(tool: str, args: dict[str, Any], ticket: str, expires: int) -> bytes:
    body = {"tool": tool, "args": args, "ticket": ticket, "exp": expires}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def mint_approval(key: bytes, tool: str, args: dict[str, Any], ticket: str,
                  approver: str, ttl_seconds: int = 3600, now: int | None = None) -> str:
    """Token = b64(header).b64(sig). Header carries ticket, approver and expiry."""
    now = int(now if now is not None else time.time())
    exp = now + ttl_seconds
    header = {"ticket": ticket, "approver": approver, "exp": exp, "tool": tool}
    sig = hmac.new(key, _canonical(tool, args, ticket, exp) + approver.encode(),
                   hashlib.sha256).digest()
    return f"{_b64(json.dumps(header, sort_keys=True).encode())}.{_b64(sig)}"


class ApprovalError(Exception):
    pass


def verify_approval(key: bytes, token: str, tool: str, args: dict[str, Any],
                    now: int | None = None) -> dict[str, Any]:
    """Raise ApprovalError unless ``token`` approves exactly this tool + args."""
    now = int(now if now is not None else time.time())
    try:
        h_b64, s_b64 = token.split(".")
        header = json.loads(_unb64(h_b64))
        sig = _unb64(s_b64)
    except Exception as exc:  # malformed
        raise ApprovalError("malformed approval token") from exc
    if header.get("tool") != tool:
        raise ApprovalError("approval is for a different tool")
    if now > int(header.get("exp", 0)):
        raise ApprovalError("approval expired")
    expected = hmac.new(
        key,
        _canonical(tool, args, header["ticket"], int(header["exp"])) + header["approver"].encode(),
        hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        raise ApprovalError("approval does not match these arguments")
    return header
