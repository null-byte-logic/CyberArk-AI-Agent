"""AgentCore Runtime entrypoint for the CyberArk Engineer agent.

Invocation payload (JSON):
    {
      "prompt": "User question or ticket text",
      "ticket_id": "INC0012345",        # optional for Q&A, required for actions
      "tier": "L1|L2|L3|L4|SR",          # optional; supervisor re-classifies
      "environment": "pc-preprod|pc-prod|onprem",  # optional; pins all tool calls
      "engineer_id": "jdoe"              # actor for memory namespaces
    }
"""
from __future__ import annotations

import logging
from pathlib import Path

from bedrock_agentcore.identity.auth import requires_access_token
from bedrock_agentcore.memory.integrations.strands.config import (
    AgentCoreMemoryConfig, RetrievalConfig)
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager)
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from mcp.client.streamable_http import streamablehttp_client
from strands import Agent
from strands.tools.mcp import MCPClient

from agent import knowledge
from agent.catalogue import ENVIRONMENTS, Tier
from agent.config import Settings
from agent.guard import SessionState, make_hook
from agent.safety import redact
from agent.specialists import _model, build_specialists

log = logging.getLogger("cyberark-engineer")
app = BedrockAgentCoreApp()
settings = Settings()
knowledge.init(settings)
SUPERVISOR_PROMPT = (Path(__file__).parent / "prompts" / "supervisor.md").read_text() + "\n\n" + \
    (Path(__file__).parent / "prompts" / "_shared_rules.md").read_text()


@requires_access_token(provider_name=settings.gateway_oauth_provider,
                       scopes=[settings.gateway_scope], auth_flow="M2M")
async def _gateway_token(*, access_token: str) -> str:
    return access_token


def _memory(payload: dict, session_id: str) -> AgentCoreMemorySessionManager:
    actor = payload.get("engineer_id", "anonymous")
    cfg = AgentCoreMemoryConfig(
        memory_id=settings.memory_id,
        session_id=payload.get("ticket_id") or session_id,
        actor_id=actor,
        retrieval_config={
            "/env/{actorId}": RetrievalConfig(top_k=10, relevance_score=0.3),
            "/preferences/{actorId}": RetrievalConfig(top_k=5, relevance_score=0.5),
            "/episodes/{actorId}": RetrievalConfig(top_k=5, relevance_score=0.5),
        },
    )
    return AgentCoreMemorySessionManager(cfg, region_name=settings.region)


@app.entrypoint
async def invoke(payload: dict, context) -> dict:
    prompt = payload.get("prompt", "").strip()
    if not prompt:
        return {"error": "prompt is required"}

    ticket = payload.get("ticket_id", "")
    try:
        tier = Tier(payload.get("tier", "L1"))
    except ValueError:
        tier = Tier.L1
    environment = payload.get("environment", "")
    if environment and environment not in ENVIRONMENTS:
        return {"error": f"environment must be one of {sorted(ENVIRONMENTS)}"}
    state = SessionState(tier=tier, ticket_id=ticket, environment=environment,
                         max_write_calls=settings.max_write_calls_per_session)
    hooks = [make_hook(state)]

    token = await _gateway_token()
    gateway = MCPClient(lambda: streamablehttp_client(
        settings.gateway_url,
        headers={"Authorization": f"Bearer {token}", "X-Ticket-Id": ticket}))

    with gateway:
        gateway_tools = gateway.list_tools_sync()
        specialists = build_specialists(settings, gateway_tools, hooks)
        itsm = [t for t in gateway_tools
                if getattr(t, "tool_name", "") in {"itsm___get_ticket", "itsm___update_ticket"}]
        supervisor = Agent(
            name="supervisor",
            model=_model(settings, settings.model_supervisor),
            system_prompt=SUPERVISOR_PROMPT,
            tools=specialists + itsm,
            session_manager=_memory(payload, context.session_id),
            hooks=hooks,
            callback_handler=None,
        )
        header = (f"[ticket={ticket or 'none'} tier={tier.value} "
                  f"environment={environment or 'not stated'}]\n")
        result = await supervisor.invoke_async(header + prompt)

    answer = redact(str(result))
    return {"answer": answer, "ticket_id": ticket, "tier": state.tier.value,
            "environment": state.environment,
            "write_calls": state.write_calls, "blocked_calls": state.blocked}


if __name__ == "__main__":
    app.run()
