"""Create the KB source bucket, an S3 Vectors store and the two Bedrock Knowledge Bases.

* kb-cyberark-product : s3://$KB_BUCKET/product/
* kb-cyberark-ops     : s3://$KB_BUCKET/internal/v0.1/bau/   (same prefix as your old KB)

Embeddings: Amazon Titan Text Embeddings V2 (1024 dims). S3 Vectors keeps
cost low for this corpus size; it supports semantic search with metadata
filters (set KB_SEARCH_TYPE=SEMANTIC, the default).

The old knowledge base W0CYQGHFSK is left untouched; delete it yourself once
the new ones are working.
"""
from __future__ import annotations

import os
import time

from common import ensure_role, load_env, outputs, save_output, session, step

EMBED_MODEL = "amazon.titan-embed-text-v2:0"
DIM = 1024


def ensure_bucket(s3, bucket: str, region: str) -> None:
    try:
        s3.head_bucket(Bucket=bucket)
        print("bucket exists:", bucket)
        return
    except Exception:
        pass
    step(f"Creating bucket {bucket}")
    s3.create_bucket(Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": region})
    s3.put_public_access_block(Bucket=bucket, PublicAccessBlockConfiguration={
        "BlockPublicAcls": True, "IgnorePublicAcls": True,
        "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
    s3.put_bucket_encryption(Bucket=bucket, ServerSideEncryptionConfiguration={
        "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "aws:kms"},
                   "BucketKeyEnabled": True}]})
    s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    for prefix in (os.environ["KB_PRODUCT_PREFIX"], os.environ["KB_OPS_PREFIX"]):
        s3.put_object(Bucket=bucket, Key=prefix)


def ensure_vectors(s3v, name: str, index: str) -> tuple[str, str]:
    try:
        s3v.create_vector_bucket(vectorBucketName=name)
    except s3v.exceptions.ConflictException:
        pass
    try:
        s3v.create_index(vectorBucketName=name, indexName=index, dataType="float32",
                         dimension=DIM, distanceMetric="cosine",
                         metadataConfiguration={"nonFilterableMetadataKeys": ["AMAZON_BEDROCK_TEXT"]})
    except s3v.exceptions.ConflictException:
        pass
    idx = s3v.get_index(vectorBucketName=name, indexName=index)["index"]
    return idx["vectorBucketName"], idx["indexArn"]


def create_kb(agent, name: str, role_arn: str, vbucket_arn: str, index_arn: str,
              bucket: str, prefix: str, region: str) -> str:
    step(f"Creating knowledge base {name}")
    kb = agent.create_knowledge_base(
        name=name, roleArn=role_arn,
        knowledgeBaseConfiguration={"type": "VECTOR", "vectorKnowledgeBaseConfiguration": {
            "embeddingModelArn": f"arn:aws:bedrock:{region}::foundation-model/{EMBED_MODEL}",
            "embeddingModelConfiguration": {"bedrockEmbeddingModelConfiguration": {
                "dimensions": DIM, "embeddingDataType": "FLOAT32"}}}},
        storageConfiguration={"type": "S3_VECTORS", "s3VectorsConfiguration": {
            "vectorBucketArn": vbucket_arn, "indexArn": index_arn}},
    )["knowledgeBase"]
    kb_id = kb["knowledgeBaseId"]
    for _ in range(30):
        if agent.get_knowledge_base(knowledgeBaseId=kb_id)["knowledgeBase"]["status"] == "ACTIVE":
            break
        time.sleep(5)
    agent.create_data_source(
        knowledgeBaseId=kb_id, name=f"{name}-s3",
        dataSourceConfiguration={"type": "S3", "s3Configuration": {
            "bucketArn": f"arn:aws:s3:::{bucket}", "inclusionPrefixes": [prefix]}},
        vectorIngestionConfiguration={"chunkingConfiguration": {
            "chunkingStrategy": "HIERARCHICAL",
            "hierarchicalChunkingConfiguration": {
                "levelConfigurations": [{"maxTokens": 1500}, {"maxTokens": 300}],
                "overlapTokens": 60}}},
    )
    return kb_id


def main() -> None:
    env = load_env()
    if outputs().get("kb_product_id") and outputs().get("kb_ops_id"):
        print("knowledge bases already created")
        return
    region, account, bucket = env["AWS_REGION"], env["AWS_ACCOUNT_ID"], env["KB_BUCKET"]
    sess = session()
    ensure_bucket(sess.client("s3"), bucket, region)

    s3v = sess.client("s3vectors")
    vb_name = f"{env['PROJECT']}-vectors-{account}"
    ids = {}
    for kind, prefix in (("product", env["KB_PRODUCT_PREFIX"]), ("ops", env["KB_OPS_PREFIX"])):
        _, index_arn = ensure_vectors(s3v, vb_name, f"kb-{kind}")
        vbucket_arn = index_arn.split("/index/")[0]
        role = ensure_role(f"{env['PROJECT']}-kb-{kind}-role", "bedrock.amazonaws.com", {
            "Version": "2012-10-17", "Statement": [
                {"Effect": "Allow", "Action": ["bedrock:InvokeModel"],
                 "Resource": f"arn:aws:bedrock:{region}::foundation-model/{EMBED_MODEL}"},
                {"Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": f"arn:aws:s3:::{bucket}",
                 "Condition": {"StringLike": {"s3:prefix": [f"{prefix}*"]}}},
                {"Effect": "Allow", "Action": ["s3:GetObject"], "Resource": f"arn:aws:s3:::{bucket}/{prefix}*"},
                {"Effect": "Allow", "Action": ["kms:Decrypt"], "Resource": "*",
                 "Condition": {"StringEquals": {"kms:ViaService": f"s3.{region}.amazonaws.com"}}},
                {"Effect": "Allow", "Action": ["s3vectors:*"], "Resource": [vbucket_arn, index_arn]},
            ]})
        ids[kind] = create_kb(sess.client("bedrock-agent"), f"kb-cyberark-{kind}", role,
                              vbucket_arn, index_arn, bucket, prefix, region)
    save_output(kb_product_id=ids["product"], kb_ops_id=ids["ops"], kb_bucket=bucket)
    print("KB product:", ids["product"], " KB ops:", ids["ops"])
    print("Next: put documents under kb-source/, run kb/make_metadata.py, sync to S3, then scripts/sync_kb.py")


if __name__ == "__main__":
    main()
