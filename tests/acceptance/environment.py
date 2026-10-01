import os

import httpx


def before_all(context):
    context.base = os.environ.get("API_BASE", "http://localhost:8000") + "/api/v1"
    context.client = httpx.Client(base_url=context.base, timeout=30)
    r = context.client.post("/auth/dev-token", json={"tenant_id": 1, "role": "manager", "sub": "bdd-manager"})
    r.raise_for_status()
    context.token = r.json()["access_token"]
    context.client.headers["Authorization"] = f"Bearer {context.token}"


def after_all(context):
    context.client.close()
