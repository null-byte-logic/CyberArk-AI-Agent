"""Create (or reuse) the Bedrock Guardrail and publish a version."""
from __future__ import annotations

import json

from common import ROOT, load_env, outputs, save_output, session, step


def main() -> None:
    load_env()
    if outputs().get("guardrail_id"):
        print("guardrail already created:", outputs()["guardrail_id"])
        return
    cfg = json.loads((ROOT / "guardrails" / "guardrail.json").read_text())
    br = session().client("bedrock")
    step("Creating Bedrock Guardrail")
    g = br.create_guardrail(**cfg)
    v = br.create_guardrail_version(guardrailIdentifier=g["guardrailId"],
                                    description="initial")
    save_output(guardrail_id=g["guardrailId"], guardrail_arn=g["guardrailArn"],
                guardrail_version=v["version"])
    print("guardrail", g["guardrailId"], "version", v["version"])


if __name__ == "__main__":
    main()
