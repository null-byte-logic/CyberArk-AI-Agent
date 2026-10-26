// AgentCore Policy (Cedar) for the CyberArk Engineer Gateway.
//
// Model: principal = the caller identified by the Gateway's inbound JWT
// (claims become tags), action = the Gateway tool "<target>___<tool>",
// context.input = the tool arguments. Default is DENY: only what is permitted
// below can run. Validate syntax in the AgentCore Policy console / API before
// enforcing; start the engine in LOG_ONLY mode for the first week.
//
// Replace GATEWAY_ARN with your Gateway ARN (scripts/setup_policy.py does this).

// ---------------------------------------------------------------- reads
permit (
  principal,
  action in [
    AgentCore::Action::"pam___get_account",
    AgentCore::Action::"pam___search_accounts",
    AgentCore::Action::"pam___get_account_activity",
    AgentCore::Action::"pam___get_user",
    AgentCore::Action::"pam___search_users",
    AgentCore::Action::"pam___get_safe_members",
    AgentCore::Action::"pam___get_platform",
    AgentCore::Action::"pam___system_health",
    AgentCore::Action::"identity___get_user",
    AgentCore::Action::"logs___query",
    AgentCore::Action::"itsm___get_ticket",
    AgentCore::Action::"itsm___find_ticket"
  ],
  resource == AgentCore::Gateway::"GATEWAY_ARN"
);

// ITSM notes and approval requests are always allowed (they are how humans stay in the loop).
permit (
  principal,
  action in [
    AgentCore::Action::"itsm___update_ticket",
    AgentCore::Action::"itsm___request_approval"
  ],
  resource == AgentCore::Gateway::"GATEWAY_ARN"
);

// ---------------------------------------------------------------- L1 self-service fixes
permit (
  principal,
  action in [
    AgentCore::Action::"pam___verify_account",
    AgentCore::Action::"pam___activate_user",
    AgentCore::Action::"identity___unlock_user"
  ],
  resource == AgentCore::Gateway::"GATEWAY_ARN"
);
// v1 note: the Gateway sees the agent's M2M token, so engineer role/tier is
// enforced in the agent (agent/guard.py). Phase 2 switches the agent to
// user-delegated tokens; then add: when { principal.getTag("cyberark_role") ... }

// ---------------------------------------------------------------- L2 remediation, approval required
permit (
  principal,
  action in [
    AgentCore::Action::"pam___change_account",
    AgentCore::Action::"pam___reconcile_account"
  ],
  resource == AgentCore::Gateway::"GATEWAY_ARN"
)
when { context.input has approval_token };

// ---------------------------------------------------------------- service requests, approval required
permit (
  principal,
  action in [
    AgentCore::Action::"pam___add_safe",
    AgentCore::Action::"pam___add_safe_member",
    AgentCore::Action::"pam___add_account"
  ],
  resource == AgentCore::Gateway::"GATEWAY_ARN"
)
when { context.input has approval_token };

// ---------------------------------------------------------------- hard limits (forbid always wins)
// Troubleshooting-only deployment: service-request tools are off.
// Delete this block (and set ENABLE_SERVICE_REQUESTS=true) to allow them.
forbid (
  principal,
  action in [
    AgentCore::Action::"pam___add_safe",
    AgentCore::Action::"pam___add_safe_member",
    AgentCore::Action::"pam___add_account"
  ],
  resource == AgentCore::Gateway::"GATEWAY_ARN"
);

// Production (Privilege Cloud prod and on-prem Primary/DR) : every write needs an approval token.
forbid (
  principal,
  action in [
    AgentCore::Action::"pam___verify_account",
    AgentCore::Action::"pam___activate_user",
    AgentCore::Action::"identity___unlock_user"
  ],
  resource == AgentCore::Gateway::"GATEWAY_ARN"
)
when { context.input has environment &&
       ["pc-prod", "onprem"].contains(context.input.environment) &&
       !(context.input has approval_token) };

// Tier-0 safes are human-only for every write.
forbid (
  principal,
  action in [
    AgentCore::Action::"pam___add_safe",
    AgentCore::Action::"pam___add_safe_member",
    AgentCore::Action::"pam___add_account"
  ],
  resource == AgentCore::Gateway::"GATEWAY_ARN"
)
when { context.input has safe_name && context.input.safe_name like "T0-*" };

// Only the named least-privilege profiles may be granted by the agent.
forbid (
  principal,
  action == AgentCore::Action::"pam___add_safe_member",
  resource == AgentCore::Gateway::"GATEWAY_ARN"
)
when { !(context.input has permission_profile) ||
       !(["EndUser", "Auditor", "Approver", "AccountsManager"].contains(context.input.permission_profile)) };
