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
from geo_index.mcp_auth import (
    JWTVerifier,
    _valid_time_claims,
    require_invited_subject,
)


def _ctx(subject: object | None = None):
    token = None if subject is None else SimpleNamespace(claims={"sub": subject})
    return SimpleNamespace(token=token)


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
