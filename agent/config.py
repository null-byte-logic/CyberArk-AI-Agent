"""Runtime configuration read from environment variables set on the
AgentCore Runtime (see scripts/deploy.sh and .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"Missing required environment variable {name}")
    return value


@dataclass(frozen=True)
class Settings:
    region: str = field(default_factory=lambda: _env("AWS_REGION", "ap-southeast-2"))
    gateway_url: str = field(default_factory=lambda: _env("GATEWAY_MCP_URL"))
    gateway_oauth_provider: str = field(default_factory=lambda: _env("GATEWAY_OAUTH_PROVIDER", "gateway-oauth"))
    gateway_scope: str = field(default_factory=lambda: _env("GATEWAY_SCOPE", "cyberark-agent/invoke"))
    memory_id: str = field(default_factory=lambda: _env("MEMORY_ID"))
    kb_product_id: str = field(default_factory=lambda: _env("KB_PRODUCT_ID"))
    kb_ops_id: str = field(default_factory=lambda: _env("KB_OPS_ID"))
    guardrail_id: str = field(default_factory=lambda: _env("GUARDRAIL_ID"))
    guardrail_version: str = field(default_factory=lambda: _env("GUARDRAIL_VERSION", "DRAFT"))
    # Bedrock model / inference-profile IDs. Use the IDs enabled in your account.
    model_supervisor: str = field(default_factory=lambda: _env("MODEL_SUPERVISOR"))
    model_fast: str = field(default_factory=lambda: _env("MODEL_FAST"))
    model_deep: str = field(default_factory=lambda: _env("MODEL_DEEP"))
    max_write_calls_per_session: int = field(
        default_factory=lambda: int(_env("MAX_WRITE_CALLS", "20")))
