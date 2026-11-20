# Futures desk plan

A separate web page for trading USDⓈ-M futures by hand, for day trading later. Note approved
both stages on 2026-11-18 (project thread "สร้างหน้า Futures สําหรับเทรดและดูความเสี่ยง").

| Stage | What | Touches money? |
| --- | --- | --- |
| 1 (PR #104) | Read-only page: per-trade P&L, open positions, liquidation price, risk against the rules | No. The worker has no order method. |
| 2 (PR after #104) | Manual order ticket: Demo only, exchange-side stop on every order, size from the 1% rule, blocked past the caps | Demo money only |

## 1. Venue

Binance **USDⓈ-M Futures Demo Trading**, REST `https://demo-fapi.binance.com` (the host Binance's
USDⓈ-M docs list for testing; `testnet.binancefuture.com` is the older host and now forwards to
Demo Trading). Keys are made on `demo.binance.com`, not on `testnet.binance.vision`. The Oracle VM
reaches the Demo hosts (checked 2026-11-17); the Claude cloud gets 451 from Binance, so all tests
use fakes.

## 2. Rules (`backend/src/trading/futures/rules.py`)

Fixed in code, never from the page or the environment:

* A stop-out loses about **1%** of equity (`RISK_PER_TRADE`).
* **Day cap 3%**: equity 3% below the first mark of the UTC day (07:00 Bangkok) blocks new trades.
* **Total cap 10%**: equity 10% below its high-water mark blocks new trades.
* **Every position has a stop-loss on the exchange.** Stage 1 shows a red card and sends a
  Telegram alert when one has none; stage 2 refuses to send an order without one.
* "Near" warning at 70% of either cap.

Equity is Binance's `totalMarginBalance` (wallet + unrealized P&L). Deposits or Demo top-ups move
it too; reset the high-water mark by deleting the `fut_risk_state` row if that happens.

## 3. Endpoint guards

`trading/runtime/endpoints.py` gains `Venue.FUTURES_DEMO`. Signed calls may now go to
`testnet.binance.vision` (spot) or `demo-fapi.binance.com`; the Mainnet `fapi.binance.com` stays
public-read-only. Each venue only accepts its own path prefix (`/api/` spot, `/fapi/` futures).
The worker refuses to start when a Mainnet key variable is set (same list as the spot workers).

## 4. Data (`supabase/migrations/20261118143243_futures_desk.sql`)

* `trading.fut_state` – latest account view per account (balances, positions, open regular and
  algo orders, mark price and filters), replaced each sync.
* `trading.fut_risk_state` – day open and high-water mark, kept across restarts.
* `trading.fut_fills` – every fill (`/fapi/v1/userTrades`), append-only.
* `trading.fut_income` – funding, commission, realized P&L, transfers (`/fapi/v1/income`), append-only.

Private like the rest of `trading`: RLS on, service_role only.

## 5. Worker (`scripts/futures_worker.py`, systemd `tradingasakan-futures`)

Its own process and unit, so it never touches `tradingasakan-worker`, `@AI` or `@BOT1`. Every 10 s
it reads account, positions, open orders, open algo orders (STOP_MARKET and friends moved to the
Algo service in December 2025), new fills and new income, then writes it all in one transaction.
It holds the writer lease (`trading.exec_writer_lease`) under its own account name (default
`FUT1`). The first run reads the last 7 days of fills and income.

Install on the VM (after this PR merges and the VM pulls):

```bash
sudo install -m 600 -o trading /dev/null /etc/tradingasakan/futures.env
# put DATABASE_URL, FUTURES_DEMO_API_KEY, FUTURES_DEMO_API_SECRET, FUTURES_ACCOUNT=FUT1,
# TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID into that file
sudo -u trading bash -c 'set -a; . /etc/tradingasakan/futures.env; cd /opt/tradingasakan && uv run python scripts/futures_worker.py --once'
sudo cp deploy/systemd/tradingasakan-futures.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now tradingasakan-futures
```

## 6. Page (`apps/web/src/futures/FuturesDesk.tsx`, main menu "Futures")

`GET /api/v1/futures/desk[?account=]` returns the view; the page polls every 10 s.

* Account: equity, unrealized P&L, available balance, the 1% risk amount in USDT.
* Caps: day loss and total drawdown as bars with OK / near / blocked and the room left.
* Positions: side, size, leverage, entry, mark, stop, take-profit, liquidation price and its
  distance, risk at entry (entry to stop, as % of equity), what the stop would cost now, and
  warnings for no stop, risk over 1%, or liquidation before the stop.
* Per-trade log ("ไม้"): fills grouped flat to flat, with entry and exit averages, holding time,
  fees, funding while held and net P&L. Win rate, averages and today's total on top.

## 7. Stage 2: manual order ticket

Page: "เปิดไม้ใหม่" (symbol, Long/Short, stop-loss required, take-profit optional, leverage 1–10),
"ตรวจขนาดไม้" (preview), "ส่งคำสั่ง" with a confirm dialog. Under each position: close at market and
move stop. A list of the last 20 requests shows what the worker did with each.

* **Size**: floor(equity × 1% / |mark − stop|) to the step size. When less room is left under the day
  or total cap than 1%, the size shrinks so a stop-out still stays inside the cap.
* **Refused** when: no stop, stop on the wrong side, closer than 0.1% or further than 15%, TP on the
  wrong side, a cap reached, a position already open on that symbol, any position without a stop,
  leftover orders on that symbol, below min qty / min notional, margin above 90% of available, or the
  estimated isolated liquidation (0.5% maintenance) within 0.2% of the stop.
* **Queue**: the API checks (`POST /api/v1/futures/orders/preview` for anyone signed in,
  `POST /api/v1/futures/orders`, `/positions/{symbol}/close`, `/positions/{symbol}/stop` for operators)
  and inserts a row in `trading.fut_order_requests` (one per `request_id`). It refuses when the worker
  does not take orders or its data is older than 60 s.
* **Worker** (with `FUTURES_ORDERS_ENABLED=true`): sees a queued row within a tick, syncs, checks the
  ticket again on that data and never sends more than the previewed size. Tickets older than 60 s
  expire. The row goes SENT before the first exchange call, so it is sent at most once; a SENT row
  found after a restart is marked FAILED with an alert.
  * OPEN: isolated margin, leverage, MARKET entry, then `POST /fapi/v1/algoOrder` STOP_MARKET
    `closePosition=true` on the mark price (+ TAKE_PROFIT_MARKET if given). If the stop is refused
    the position is closed at market at once and a CRITICAL alert goes out.
  * CLOSE: reduce-only MARKET for the whole position, then cancel the symbol's regular and algo orders.
    Allowed even when a cap is reached.
  * MOVE_STOP: new stop first, then cancel the old ones. Only while risk at entry stays within 1.1%.
    Binance allows one `closePosition` stop per direction (-4130), so the new stop is a reduce-only
    STOP_MARKET for the position's quantity. If that is refused: cancel the old stop and place the new
    one; if that fails put the old stop back; if even that fails close the position at market.
  * A TP that hits -4130 next to the stop is sent the same reduce-only way.
* The REST client refuses every order call unless built with `orders_enabled=True`.

Turn on (after stage 1 runs read-only for a while): give the Demo key trade permission, add
`FUTURES_ORDERS_ENABLED=true` to `/etc/tradingasakan/futures.env`, `sudo systemctl restart tradingasakan-futures`.
The page header then says "worker รับคำสั่งจากหน้านี้".
