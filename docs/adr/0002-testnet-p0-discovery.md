# ADR 0002: Testnet P0 discovery (host, environment, accounts, sources)

Status: **proposed 2026-10-17**. The host decision is accepted (Note, 2026-10-17).
Public checks were run on Note's PC on 2026-10-17 at 02:44 UTC; the report is in
`0002-evidence/p0-pc-2026-10-17-nokey.json`. The rows still marked *pending, needs keys*
are filled from a second run once the Testnet key is set, and the ADR is accepted then.

Plan: docs/testnet-development-plan.md (v1.2, PR #42) §10 P0: "active books/jobs
audit, Host ADR, Spot Testnet auth/metadata/stream access, source/account/rights
ADR, จำนวน Testnet accounts". Gate: "ยืนยัน Testnet portal/account/endpoint จาก
Host จริง; Mainnet trading ปิด".

## 1. Active books and jobs (audit, nothing changed)

| What | Where it runs | P0–P5 effect |
| --- | --- | --- |
| Paper books pathA-v1.1, pathA-v1.2, pathA-p5-r1, pathA-p5-r2, futures-r7, pathA-r10a, futures-r11 (`scripts/paper_run.py --book …`) | Claude scheduled task, daily 08:20 UTC, in the cloud | Untouched. They stay the SHADOW reference (§8.7). |
| `trading_sync_sql.py`, `supabase_sync_sql.py`, `telegram_notify.py` steps of that task | same task | Untouched; owned by the "Backtest Phase 0.5 v1" thread. |
| `trading.exec_*` tables (migration 20261016144036) | Supabase asakantrading | Empty. They become the P3 store for TESTNET orders. |
| Order core and store code (PRs #31, #32) | not running anywhere | Reused in P3/P4 (§10.2). |

No process sends orders today. `LIVE_SUPPORTED_IN_BUILD = False`, and nothing in P0 changes it.

## 2. Host

**Decision (Note, 2026-10-17): PC first, then a VPS.** P1–P4 run on Note's
Windows PC, and a VPS is set up before the P5 soak (§2.2).

Evidence that the cloud cannot host it (`scripts/testnet_discovery.py` from the
Claude cloud container, 2026-10-17): Testnet REST, the Testnet market stream and
the Testnet WebSocket API were refused at the egress proxy (HTTP 403). Mainnet
`fapi.binance.com` answered HTTP 451, and only `data.binance.vision` (the
archive) was reachable.

| Host fact | PC (P1–P4) | VPS (before P5) |
| --- | --- | --- |
| Testnet REST / market stream / WS API reachable | yes / yes / yes (REST RTT 251 ms, stream first message 1.0 s, WS API ping 0.56 s) | to check on the VPS with the same script |
| Clock offset to the Testnet server (must be < 1 s) | **+751 ms** (PC clock behind the server): inside 1 s but too close. Resync Windows time (`w32tm /resync` as admin) and recheck; the gateway also signs with a measured server-time offset, and recvWindow is 5 s | to check, with NTP on |
| Country / provider / monthly cost | Note's home connection | to choose before P5: a country where Binance allows access, plus the provider and budget |
| Always on | no; a PC that is off pauses entries and starts in RECOVERING next time | yes, with a restart policy |

## 3. Environment

**Binance Spot Test Network** (`testnet.binance.vision`), not Binance Demo
Trading. Switching to Demo would need a new ADR (§3.1). The hosts are constants
in code. The discovery script calls no order, cancel or wallet route, and its
tests check that.

| Testnet fact | Value |
| --- | --- |
| BTCUSDT status, order types, filters | TRADING; LIMIT, LIMIT_MAKER, MARKET, STOP_LOSS(_LIMIT), TAKE_PROFIT(_LIMIT); tick 0.01, step 0.00001, minQty 0.00001, maxQty 9000, min notional 5 USDT (also for MARKET); MARKET_LOT_SIZE maxQty 133.97; PERCENT_PRICE_BY_SIDE bid 0.5–1.2×, ask 0.8–2× of the 5-min average; MAX_NUM_ORDERS 200, algo orders 5. These match the fixture rules used in the PR #31 tests |
| Published rate limits | REQUEST_WEIGHT 6,000/min; ORDERS 50 per 10 s and 160,000/day; RAW_REQUESTS 300,000 per 5 min. Our own limits (1 order/s, 20/day) sit far below them |
| OCO / OTO / cancel-replace allowed | yes / yes / yes per exchangeInfo. Whether a stop actually rests and triggers is still tested with orders in P4 before any protection policy (§8.5) |
| Commission rates on the account | *pending, needs keys* |

## 4. Accounts and keys

- **One Spot Testnet account (DEFAULT).** tn-conformance-v1 runs on it in P4 and
  stops before the P5 soak, and tn-r10a-code-v1 then has the account alone
  (§5.4, §12.2). A second account would only be needed to run both at once.
- **Key type: Ed25519 (recommended).** The WebSocket API's `session.logon`
  (used for the user-data stream) needs an Ed25519 key, and HMAC is still
  accepted for REST. The discovery script supports both.
- Keys are created by Note in the Testnet portal and live only in environment
  variables on the Host (`BINANCE_TESTNET_API_KEY`, plus
  `BINANCE_TESTNET_PRIVATE_KEY_PATH` for Ed25519 or `BINANCE_TESTNET_API_SECRET`
  for HMAC). They are never put in the repo, Supabase, logs, chat or `VITE_*`.
- Opening balances are read from the account, never assumed (§3.5). *Pending, needs keys.*

## 5. Sources and rights (signal plane, §3.3)

| Plane | Source | Status |
| --- | --- | --- |
| Signal (R10a: daily close, SMA100, 7-day funding) | Mainnet public data, read-only and unauthenticated. | From the PC, `api.binance.com`, `data-api.binance.vision`, `fapi.binance.com` and the `data.binance.vision` archive all answered 200. So P2 can take daily closes and funding from Mainnet public REST/WS on the PC, with the archive as the cross-check and fallback. The VPS must pass the same check. |
| Execution (quotes, filters, account) | Testnet only | §3 above |
| Result (fills, fees, balances) | Testnet only | from P4 |

All signal sources are public market data, with no paid licence and no account.
News and calendar providers are out of P0–P5 scope (§10.3).

## 6. How to finish P0

On the PC, in the repo folder:

```
uv run python scripts/testnet_discovery.py --out p0-pc.json
```

Run it once without keys (public checks) and once after the Testnet key is set.
Paste the summary lines, or send `p0-pc.json`, which contains no secrets. The
pending rows above are filled from it, and this ADR moves to accepted.
