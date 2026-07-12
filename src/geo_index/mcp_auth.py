from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable, Mapping

from fastmcp.server.auth import AuthContext, RemoteAuthProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier as FastMCPJWTVerifier
from pydantic import AnyHttpUrl

from .mcp_settings import McpSettings


AuthCheck = Callable[[AuthContext], bool]
JWKS_REFRESH_COOLDOWN_SECONDS = 30.0


def _numeric_date(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _valid_time_claims(
    claims: Mapping[str, object], *, now: float | None = None
) -> bool:
    current = time.time() if now is None else now
    expiration = _numeric_date(claims.get("exp"))
    if expiration is None or expiration <= current:
        return False
    for name in ("nbf", "iat"):
        if name not in claims:
            continue
        timestamp = _numeric_date(claims[name])
        if timestamp is None or timestamp > current:
            return False
    return True


class JWTVerifier(FastMCPJWTVerifier):
    """FastMCP JWT verification with required, fail-closed time claims."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.jwks_refresh_cooldown_seconds = JWKS_REFRESH_COOLDOWN_SECONDS
        self._jwks_refresh_lock = asyncio.Lock()
        self._jwks_next_refresh_at = 0.0

    def _cached_jwks_key(self, kid: str | None) -> str | None:
        if time.time() - self._jwks_cache_time >= self._cache_ttl:
            return None
        if kid is not None:
            return self._jwks_cache.get(kid)
        if len(self._jwks_cache) == 1:
            return next(iter(self._jwks_cache.values()))
        return None

    async def _get_jwks_key(self, kid: str | None) -> str:
        cached = self._cached_jwks_key(kid)
        if cached is not None:
            return cached
        async with self._jwks_refresh_lock:
            cached = self._cached_jwks_key(kid)
            if cached is not None:
                return cached
            now = time.monotonic()
            if now < self._jwks_next_refresh_at:
                raise ValueError("JWKS refresh is temporarily rate limited")
            self._jwks_next_refresh_at = now + self.jwks_refresh_cooldown_seconds
            return await super()._get_jwks_key(kid)

    async def verify_token(self, token: str):
        access_token = await super().verify_token(token)
        if access_token is None or not _valid_time_claims(access_token.claims):
            return None
        return access_token


def require_invited_subject(subjects: frozenset[str]) -> AuthCheck:
    def check(ctx: AuthContext) -> bool:
        if ctx.token is None:
            return False
        subject = ctx.token.claims.get("sub")
        return (
            isinstance(subject, str)
            and bool(subject.strip())
            and subject.strip() in subjects
        )
    return check


def create_auth(settings: McpSettings) -> RemoteAuthProvider:
    verifier = JWTVerifier(
        jwks_uri=settings.jwks_uri,
        issuer=settings.issuer,
        audience=settings.audience,
        required_scopes=[settings.required_scope],
    )
    return RemoteAuthProvider(
        token_verifier=verifier,
        authorization_servers=[AnyHttpUrl(settings.authorization_server)],
        base_url=settings.public_base_url,
        scopes_supported=[settings.required_scope],
    )
