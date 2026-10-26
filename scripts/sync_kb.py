"""Start ingestion jobs for both knowledge bases and wait for them to finish."""
from __future__ import annotations

import time

from common import load_env, outputs, session, step


def main() -> None:
    load_env()
    agent = session().client("bedrock-agent")
    for key in ("kb_product_id", "kb_ops_id"):
        kb = outputs()[key]
        ds = agent.list_data_sources(knowledgeBaseId=kb)["dataSourceSummaries"][0]["dataSourceId"]
        step(f"Ingesting {key} ({kb})")
        job = agent.start_ingestion_job(knowledgeBaseId=kb, dataSourceId=ds)["ingestionJob"]
        while True:
            j = agent.get_ingestion_job(knowledgeBaseId=kb, dataSourceId=ds,
                                        ingestionJobId=job["ingestionJobId"])["ingestionJob"]
            if j["status"] in ("COMPLETE", "FAILED", "STOPPED"):
                print(j["status"], j.get("statistics"), j.get("failureReasons", ""))
                break
            time.sleep(10)


if __name__ == "__main__":
    main()
