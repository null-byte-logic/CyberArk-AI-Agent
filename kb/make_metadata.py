"""Generate Bedrock Knowledge Base metadata sidecars (<file>.metadata.json).

Layout expected under the source root (mirrors the S3 prefixes):

    product/<product>/<version>/<doc_type>/<file>      -> vendor documentation
    internal/v0.1/bau/<product>/<doc_type>/<file>      -> your runbooks, SOPs, KEDB, RCAs

Examples:
    product/privilege-cloud/current/guide/Privilege-Cloud-Admin.pdf
    internal/v0.1/bau/privilege-cloud/kedb/INC0012345.md

Usage:
    python kb/make_metadata.py ./kb-source            # writes sidecars next to files
    aws s3 sync ./kb-source s3://$KB_BUCKET/          # then upload
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PRODUCTS = {"pam-selfhosted", "privilege-cloud", "epm", "identity", "conjur",
            "secrets-hub", "sia", "sca", "general"}
DOC_TYPES = {"guide", "api", "release-notes", "known-issues",
             "runbook", "sop", "standard", "kedb", "rca"}
SKIP_SUFFIXES = {".json"}
COMPONENT_HINTS = {
    "cpm": "CPM", "psmp": "PSMP", "psm": "PSM", "pvwa": "PVWA", "vault": "Vault",
    "connector": "Connector", "pta": "PTA", "conjur": "Conjur", "epm": "EPM", "identity": "Identity",
}


def classify(rel: Path) -> dict | None:
    parts = rel.parts
    if parts[0] == "product" and len(parts) >= 5:
        product, version, doc_type = parts[1], parts[2], parts[3]
        source = "vendor"
    elif parts[:3] == ("internal", "v0.1", "bau") and len(parts) >= 6:
        product, version, doc_type = parts[3], "any", parts[4]
        source = "internal"
    else:
        return None
    if product not in PRODUCTS or doc_type not in DOC_TYPES:
        return None
    name = rel.name.lower()
    component = next((v for k, v in COMPONENT_HINTS.items() if k in name), "general")
    return {"metadataAttributes": {
        "product": product,
        "version": version,
        "doc_type": doc_type,
        "source": source,
        "component": component,
    }}


def main(root: str) -> int:
    base = Path(root)
    written, skipped = 0, []
    for f in sorted(base.rglob("*")):
        if not f.is_file() or f.suffix in SKIP_SUFFIXES or f.name.endswith(".metadata.json"):
            continue
        meta = classify(f.relative_to(base))
        if meta is None:
            skipped.append(str(f.relative_to(base)))
            continue
        f.with_name(f.name + ".metadata.json").write_text(json.dumps(meta, indent=2))
        written += 1
    print(f"wrote {written} sidecars")
    for s in skipped:
        print(f"skipped (path does not match layout): {s}")
    return 0 if not skipped else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "kb-source"))
