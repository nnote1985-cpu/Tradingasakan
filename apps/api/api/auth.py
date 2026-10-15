"""Supabase JWT verification and operator allowlist (BUILDPLAN v4.2 §22.1).

The bearer token's signature, issuer, audience, exp and nbf are verified
against the project's JWKS; roles come from trading.operator_roles in the
database on every request, never from the token or the browser.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

import jwt

from trading.contracts.enums import OperatorRole

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

ROLE_RANK = {OperatorRole.VIEWER: 0, OperatorRole.OPERATOR: 1, OperatorRole.ADMIN: 2}


class AuthError(Exception):
    def __init__(self, code: str, message_th: str, status: int = 401) -> None:
        super().__init__(code)
        self.code, self.message_th, self.status = code, message_th, status


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    roles: frozenset[OperatorRole]

    def at_least(self, role: OperatorRole) -> bool:
        have = [ROLE_RANK[r] for r in self.roles if r in ROLE_RANK]
        return bool(have) and max(have) >= ROLE_RANK[role]


class TokenVerifier(Protocol):
    def verify(self, token: str) -> UUID: ...


class RoleStore(Protocol):
    async def active_roles(self, user_id: UUID) -> frozenset[OperatorRole]: ...


class SupabaseJwtVerifier:
    """Asymmetric Supabase Auth tokens checked against the project's JWKS (cached, rotation-aware)."""

    ALGORITHMS = ["ES256", "RS256", "EdDSA"]

    def __init__(self, supabase_url: str, audience: str = "authenticated", jwks_client: jwt.PyJWKClient | None = None):
        base = supabase_url.rstrip("/")
        self.issuer = f"{base}/auth/v1"
        self.audience = audience
        self.jwks = jwks_client or jwt.PyJWKClient(f"{self.issuer}/.well-known/jwks.json", cache_keys=True, lifespan=600)

    def verify(self, token: str) -> UUID:
        try:
            key = self.jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(token, key.key, algorithms=self.ALGORITHMS, audience=self.audience, issuer=self.issuer,
                                options={"require": ["exp", "sub", "iss", "aud"]})
        except jwt.PyJWTError as e:
            raise AuthError("invalid_token", "โทเคนไม่ถูกต้องหรือหมดอายุ กรุณาเข้าสู่ระบบใหม่") from e
        try:
            return UUID(claims["sub"])
        except ValueError as e:
            raise AuthError("invalid_token", "โทเคนไม่ถูกต้อง") from e


class PgRoleStore:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def active_roles(self, user_id: UUID) -> frozenset[OperatorRole]:
        async with self.pool.connection() as conn:
            cur = await conn.execute("select role from trading.operator_roles where user_id = %s and active", (user_id,))
            return frozenset(OperatorRole(r[0]) for r in await cur.fetchall())


async def authenticate(header: str | None, verifier: TokenVerifier, roles: RoleStore) -> Principal:
    if not header or not header.lower().startswith("bearer "):
        raise AuthError("missing_token", "ต้องเข้าสู่ระบบก่อน")
    uid = verifier.verify(header.split(" ", 1)[1].strip())
    have = await roles.active_roles(uid)
    if not have:
        raise AuthError("not_allowlisted", "บัญชีนี้ยังไม่ได้รับสิทธิ์ใช้งาน", status=403)
    return Principal(uid, have)
