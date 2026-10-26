"""Specialist agents, exposed to the supervisor as tools (agents-as-tools).

Each specialist receives only the Gateway tools the catalogue assigns to it,
so a prompt-injected Knowledge Advisor cannot call a write tool: it simply
does not have one.
"""
from __future__ import annotations

from pathlib import Path

from strands import Agent, tool
from strands.models import BedrockModel

from agent import catalogue as cat
from agent.config import Settings
from agent.guard import service_requests_enabled
from agent.knowledge import search_product_docs, search_runbooks

PROMPTS = Path(__file__).parent / "prompts"


def _prompt(name: str) -> str:
    shared = (PROMPTS / "_shared_rules.md").read_text()
    return (PROMPTS / f"{name}.md").read_text() + "\n\n" + shared


def _model(settings: Settings, model_id: str) -> BedrockModel:
    return BedrockModel(
        model_id=model_id,
        region_name=settings.region,
        guardrail_id=settings.guardrail_id,
        guardrail_version=settings.guardrail_version,
        guardrail_trace="enabled",
        temperature=0.1,
    )


def _pick(gateway_tools: list, agent_name: str) -> list:
    allowed = cat.tool_names_for(agent_name)
    return [t for t in gateway_tools if getattr(t, "tool_name", None) in allowed]


def build_specialists(settings: Settings, gateway_tools: list, hooks: list) -> list:
    fast = _model(settings, settings.model_fast)
    std = _model(settings, settings.model_supervisor)
    deep = _model(settings, settings.model_deep)

    knowledge = Agent(name=cat.KNOWLEDGE, model=fast, system_prompt=_prompt("knowledge"),
                      tools=[search_product_docs, search_runbooks], callback_handler=None)
    diagnostics = Agent(name=cat.DIAGNOSTICS, model=deep, system_prompt=_prompt("diagnostics"),
                        tools=_pick(gateway_tools, cat.DIAGNOSTICS) + [search_runbooks, search_product_docs],
                        hooks=hooks, callback_handler=None)
    remediation = Agent(name=cat.REMEDIATION, model=std, system_prompt=_prompt("remediation"),
                        tools=_pick(gateway_tools, cat.REMEDIATION) + [search_runbooks],
                        hooks=hooks, callback_handler=None)
    fulfiller = Agent(name=cat.FULFILLER, model=std, system_prompt=_prompt("service_request"),
                      tools=_pick(gateway_tools, cat.FULFILLER) + [search_runbooks],
                      hooks=hooks, callback_handler=None)
    escalation = Agent(name=cat.ESCALATION, model=deep, system_prompt=_prompt("escalation"),
                       tools=_pick(gateway_tools, cat.ESCALATION) + [search_product_docs, search_runbooks],
                       hooks=hooks, callback_handler=None)

    @tool
    def knowledge_advisor(question: str) -> str:
        """Answer CyberArk product, architecture, configuration and API questions with citations.
        Include product and version in the question when known."""
        return str(knowledge(question))

    @tool
    def diagnostics_engineer(incident: str) -> str:
        """Read-only investigation of a CyberArk incident. Pass the ticket number, symptom,
        exact error text, affected user/account/safe and environment. Returns findings,
        evidence, root-cause hypothesis, confidence and a catalogue action or ESCALATE."""
        return str(diagnostics(incident))

    @tool
    def remediation_engineer(plan: str) -> str:
        """Execute ONE remediation from the approved catalogue for a diagnosed incident.
        Pass ticket number, diagnosis, action id, target ids and any approval token."""
        return str(remediation(plan))

    @tool
    def service_request_fulfiller(request: str) -> str:
        """Validate and fulfil a CyberArk service request (safe, member, account onboarding,
        user activation). Pass the ticket number and the request details."""
        return str(fulfiller(request))

    @tool
    def escalation_packager(case: str) -> str:
        """Prepare an L3/L4 hand-off: timeline, evidence, root-cause hypotheses, remediation plan
        with rollback, and CyberArk support case text. Pass all findings so far."""
        return str(escalation(case))

    agents = [knowledge_advisor, diagnostics_engineer, remediation_engineer, escalation_packager]
    if service_requests_enabled():
        agents.append(service_request_fulfiller)
    return agents
