"""Token helpers for API tests. Kept out of conftest so test modules can import them by a unique name."""

from __future__ import annotations

import os

os.environ.setdefault("ORBIT_ENV", "dev")
os.environ.setdefault("JWT_DEV_SECRET", "unit-test-secret")
os.environ.setdefault("JWKS_URL", "")
os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", "")

from orbit_api.auth import mint_dev_token  # noqa: E402


def token(tenant_id: int = 1, role: str = "manager", sub: str = "user-1", ttl_s: int = 3600) -> str:
    return mint_dev_token(tenant_id, role, sub, ttl_s=ttl_s)


def auth(tenant_id: int = 1, role: str = "manager", sub: str = "user-1") -> dict[str, str]:
    return {"Authorization": f"Bearer {token(tenant_id, role, sub)}"}
