"""API shell (BUILDPLAN v4.2 §22): health, identity and overview. No trading
loop runs here (§21.2) and no endpoint places orders."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg_pool import PoolTimeout

from api.ai import ai_settings, scorecard
from api.auth import AuthError, Principal, RoleStore, TokenVerifier, authenticate
from api.portfolios import NoPortfolios, PortfolioStore, Unavailable, decision_items, equity_series, overview_items, risk_items
from trading.config import Settings
from trading.contracts.enums import OperatorRole


def create_app(settings: Settings, verifier: TokenVerifier, roles: RoleStore,
               portfolios: PortfolioStore | None = None) -> FastAPI:
    store: PortfolioStore = portfolios or NoPortfolios()
    app = FastAPI(title="asakantrading API", version="0.1.0")
    if settings.allowed_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(settings.allowed_origins),
                           allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type", "Idempotency-Key"])

    @app.middleware("http")
    async def request_id(request: Request, call_next: Callable[[Request], Awaitable[Any]]) -> Any:
        request.state.request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        response = await call_next(request)
        response.headers["x-request-id"] = request.state.request_id
        return response

    @app.exception_handler(AuthError)
    async def auth_error(request: Request, exc: AuthError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content={
            "code": exc.code, "request_id": getattr(request.state, "request_id", None),
            "retryable": False, "message": exc.message_th})

    @app.exception_handler(PoolTimeout)
    async def database_unavailable(request: Request, exc: PoolTimeout) -> JSONResponse:
        return JSONResponse(status_code=503, content={
            "code": "database_unavailable", "request_id": getattr(request.state, "request_id", None),
            "retryable": True, "message": "เชื่อมต่อฐานข้อมูลไม่ได้ กรุณาตรวจ DATABASE_URL ในไฟล์ .env ของ Backend"})

    async def principal(request: Request) -> Principal:
        return await authenticate(request.headers.get("authorization"), verifier, roles)

    def require(role: OperatorRole) -> Callable[[Principal], Principal]:
        def check(p: Principal = Depends(principal)) -> Principal:
            if not p.at_least(role):
                raise AuthError("forbidden", "สิทธิ์ไม่พอสำหรับคำสั่งนี้", status=403)
            return p
        return check

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "mode": settings.trading_mode.value, "live_enabled": settings.live_enabled}

    @app.get("/api/v1/me")
    async def me(p: Principal = Depends(principal)) -> dict[str, Any]:
        return {"user_id": str(p.user_id), "roles": sorted(r.value for r in p.roles)}

    @app.get("/api/v1/overview")
    async def overview(p: Principal = Depends(require(OperatorRole.VIEWER))) -> dict[str, Any]:
        return {"mode": settings.trading_mode.value, "live_enabled": settings.live_enabled,
                "portfolios": overview_items(await store.overview_rows())}

    # Labels contain "/" (e.g. pathA-v1.2/paper), hence the path converter.
    @app.get("/api/v1/portfolios/{label:path}/equity")
    async def equity(label: str, p: Principal = Depends(require(OperatorRole.VIEWER))) -> dict[str, Any]:
        row = await store.portfolio(label)
        if row is None:
            raise AuthError("not_found", "ไม่พบพอร์ตนี้", status=404)
        return equity_series(row, await store.equity_rows(label))

    @app.get("/api/v1/risk-status")
    async def risk_status(p: Principal = Depends(require(OperatorRole.VIEWER))) -> dict[str, Any]:
        try:
            return {"available": True, "portfolios": risk_items(await store.risk_rows())}
        except Unavailable:
            return {"available": False, "portfolios": []}

    @app.get("/api/v1/ai/settings")
    async def ai_settings_view(p: Principal = Depends(require(OperatorRole.VIEWER))) -> dict[str, Any]:
        return ai_settings()

    @app.get("/api/v1/portfolios/{label:path}/ai-scorecard")
    async def ai_scorecard(label: str, p: Principal = Depends(require(OperatorRole.VIEWER))) -> dict[str, Any]:
        if await store.portfolio(label) is None:
            raise AuthError("not_found", "ไม่พบพอร์ตนี้", status=404)
        try:
            items = decision_items(await store.decision_rows(label))["items"]
        except Unavailable:
            return {"available": False, "horizon_days": 0, "items": [], "counts": {}, "diagnostic_only": True}
        return {"available": True, **scorecard(items, await store.equity_rows(label))}

    @app.get("/api/v1/portfolios/{label:path}/decisions")
    async def decisions(label: str, p: Principal = Depends(require(OperatorRole.VIEWER))) -> dict[str, Any]:
        if await store.portfolio(label) is None:
            raise AuthError("not_found", "ไม่พบพอร์ตนี้", status=404)
        try:
            return decision_items(await store.decision_rows(label))
        except Unavailable:
            return {"available": False, "items": [], "counts": {}, "cost_usd": "0", "limit": 0}

    return app


def app_from_env() -> FastAPI:   # uvicorn --factory api.main:app_from_env
    from psycopg_pool import AsyncConnectionPool

    from api.auth import PgRoleStore, SupabaseJwtVerifier
    from api.portfolios import PgPortfolioStore
    from trading.config import load_settings

    s = load_settings()
    if not s.supabase_url or not s.database_url:
        raise RuntimeError("SUPABASE_URL and DATABASE_URL are required")
    pool = AsyncConnectionPool(s.database_url, open=False, timeout=5)
    app = create_app(s, SupabaseJwtVerifier(s.supabase_url), PgRoleStore(pool), PgPortfolioStore(pool))
    app.router.on_startup.append(pool.open)
    app.router.on_shutdown.append(pool.close)
    return app
