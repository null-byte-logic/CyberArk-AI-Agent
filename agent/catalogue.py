"""Tool catalogue: the single source of truth for what each tool is,
whether it writes, which tiers may use it and which specialist owns it.

Gateway exposes tools as ``<target>___<tool>``. Everything that is not in
this catalogue is invisible to the agents, even if the Gateway lists it.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Access(str, Enum):
    READ = "R"
    WRITE = "W"


class Tier(str, Enum):
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"
    SR = "SR"


TIER_ORDER = {Tier.L1: 1, Tier.L2: 2, Tier.L3: 3, Tier.L4: 4, Tier.SR: 2}

# The three CyberArk environments this agent troubleshoots.
ENVIRONMENTS = {
    "pc-preprod": "Privilege Cloud pre-production tenant",
    "pc-prod": "Privilege Cloud production tenant",
    "onprem": "Self-hosted PAM: Primary Vault + DR Vault, PVWA/CPM/PSM on-premises",
}
# Every write in these environments needs an approval token, even L1 fixes.
PROD_ENVIRONMENTS = frozenset({"pc-prod", "onprem"})
# Identity tools only exist for Privilege Cloud tenants.
IDENTITY_ENVIRONMENTS = frozenset({"pc-preprod", "pc-prod"})


@dataclass(frozen=True)
class ToolSpec:
    name: str                 # Gateway tool name, "<target>___<tool>"
    access: Access
    owners: tuple[str, ...]   # specialist agent names allowed to load it
    max_tier: Tier | None     # highest incident tier it may run for (None = any)
    needs_approval: bool
    description: str


KNOWLEDGE = "knowledge_advisor"
DIAGNOSTICS = "diagnostics_engineer"
REMEDIATION = "remediation_engineer"
FULFILLER = "service_request_fulfiller"
ESCALATION = "escalation_packager"
SUPERVISOR = "supervisor"

_ALL_READERS = (DIAGNOSTICS, REMEDIATION, FULFILLER, ESCALATION)

CATALOGUE: tuple[ToolSpec, ...] = (
    # ---- PAM read --------------------------------------------------------
    ToolSpec("pam___get_account", Access.READ, _ALL_READERS, None, False,
             "Account details incl. secretManagement status. Never returns the secret."),
    ToolSpec("pam___search_accounts", Access.READ, _ALL_READERS, None, False,
             "Search accounts by keyword and safe."),
    ToolSpec("pam___get_account_activity", Access.READ, _ALL_READERS, None, False,
             "Recent activities on an account (CPM verify/change/reconcile, retrievals)."),
    ToolSpec("pam___get_user", Access.READ, _ALL_READERS, None, False,
             "Vault user details incl. suspended/disabled state."),
    ToolSpec("pam___search_users", Access.READ, _ALL_READERS, None, False,
             "Search vault users."),
    ToolSpec("pam___get_safe_members", Access.READ, _ALL_READERS, None, False,
             "Members and permissions of a safe."),
    ToolSpec("pam___get_platform", Access.READ, _ALL_READERS, None, False,
             "Platform definition and policy parameters."),
    ToolSpec("pam___system_health", Access.READ, _ALL_READERS, None, False,
             "Component health (PVWA, CPM, PSM, AIM) from System Health."),
    # ---- PAM write -------------------------------------------------------
    ToolSpec("pam___verify_account", Access.WRITE, (REMEDIATION,), Tier.L2, False,
             "Trigger CPM verify. Non-destructive."),
    ToolSpec("pam___activate_user", Access.WRITE, (REMEDIATION,), Tier.L1, False,
             "Re-activate a suspended vault user."),
    ToolSpec("pam___change_account", Access.WRITE, (REMEDIATION,), Tier.L2, True,
             "Trigger CPM immediate change."),
    ToolSpec("pam___reconcile_account", Access.WRITE, (REMEDIATION,), Tier.L2, True,
             "Trigger CPM reconcile using the linked reconcile account."),
    ToolSpec("pam___add_safe", Access.WRITE, (FULFILLER,), Tier.SR, True,
             "Create a safe that follows naming standards."),
    ToolSpec("pam___add_safe_member", Access.WRITE, (FULFILLER,), Tier.SR, True,
             "Add a member to a safe with a permission profile."),
    ToolSpec("pam___add_account", Access.WRITE, (FULFILLER,), Tier.SR, True,
             "Onboard an account without a secret; CPM sets the secret."),
    # ---- Identity --------------------------------------------------------
    ToolSpec("identity___get_user", Access.READ, _ALL_READERS, None, False,
             "CyberArk Identity user, MFA and lock state."),
    ToolSpec("identity___unlock_user", Access.WRITE, (REMEDIATION,), Tier.L1, False,
             "Unlock a CyberArk Identity user."),
    # ---- Logs ------------------------------------------------------------
    ToolSpec("logs___query", Access.READ, _ALL_READERS, None, False,
             "Query forwarded CyberArk component logs (Vault, CPM, PSM, PVWA)."),
    # ---- ITSM ------------------------------------------------------------
    ToolSpec("itsm___get_ticket", Access.READ, (SUPERVISOR,) + _ALL_READERS, None, False,
             "Read an incident or service request."),
    ToolSpec("itsm___update_ticket", Access.WRITE, (SUPERVISOR, ESCALATION), None, False,
             "Add work notes or change state on a ticket."),
    ToolSpec("itsm___request_approval", Access.WRITE, (REMEDIATION, FULFILLER), None, False,
             "Raise an approval on the ticket for a planned write action."),
)

BY_NAME = {t.name: t for t in CATALOGUE}

# Endpoints that must never be exposed through the Gateway.
FORBIDDEN_SUBSTRINGS = (
    "Password/Retrieve",
    "/Secret/Retrieve",
    "DeleteSafe",
    "DeleteAccount",
    "Platforms/Delete",
)


def tools_for(agent: str) -> list[ToolSpec]:
    return [t for t in CATALOGUE if agent in t.owners]


def tool_names_for(agent: str) -> set[str]:
    return {t.name for t in tools_for(agent)}


def allowed_for_tier(tool: str, tier: Tier) -> bool:
    spec = BY_NAME.get(tool)
    if spec is None:
        return False
    if spec.access is Access.READ or spec.max_tier is None:
        return True
    if spec.max_tier is Tier.SR:
        return tier is Tier.SR
    if tier is Tier.SR:
        return False
    return TIER_ORDER[tier] <= TIER_ORDER[spec.max_tier]


def requires_approval(tool: str, environment: str = "") -> bool:
    spec = BY_NAME.get(tool)
    if spec is None or spec.access is Access.READ or tool.startswith("itsm___"):
        return False
    return spec.needs_approval or environment in PROD_ENVIRONMENTS


def needs_environment(tool: str) -> bool:
    return tool.startswith(("pam___", "identity___", "logs___"))


def is_service_request_tool(tool: str) -> bool:
    spec = BY_NAME.get(tool)
    return bool(spec and spec.max_tier is Tier.SR)
