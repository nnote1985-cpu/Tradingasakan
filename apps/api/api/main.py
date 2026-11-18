"""API shell (BUILDPLAN v4.2 §22): health, identity and overview. No trading
loop runs here (§21.2) and no endpoint places orders."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Any, Literal

from fastapi import Depends, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from psycopg_pool import PoolTimeout
from pydantic import BaseModel, ConfigDict, Field

from api.ai import ai_settings, scorecard
from api.assets import ASSETS, AssetCache
from api.auth import AuthError, Principal, RoleStore, TokenVerifier, authenticate
from api.backtests import load_backtests
from api.bots import BotEvent, BotNotFound, BotRefused, BotStore, BotsUnavailable, NoBots, PgBotStore, SaveBot, template_list
from api.charts import BadRequest, ChartService, Loading, PgBarStore, utc
from api.futures import (
    CloseTicket,
    FuturesStore,
    NoFutures,
    OpenTicket,
    PgFuturesStore,
    Refused,
    StopTicket,
    preview,
    submit_close,
    submit_open,
    submit_stop,
)
from api.futures import desk as futures_desk
from api.overlays import NoOverlays, OverlayStore, PgOverlayStore, overlay_items
from api.overlays import Unavailable as OverlaysUnavailable
from api.paper_compare import CompareStore, NoCompare, PgCompareStore, compare_view
from api.portfolios import NoPortfolios, PortfolioStore, Unavailable, decision_items, equity_series, overview_items, risk_items
from api.profile_drafts import DraftConflict, DraftStore, DraftsUnavailable, NoDrafts, PgDraftStore, SaveDraft
from api.runs import backtest_run, run_list, shadow_run
from api.strategies import catalog_view, profiles_view
from api.stream import MAX_AGE, PING_EVERY, ChangeWatcher, CursorSource, NoCursors, PgCursorSource, event_stream
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
               workers: WorkerStore | None = None, drafts: DraftStore | None = None,
               charts: ChartService | None = None, overlays: OverlayStore | None = None,
               bots: BotStore | None = None, cursors: CursorSource | None = None,
               compare: CompareStore | None = None, futures: FuturesStore | None = None,
               stream_max_age: float = MAX_AGE, stream_ping_every: float = PING_EVERY) -> FastAPI:
    store: PortfolioStore = portfolios or NoPortfolios()
    worker_store: WorkerStore = workers or NoWorkers()
    market = assets or AssetCache()
    draft_store: DraftStore = drafts or NoDrafts()
    chart = charts or ChartService()
    overlay_store: OverlayStore = overlays or NoOverlays()
    bot_store: BotStore = bots or NoBots()
    compare_store: CompareStore = compare or NoCompare()
    futures_store: FuturesStore = futures or NoFutures()
    watcher = ChangeWatcher(cursors or NoCursors())
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

    @app.get("/api/v1/paper-compare")
    async def paper_compare(start: date | None = Query(None, alias="from"),
                            p: Principal = Depends(require(OperatorRole.VIEWER))) -> dict[str, Any]:
        return compare_view(await compare_store.snapshot_rows(), await compare_store.total_rows(),
                            await compare_store.fill_rows(), start)

    @app.get("/api/v1/risk-status")
    async def risk_status(p: Principal = Depends(require(OperatorRole.VIEWER))) -> dict[str, Any]:
        try:
            return {"available": True, "portfolios": risk_items(await store.risk_rows())}
        except Unavailable:
            return {"available": False, "portfolios": []}

    @app.get("/api/v1/futures/desk")
    async def futures_desk_view(account: str | None = None, p: Principal = Depends(require(OperatorRole.VIEWER))) -> dict[str, Any]:
        """Futures Demo account: positions, stops, liquidation, loss caps and per-trade P&L (futures-desk-plan.md §6)."""
        return await futures_desk(futures_store, account, can_edit=p.at_least(OperatorRole.OPERATOR))

    @app.exception_handler(Refused)
    async def futures_refused(request: Request, exc: Refused) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content={
            "code": exc.code, "request_id": getattr(request.state, "request_id", None), "retryable": False,
            "message": exc.reasons[0] if exc.reasons else "ไม่ผ่านการตรวจ", "reasons": exc.reasons, "plan": exc.plan})

    @app.post("/api/v1/futures/orders/preview")
    async def futures_preview(body: OpenTicket, p: Principal = Depends(require(OperatorRole.VIEWER))) -> dict[str, Any]:
        """Size and checks for a ticket, without queuing anything."""
        return await preview(futures_store, body)

    @app.post("/api/v1/futures/orders")
    async def futures_open(body: OpenTicket, p: Principal = Depends(require(OperatorRole.OPERATOR))) -> dict[str, Any]:
        """Queue an OPEN ticket. The worker checks it again on fresh data and sends entry + exchange stop (§7)."""
        return await submit_open(futures_store, body, f"web:{p.user_id}")

    @app.post("/api/v1/futures/positions/{symbol}/close")
    async def futures_close(symbol: str, body: CloseTicket, p: Principal = Depends(require(OperatorRole.OPERATOR))) -> dict[str, Any]:
        return await submit_close(futures_store, symbol, body, f"web:{p.user_id}")

    @app.post("/api/v1/futures/positions/{symbol}/stop")
    async def futures_stop(symbol: str, body: StopTicket, p: Principal = Depends(require(OperatorRole.OPERATOR))) -> dict[str, Any]:
        return await submit_stop(futures_store, symbol, body, f"web:{p.user_id}")

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

    @app.exception_handler(BadRequest)
    async def bad_request(request: Request, exc: BadRequest) -> JSONResponse:
        return JSONResponse(status_code=422, content={
            "code": "bad_request", "request_id": getattr(request.state, "request_id", None),
            "retryable": False, "message": str(exc)})

    @app.exception_handler(Loading)
    async def chart_loading(request: Request, exc: Loading) -> JSONResponse:
        return JSONResponse(status_code=503, headers={"Retry-After": "3"}, content={
            "code": "chart_loading", "request_id": getattr(request.state, "request_id", None),
            "retryable": True, "message": "กำลังดาวน์โหลดแท่งย้อนหลังจาก archive ลองใหม่ในไม่กี่วินาที"})

    @app.get("/api/v1/charts/{symbol}/bars")
    async def chart_bars(symbol: str, venue: str = "spot", timeframe: str = "1d", start: datetime | None = Query(None, alias="from"),
                         end: datetime | None = Query(None, alias="to"),
                         p: Principal = Depends(require(OperatorRole.VIEWER))) -> dict[str, Any]:
        return await chart.bars(symbol, venue, timeframe, start, end)

    @app.get("/api/v1/charts/{symbol}/indicators")
    async def chart_indicators(symbol: str, venue: str = "spot", timeframe: str = "1d", set: str = "basic",
                               start: datetime | None = Query(None, alias="from"), end: datetime | None = Query(None, alias="to"),
                               p: Principal = Depends(require(OperatorRole.VIEWER))) -> dict[str, Any]:
        return await chart.indicators(symbol, venue, timeframe, start, end, set)

    @app.get("/api/v1/bots/{account}/{book}/overlays")
    async def bot_overlays(account: str, book: str, start: datetime | None = Query(None, alias="from"),
                           end: datetime | None = Query(None, alias="to"),
                           p: Principal = Depends(require(OperatorRole.VIEWER))) -> dict[str, Any]:
        now = chart.now()
        a = utc(start or datetime(2026, 1, 1)).to_pydatetime()
        b = utc(end or now).to_pydatetime()
        if a >= b:
            raise BadRequest("from must be before to")
        try:
            fills, decisions, events = await overlay_store.rows(account, book, a, b)
        except OverlaysUnavailable:
            return {"available": False, "mode": "TESTNET", "fills": [], "decisions": [], "halts": [], "alerts": []}
        return {"available": True, "account": account, "book": book, **overlay_items(fills, decisions, events, a)}

    @app.get("/api/v1/charts/runs")
    async def chart_runs(p: Principal = Depends(require(OperatorRole.VIEWER))) -> dict[str, Any]:
        return {"items": run_list(load_backtests(), overview_items(await store.overview_rows()))}

    @app.get("/api/v1/charts/runs/{kind}/{run_id:path}")
    async def chart_run(kind: str, run_id: str, p: Principal = Depends(require(OperatorRole.VIEWER))) -> dict[str, Any]:
        if kind == "backtest":
            found = [b for b in load_backtests() if b["id"] == run_id]
            if not found:
                raise AuthError("not_found", "ไม่พบผล backtest นี้", status=404)
            return backtest_run(found[0])
        if kind == "shadow":
            row = await store.portfolio(run_id)
            if row is None or row[1] != "SHADOW":
                raise AuthError("not_found", "ไม่พบพอร์ต Shadow นี้", status=404)
            return shadow_run(row, await store.equity_rows(run_id))
        raise BadRequest(f"unknown run kind {kind!r}; allowed: backtest, shadow")

    @app.exception_handler(BotsUnavailable)
    async def bots_unavailable(request: Request, exc: BotsUnavailable) -> JSONResponse:
        return JSONResponse(status_code=503, content={
            "code": "bots_unavailable", "request_id": getattr(request.state, "request_id", None), "retryable": True,
            "message": "ยังไม่มีตารางบอทในฐานข้อมูล หรือยังไม่ได้ตั้ง DATABASE_URL ของ Backend"})

    @app.exception_handler(BotRefused)
    async def bot_refused(request: Request, exc: BotRefused) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content={
            "code": exc.errors[0].code if exc.errors else "refused", "request_id": getattr(request.state, "request_id", None),
            "retryable": False, "message": exc.errors[0].message_th if exc.errors else "ไม่ผ่านการตรวจ",
            "errors": [e.as_dict() for e in exc.errors]})

    @app.exception_handler(BotNotFound)
    async def bot_not_found(request: Request, exc: BotNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={
            "code": "not_found", "request_id": getattr(request.state, "request_id", None), "retryable": False,
            "message": "ไม่พบบอทหรือ revision นี้"})

    @app.get("/api/v1/bot-templates")
    async def bot_templates(p: Principal = Depends(require(OperatorRole.VIEWER))) -> dict[str, Any]:
        """Strategy templates a bot can run (trading/bots/templates.py), with param and allocation bounds."""
        return {"templates": template_list()}

    @app.get("/api/v1/bot-accounts")
    async def bot_accounts(p: Principal = Depends(require(OperatorRole.VIEWER))) -> dict[str, Any]:
        """Testnet accounts a worker has reported, their last USDT free, and whether a new bot may use them."""
        return {"accounts": await bot_store.accounts()}

    @app.get("/api/v1/bots")
    async def list_bots(p: Principal = Depends(require(OperatorRole.VIEWER))) -> dict[str, Any]:
        return {"bots": await bot_store.list_bots(), "can_edit": p.at_least(OperatorRole.OPERATOR)}

    @app.get("/api/v1/bots/{bot_id}")
    async def get_bot(bot_id: uuid.UUID, p: Principal = Depends(require(OperatorRole.VIEWER))) -> dict[str, Any]:
        return {**await bot_store.get_bot(bot_id), "can_edit": p.at_least(OperatorRole.OPERATOR)}

    @app.post("/api/v1/bots")
    async def save_bot(body: SaveBot, p: Principal = Depends(require(OperatorRole.OPERATOR))) -> dict[str, Any]:
        """Append a revision (never runs it: a revision needs submit and approve, and a worker with WORKER_BOT=on)."""
        return await bot_store.save(body, p.user_id)

    @app.post("/api/v1/bots/{bot_id}/revisions/{revision}/{action}")
    async def bot_action(bot_id: uuid.UUID, revision: int, action: Literal["submit", "approve", "reject"], body: BotEvent,
                         p: Principal = Depends(require(OperatorRole.OPERATOR))) -> dict[str, Any]:
        return await bot_store.act(bot_id, revision, action, p.user_id, body.note)

    @app.post("/api/v1/bots/{bot_id}/retire")
    async def retire_bot(bot_id: uuid.UUID, body: BotEvent, p: Principal = Depends(require(OperatorRole.OPERATOR))) -> dict[str, Any]:
        return await bot_store.act(bot_id, None, "retire", p.user_id, body.note)

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
        """Bot decisions (R10a or AI r2), Guardian events, HALT/RESUME requests and AI spend (P5, P6)."""
        try:
            rows = await worker_store.control_rows(account, CONTROL_LIMIT)
        except WorkersUnavailable:
            return {"available": False, "decisions": [], "risk_events": [], "requests": [], "ai_month": []}
        return {"available": True, **control_items(*rows)}

    @app.get("/api/v1/stream")
    async def live_stream(request: Request, p: Principal = Depends(require(OperatorRole.VIEWER))) -> StreamingResponse:
        """Change notices (SSE) for the web: hello, heartbeat, changed, ping, bye (m1-realtime-plan.md §3.1)."""
        return StreamingResponse(event_stream(watcher, request.is_disconnected, stream_max_age, stream_ping_every),
                                 media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    app.state.watcher = watcher

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
                     workers=PgWorkerStore(pool), drafts=PgDraftStore(pool), charts=ChartService(PgBarStore(pool)),
                     overlays=PgOverlayStore(pool), bots=PgBotStore(pool), cursors=PgCursorSource(pool),
                     compare=PgCompareStore(pool), futures=PgFuturesStore(pool))
    app.router.on_startup.append(pool.open)
    app.router.on_shutdown.append(pool.close)
    return app
