"""Create an AgentCore Policy engine, load policy/cyberark_agent.cedar and
attach it to the Gateway in LOG_ONLY mode. Switch to ENFORCE after a week of
clean traces:  python scripts/setup_policy.py --enforce

The Policy API is new; if a call here fails because a parameter name changed,
create the engine in the console (AgentCore > Policy), paste the Cedar file,
and attach it to the gateway. The Cedar file is the source of truth.
"""
from __future__ import annotations

import sys

from common import ROOT, load_env, outputs, save_output, session, step


def main() -> None:
    load_env()
    enforce = "--enforce" in sys.argv
    out = outputs()
    gw_id = out["gateway_id"]
    ctl = session().client("bedrock-agentcore-control")
    gw_arn = ctl.get_gateway(gatewayIdentifier=gw_id)["gatewayArn"]
    cedar = (ROOT / "policy" / "cyberark_agent.cedar").read_text().replace("GATEWAY_ARN", gw_arn)

    if not out.get("policy_engine_id"):
        step("Creating policy engine")
        eng = ctl.create_policy_engine(name="cyberark_engineer_policy",
                                       description="Tool-call authorisation for CyberArk Engineer")
        engine_id = eng["policyEngineId"]
        step("Adding Cedar policy")
        ctl.create_policy(policyEngineId=engine_id, name="cyberark_agent",
                          definition={"cedar": {"statement": cedar}},
                          validationMode="FAIL_ON_ANY_FINDINGS")
        save_output(policy_engine_id=engine_id, policy_engine_arn=eng["policyEngineArn"])
        out = outputs()

    mode = "ENFORCE" if enforce else "LOG_ONLY"
    step(f"Attaching policy engine to gateway ({mode})")
    gw = ctl.get_gateway(gatewayIdentifier=gw_id)
    ctl.update_gateway(
        gatewayIdentifier=gw_id, name=gw["name"], roleArn=gw["roleArn"],
        protocolType=gw["protocolType"], authorizerType=gw["authorizerType"],
        authorizerConfiguration=gw["authorizerConfiguration"],
        policyEngineConfiguration={"arn": out["policy_engine_arn"], "mode": mode})
    print("policy engine attached in", mode)


if __name__ == "__main__":
    main()
