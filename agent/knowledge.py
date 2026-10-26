"""Knowledge Base retrieval tools (Bedrock Knowledge Bases, Retrieve API)."""
from __future__ import annotations

import json
import os
from functools import lru_cache

from strands import tool

from agent.config import Settings
from agent.safety import redact

_settings: Settings | None = None


def init(settings: Settings) -> None:
    global _settings
    _settings = settings


@lru_cache(maxsize=1)
def _client():
    import boto3
    assert _settings is not None, "knowledge.init() not called"
    return boto3.client("bedrock-agent-runtime", region_name=_settings.region)


def _filter(product: str | None, version: str | None, doc_type: str | None) -> dict | None:
    clauses = []
    if product:
        clauses.append({"equals": {"key": "product", "value": product}})
    if version:
        clauses.append({"startsWith": {"key": "version", "value": version}})
    if doc_type:
        clauses.append({"equals": {"key": "doc_type", "value": doc_type}})
    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else {"andAll": clauses}


def _retrieve(kb_id: str, query: str, flt: dict | None, k: int) -> list[dict]:
    # S3 Vectors stores support SEMANTIC only; set KB_SEARCH_TYPE=HYBRID on OpenSearch Serverless.
    search_type = os.environ.get("KB_SEARCH_TYPE", "SEMANTIC")
    cfg: dict = {"vectorSearchConfiguration": {
        "numberOfResults": k, "overrideSearchType": search_type}}
    if flt:
        cfg["vectorSearchConfiguration"]["filter"] = flt
    resp = _client().retrieve(knowledgeBaseId=kb_id,
                              retrievalQuery={"text": query},
                              retrievalConfiguration=cfg)
    out = []
    for r in resp.get("retrievalResults", []):
        loc = r.get("location", {})
        uri = (loc.get("s3Location") or {}).get("uri") or (loc.get("webLocation") or {}).get("url")
        out.append({"score": round(r.get("score", 0.0), 3),
                    "source": uri,
                    "metadata": {k: v for k, v in (r.get("metadata") or {}).items()
                                 if not k.startswith("x-amz")},
                    "text": redact(r["content"]["text"])})
    return out


@tool
def search_product_docs(query: str, product: str = "", version: str = "",
                        doc_type: str = "") -> str:
    """Search CyberArk product documentation.

    Args:
        query: the question or exact error string (e.g. "CACPM410E").
        product: pam-selfhosted | privilege-cloud | epm | identity | conjur | secrets-hub | sia
        version: product version prefix, e.g. "14.2". Leave empty if unknown.
        doc_type: guide | api | release-notes | known-issues. Optional.
    """
    assert _settings
    hits = _retrieve(_settings.kb_product_id, query,
                     _filter(product or None, version or None, doc_type or None), 8)
    if not hits and (product or version):
        hits = _retrieve(_settings.kb_product_id, query, None, 8)
    return json.dumps(hits[:5], indent=1)


@tool
def search_runbooks(query: str, product: str = "", doc_type: str = "") -> str:
    """Search internal runbooks, SOPs, standards and the known-error database (resolved tickets).

    Args:
        query: symptom or exact error string.
        product: optional product filter (same values as search_product_docs).
        doc_type: runbook | sop | standard | kedb | rca. Use kedb to find past fixes.
    """
    assert _settings
    hits = _retrieve(_settings.kb_ops_id, query,
                     _filter(product or None, None, doc_type or None), 8)
    return json.dumps(hits[:5], indent=1)
