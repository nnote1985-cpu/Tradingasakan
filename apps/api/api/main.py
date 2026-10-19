"""API shell (BUILDPLAN v4.2 §22): health, identity and overview. No trading
loop runs here (§21.2) and no endpoint places orders."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg_pool import PoolTimeout
from pydantic import BaseModel, ConfigDict, Field

from api.ai import ai_settings, scorecard
from api.assets import ASSETS, AssetCache
from api.auth import AuthError, Principal, RoleStore, TokenVerifier, authenticate
from api.backtests import load_backtests
from api.portfolios import NoPortfolios, PortfolioStore, Unavailable, decision_items, equity_series, overview_items, risk_items
from api.profile_drafts import DraftConflict, DraftStore, DraftsUnavailable, NoDrafts, PgDraftStore, SaveDraft
from api.strategies import catalog_view, profiles_view
from api.workers import ACTIVITY_LIMIT, CONTROL_LIMIT, NoWorkers, PgWorkerStore, WorkerStore, activity_items, control_items, pnl_items, worker_items
from api.workers import Unavailable as WorkersUnavailable
from trading.config import Settings
from trading.contracts.enums import OperatorRole


class OperatorRequest(BaseModel):
    """HALT or RESUME from the Worker tab; Guardian carries it out (runbook §12)."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: Literal["HALT", "RESUME"]
    reason: str = Field(min_length=1, max_length=200)


def create_app(settings: Settings, verifier: TokenVerifier, roles: RoleStore,
               portfolios: PortfolioStore | None = None, assets: AssetCache | None = None,
               workers: WorkerStore | None = None, drafts: DraftStore | None = None) -> FastAPI:
    store: PortfolioStore = portfolios or NoPortfolios()
    worker_store: WorkerStore = workers or NoWorkers()
    market = assets or AssetCache()
    draft_store: DraftStore = drafts or NoDrafts()
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

    @app.exception_handler(DraftsUnavailable)
    async def drafts_unavailable(request: Request, exc: DraftsUnavailable) -> JSONResponse:
        return JSONResponse(status_code=503, content={
            "code": "drafts_unavailable", "request_id": getattr(request.state, "request_id", None),
            "retryable": True, "message": "ยังไม่มีตารางเก็บร่างในฐานข้อมูล หรือยังไม่ได้ตั้ง DATABASE_URL ของ Backend"})

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

    @app.get("/api/v1/backtests")
    async def backtests(market: str | None = None, p: Principal = Depends(require(OperatorRole.VIEWER))) -> dict[str, Any]:
        return {"items": load_backtests(market)}

    @app.get("/api/v1/assets")
    async def asset_list(p: Principal = Depends(require(OperatorRole.VIEWER))) -> dict[str, Any]:
        return {"items": list(ASSETS.values())}

    @app.get("/api/v1/assets/{symbol}")
    async def asset(symbol: str, p: Principal = Depends(require(OperatorRole.VIEWER))) -> dict[str, Any]:
        if symbol not in ASSETS:
            raise AuthError("not_found", "ยังไม่รองรับสินทรัพย์นี้", status=404)
        return await market.get(symbol)

    @app.get("/api/v1/strategies")
    async def strategies(p: Principal = Depends(require(OperatorRole.VIEWER))) -> dict[str, Any]:
        return catalog_view()

    @app.get("/api/v1/bot-profiles")
    async def bot_profiles(p: Principal = Depends(require(OperatorRole.VIEWER))) -> dict[str, Any]:
        return profiles_view()

    @app.get("/api/v1/bot-profile-drafts")
    async def bot_profile_drafts(p: Principal = Depends(require(OperatorRole.VIEWER))) -> dict[str, Any]:
        return {"items": await draft_store.list_latest(), "can_edit": p.at_least(OperatorRole.OPERATOR)}

    @app.post("/api/v1/bot-profile-drafts")
    async def save_bot_profile_draft(body: SaveDraft, p: Principal = Depends(require(OperatorRole.OPERATOR))) -> dict[str, Any]:
        try:
            return await draft_store.save(body, p.user_id)
        except DraftConflict as e:
            raise AuthError("draft_conflict", "มีการเปลี่ยนแปลงร่างนี้แล้ว กรุณาโหลดรายการล่าสุดก่อนแก้ไขอีกครั้ง", status=409) from e

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

    @app.get("/api/v1/workers")
    async def worker_status(p: Principal = Depends(require(OperatorRole.VIEWER))) -> dict[str, Any]:
        try:
            return {"available": True, "workers": worker_items(await worker_store.worker_rows())}
        except WorkersUnavailable:
            return {"available": False, "workers": []}

    @app.get("/api/v1/workers/{account}/activity")
    async def worker_activity(account: str, p: Principal = Depends(require(OperatorRole.VIEWER))) -> dict[str, Any]:
        """Latest Testnet orders and trades for the account (testnet-development-plan.md §9; read-only)."""
        try:
            orders, trades = await worker_store.activity_rows(account, ACTIVITY_LIMIT)
            balances, books, mark = await worker_store.pnl_rows(account)
        except WorkersUnavailable:
            return {"available": False, "orders": [], "trades": [], "pnl": None}
        return {"available": True, **activity_items(orders, trades), "pnl": pnl_items(balances, books, mark)}

    @app.get("/api/v1/workers/{account}/control")
    async def worker_control(account: str, p: Principal = Depends(require(OperatorRole.VIEWER))) -> dict[str, Any]:
        """R10a decisions, Guardian events and HALT/RESUME requests (P5)."""
        try:
            decisions, events, requests = await worker_store.control_rows(account, CONTROL_LIMIT)
        except WorkersUnavailable:
            return {"available": False, "decisions": [], "risk_events": [], "requests": []}
        return {"available": True, **control_items(decisions, events, requests)}

    @app.post("/api/v1/workers/{account}/requests")
    async def worker_request(account: str, body: OperatorRequest, request: Request,
                             p: Principal = Depends(require(OperatorRole.OPERATOR))) -> Any:
        """Queue a HALT or RESUME. The worker acts on it within a tick and writes the outcome to the same row."""
        try:
            rid = await worker_store.add_request(account, body.kind, f"web:{p.user_id}", body.reason)
        except WorkersUnavailable:
            return JSONResponse(status_code=503, content={
                "code": "requests_unavailable", "request_id": getattr(request.state, "request_id", None), "retryable": True,
                "message": "ยังไม่มีตารางคำสั่ง HALT/RESUME ในฐานข้อมูล"})
        return {"id": rid, "kind": body.kind}

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
    app = create_app(s, SupabaseJwtVerifier(s.supabase_url), PgRoleStore(pool), PgPortfolioStore(pool),
                     workers=PgWorkerStore(pool), drafts=PgDraftStore(pool))
    app.router.on_startup.append(pool.open)
    app.router.on_shutdown.append(pool.close)
    return app
