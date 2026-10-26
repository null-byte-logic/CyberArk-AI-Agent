"""In-agent tool-call guard (defence in depth).

AgentCore Policy at the Gateway and the Lambda checks are authoritative.
This guard stops wrong calls earlier and gives the model a clear reason,
which saves a round trip and a Policy denial in the trace.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from agent.catalogue import (BY_NAME, ENVIRONMENTS, IDENTITY_ENVIRONMENTS, Access, Tier,
                             allowed_for_tier, is_service_request_tool, needs_environment,
                             requires_approval)


def service_requests_enabled() -> bool:
    return os.environ.get("ENABLE_SERVICE_REQUESTS", "false").lower() == "true"


@dataclass
class SessionState:
    tier: Tier = Tier.L1
    ticket_id: str = ""
    environment: str = ""          # pinned by the caller or the first tool call
    has_diagnosis: bool = False
    write_calls: int = 0
    max_write_calls: int = 20
    blocked: list[str] = field(default_factory=list)


def check_tool_call(state: SessionState, tool: str, args: dict) -> str | None:
    """Return None if the call may proceed, else a reason string."""
    spec = BY_NAME.get(tool)
    if spec is None:
        return f"{tool} is not in the approved tool catalogue."

    env = args.get("environment", "")
    if needs_environment(tool):
        if env not in ENVIRONMENTS:
            return f"Say which environment: one of {sorted(ENVIRONMENTS)}."
        if state.environment and env != state.environment:
            return (f"This ticket is pinned to {state.environment}; a call to {env} needs a "
                    f"separate ticket or an explicit instruction from the engineer.")
        if tool.startswith("identity___") and env not in IDENTITY_ENVIRONMENTS:
            return "CyberArk Identity tools apply to the Privilege Cloud tenants only."

    if spec.access is Access.READ:
        return None
    if is_service_request_tool(tool) and not service_requests_enabled():
        return "Service-request tools are disabled: this agent is configured for troubleshooting only."
    if not state.ticket_id:
        return "Write actions need a ticket number for the audit trail."
    if not allowed_for_tier(tool, state.tier):
        return f"{tool} is not allowed for a {state.tier.value} ticket; escalate instead."
    if state.tier is not Tier.SR and not tool.startswith("itsm___") and not state.has_diagnosis:
        return "Run diagnostics and record a finding before any remediation."
    if requires_approval(tool, env) and not args.get("approval_token"):
        return ("APPROVAL_REQUIRED: post the plan with itsm___request_approval and wait "
                "for an approval token before calling this tool.")
    if state.write_calls >= state.max_write_calls:
        return "Write-call limit for this session reached; hand over to an engineer."
    return None


DIAGNOSTIC_TOOLS = {"pam___get_account_activity", "logs___query", "pam___system_health",
                    "pam___get_user", "identity___get_user"}


def record(state: SessionState, tool: str, args: dict | None = None) -> None:
    spec = BY_NAME.get(tool)
    if spec and spec.access is Access.WRITE:
        state.write_calls += 1
    if tool in DIAGNOSTIC_TOOLS:
        state.has_diagnosis = True
    env = (args or {}).get("environment")
    if env in ENVIRONMENTS and not state.environment and needs_environment(tool):
        state.environment = env


def make_hook(state: SessionState):
    """Build a Strands hook provider that applies ``check_tool_call``.

    Imported lazily so this module stays importable without the SDK.
    Verify the hook event names against your installed strands-agents version.
    """
    from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent, HookProvider, HookRegistry

    class ToolGuard(HookProvider):
        def register_hooks(self, registry: HookRegistry, **_: object) -> None:
            registry.add_callback(BeforeToolCallEvent, self.before)
            registry.add_callback(AfterToolCallEvent, self.after)

        def before(self, event: BeforeToolCallEvent) -> None:
            name = event.tool_use["name"]
            reason = check_tool_call(state, name, event.tool_use.get("input") or {})
            if reason:
                state.blocked.append(f"{name}: {reason}")
                event.cancel_tool = reason

        def after(self, event: AfterToolCallEvent) -> None:
            record(state, event.tool_use["name"], event.tool_use.get("input") or {})

    return ToolGuard()
