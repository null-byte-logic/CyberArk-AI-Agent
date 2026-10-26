"""Create the AgentCore Memory resource with long-term strategies.

Namespaces match agent/app.py retrieval config:
    /env/{actorId}          environment facts (versions, topology)
    /preferences/{actorId}  engineer preferences
    /episodes/{actorId}     incident summaries (what worked)
"""
from __future__ import annotations

from common import load_env, outputs, save_output, session, step


def main() -> None:
    env = load_env()
    if outputs().get("memory_id"):
        print("memory already created:", outputs()["memory_id"])
        return
    ctl = session().client("bedrock-agentcore-control")
    step("Creating AgentCore Memory")
    mem = ctl.create_memory(
        name=f"{env['PROJECT'].replace('-', '_')}_memory",
        description="CyberArk Engineer agent memory",
        eventExpiryDuration=90,
        memoryStrategies=[
            {"semanticMemoryStrategy": {"name": "EnvironmentFacts",
                                        "namespaces": ["/env/{actorId}"]}},
            {"userPreferenceMemoryStrategy": {"name": "EngineerPreferences",
                                              "namespaces": ["/preferences/{actorId}"]}},
            {"summaryMemoryStrategy": {"name": "IncidentSummaries",
                                       "namespaces": ["/episodes/{actorId}"]}},
        ],
    )["memory"]
    save_output(memory_id=mem["id"])
    print("memory", mem["id"], mem.get("status"))


if __name__ == "__main__":
    main()
