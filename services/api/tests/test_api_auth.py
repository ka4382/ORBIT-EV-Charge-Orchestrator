from __future__ import annotations

import time

import jwt
import pytest
from api_auth import auth, token
from orbit_common.config import settings


async def test_healthz_open(client):
    r = await client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "x-request-id" in r.headers


async def test_readyz_reports_components(client):
    r = await client.get("/readyz")
    assert r.status_code == 503  # no postgres in unit tests
    assert r.json() == {"kafka": True, "postgres": False, "redis": True}


async def test_dev_token_roundtrip(client):
    r = await client.post("/api/v1/auth/dev-token", json={"tenant_id": 1, "role": "admin"})
    assert r.status_code == 200
    body = r.json()
    assert body["expires_in"] == 3600
    me = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["tenant_id"] == 1 and me.json()["role"] == "admin"


async def test_dev_token_rejects_bad_role(client):
    r = await client.post("/api/v1/auth/dev-token", json={"tenant_id": 1, "role": "root"})
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/problem+json")


async def test_dev_token_disabled_outside_dev(client, monkeypatch):
    monkeypatch.setattr(settings(), "orbit_env", "prod")
    r = await client.post("/api/v1/auth/dev-token", json={"tenant_id": 1, "role": "admin"})
    assert r.status_code == 404


async def test_missing_token(client):
    r = await client.get("/api/v1/me")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    assert r.json()["type"] == "urn:orbit:problem:unauthorized"


async def test_expired_token(client):
    r = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token(ttl_s=-10)}"})
    assert r.status_code == 401
    assert r.json()["detail"] == "token expired"


async def test_wrong_secret(client):
    bad = jwt.encode(
        {
            "sub": "x",
            "tenant_id": 1,
            "role": "admin",
            "iss": settings().jwt_issuer,
            "exp": int(time.time()) + 60,
        },
        "not-the-secret",
        algorithm="HS256",
    )
    r = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {bad}"})
    assert r.status_code == 401


async def test_wrong_issuer(client):
    bad = jwt.encode(
        {"sub": "x", "tenant_id": 1, "role": "admin", "iss": "someone-else", "exp": int(time.time()) + 60},
        settings().jwt_dev_secret,
        algorithm="HS256",
    )
    r = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {bad}"})
    assert r.status_code == 401


@pytest.mark.parametrize(
    "claims",
    [
        {"tenant_id": 1, "role": "admin"},  # no sub
        {"sub": "x", "role": "admin"},  # no tenant
        {"sub": "x", "tenant_id": "1", "role": "admin"},  # tenant not int
        {"sub": "x", "tenant_id": 1, "role": "superuser"},  # unknown role
    ],
)
async def test_malformed_claims(client, claims):
    bad = jwt.encode(
        {**claims, "iss": settings().jwt_issuer, "exp": int(time.time()) + 60},
        settings().jwt_dev_secret,
        algorithm="HS256",
    )
    r = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {bad}"})
    assert r.status_code == 401


async def test_wrong_role_forbidden(client):
    r = await client.post(
        "/api/v1/plans",
        headers=auth(role="finance"),
        json={"vin": "1HGCM82633A004352", "target_soc": 80, "depart_ts": "2030-01-01T00:00:00Z"},
    )
    assert r.status_code == 403
    assert r.json()["type"] == "urn:orbit:problem:forbidden"


async def test_tenant_mismatch_is_404(client):
    """A tenant-2 token cannot see tenant-1's vehicle: the scoped repository simply has no such row."""
    r = await client.get("/api/v1/vehicles/1HGCM82633A004352", headers=auth(tenant_id=2))
    assert r.status_code == 404
    r = await client.get("/api/v1/vehicles/1HGCM82633A004352", headers=auth(tenant_id=1))
    assert r.status_code == 200


async def test_rs256_path_uses_jwks(client, monkeypatch):
    """With JWKS_URL set, HS256 dev tokens are rejected and the JWKS client is consulted."""
    from orbit_api import auth as auth_mod

    class FakeKey:
        key = "irrelevant"

    class FakeClient:
        def get_signing_key_from_jwt(self, tok):
            return FakeKey()

    monkeypatch.setattr(settings(), "jwks_url", "https://idp.example/jwks")
    monkeypatch.setattr(auth_mod, "_jwks_client", lambda url: FakeClient())
    r = await client.get("/api/v1/me", headers=auth())
    assert r.status_code == 401
