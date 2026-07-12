from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp.server.auth.providers.jwt import (
    JWTVerifier as FastMCPJWTVerifier,
    RSAKeyPair,
)
from joserfc import jwk
from pydantic import AnyHttpUrl

from geo_index.elasticsearch_config import ElasticsearchSettings
from geo_index.mcp_auth import (
    JWTVerifier,
    _valid_time_claims,
    create_auth,
    require_invited_subject,
)
from geo_index.mcp_settings import McpSettings


def _ctx(subject: object | None = None):
    token = None if subject is None else SimpleNamespace(claims={"sub": subject})
    return SimpleNamespace(token=token)


def _settings() -> McpSettings:
    return McpSettings(
        elasticsearch=ElasticsearchSettings(
            url="https://elastic.internal:9200",
            api_key="secret-api-key",
            active_model_key="gemini_embedding_2_3072_v1",
        ),
        public_base_url="https://geo.example.org",
        jwks_uri="https://login.example.org/jwks",
        issuer="https://login.example.org/",
        audience="geo-mcp",
        authorization_server="https://login.example.org",
        allowed_subjects=frozenset({"user-1"}),
        allowed_hosts=("geo.example.org",),
        allowed_origins=(),
    )


def test_invited_subject_is_allowed() -> None:
    assert require_invited_subject(frozenset({"user-1"}))(_ctx("user-1")) is True


def test_missing_blank_uninvited_or_nonstring_subject_is_denied() -> None:
    check = require_invited_subject(frozenset({"user-1", "123"}))
    assert check(_ctx()) is False
    assert check(_ctx(" ")) is False
    assert check(_ctx("user-2")) is False
    assert check(_ctx(123)) is False


def test_invited_subject_is_stripped_before_matching() -> None:
    assert require_invited_subject(frozenset({"user-1"}))(_ctx(" user-1 ")) is True


def test_create_auth_forwards_exact_remote_jwt_contract(monkeypatch) -> None:
    calls: dict[str, dict[str, object]] = {}
    verifier = object()
    provider = object()

    def fake_jwt_verifier(**kwargs):
        calls["verifier"] = kwargs
        return verifier

    def fake_remote_auth_provider(**kwargs):
        calls["provider"] = kwargs
        return provider

    monkeypatch.setattr("geo_index.mcp_auth.JWTVerifier", fake_jwt_verifier)
    monkeypatch.setattr(
        "geo_index.mcp_auth.RemoteAuthProvider", fake_remote_auth_provider
    )

    assert create_auth(_settings()) is provider
    assert calls["verifier"] == {
        "jwks_uri": "https://login.example.org/jwks",
        "issuer": "https://login.example.org/",
        "audience": "geo-mcp",
        "required_scopes": ["geo:read"],
    }
    assert calls["provider"] == {
        "token_verifier": verifier,
        "authorization_servers": [AnyHttpUrl("https://login.example.org")],
        "base_url": "https://geo.example.org",
        "scopes_supported": ["geo:read"],
    }
    assert not any("redirect" in key for key in calls["provider"])


def test_time_claims_require_expiration_and_reject_future_activation() -> None:
    now = time.time()
    assert _valid_time_claims({}, now=now) is False
    assert _valid_time_claims({"exp": None}, now=now) is False
    assert _valid_time_claims({"exp": "tomorrow"}, now=now) is False
    assert _valid_time_claims({"exp": now}, now=now) is False
    assert _valid_time_claims({"exp": now + 300, "nbf": now + 1}, now=now) is False
    assert _valid_time_claims({"exp": now + 300, "iat": now + 1}, now=now) is False
    for invalid in (None, "tomorrow", True, float("nan")):
        assert _valid_time_claims({"exp": now + 300, "nbf": invalid}, now=now) is False
        assert _valid_time_claims({"exp": now + 300, "iat": invalid}, now=now) is False
    assert _valid_time_claims(
        {"exp": now + 300, "nbf": now - 1, "iat": now - 1}, now=now
    ) is True


async def test_unknown_jwks_kids_share_a_bounded_refresh_window(monkeypatch) -> None:
    pair = RSAKeyPair.generate()
    key_data = jwk.import_key(pair.public_key, "RSA").as_dict()
    key_data["kid"] = "known-key"
    fetch = AsyncMock(return_value={"keys": [key_data]})
    monkeypatch.setattr(FastMCPJWTVerifier, "_fetch_jwks", fetch)
    verifier = JWTVerifier(jwks_uri="https://issuer.test/jwks")

    with pytest.raises(ValueError, match="Key ID"):
        await verifier._get_jwks_key("unknown-key-1")
    with pytest.raises(ValueError):
        await verifier._get_jwks_key("unknown-key-2")

    assert "BEGIN PUBLIC KEY" in await verifier._get_jwks_key("known-key")
    fetch.assert_awaited_once()
