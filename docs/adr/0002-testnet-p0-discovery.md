# ADR 0002: Testnet P0 discovery (host, environment, accounts, sources)

Status: **proposed 2026-10-17**. The host decision is accepted (Note, 2026-10-17).
The rows marked *pending P0 run* are filled from `scripts/testnet_discovery.py`
run on Note's PC, and the ADR is accepted once they are.

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
| Testnet REST / market stream / WS API reachable | *pending P0 run* | to check on the VPS with the same script |
| Clock offset to the Testnet server (must be < 1 s) | *pending P0 run* | to check, with NTP on |
| Country / provider / monthly cost | Note's home connection | to choose before P5: a country where Binance allows access, plus the provider and budget |
| Always on | no; a PC that is off pauses entries and starts in RECOVERING next time | yes, with a restart policy |

## 3. Environment

**Binance Spot Test Network** (`testnet.binance.vision`), not Binance Demo
Trading. Switching to Demo would need a new ADR (§3.1). The hosts are constants
in code. The discovery script calls no order, cancel or wallet route, and its
tests check that.

| Testnet fact | Value |
| --- | --- |
| BTCUSDT status, order types, filters (tick, step, min notional) | *pending P0 run* |
| Published rate limits | *pending P0 run* |
| OCO / OTO / cancel-replace allowed | *pending P0 run* (needed before any protection policy, §8.5) |
| Commission rates on the account | *pending P0 run, needs keys* |

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
- Opening balances are read from the account, never assumed (§3.5). *Pending P0 run.*

## 5. Sources and rights (signal plane, §3.3)

| Plane | Source | Status |
| --- | --- | --- |
| Signal (R10a: daily close, SMA100, 7-day funding) | Mainnet public data. The daily books already use the `data.binance.vision` archive, which is free and public. | Live reachability of `api.binance.com` / `fapi.binance.com` from the PC is *pending P0 run*. If it is reachable, P2 can use public REST/WS for fresher closes; if not, signals stay on the archive and the closed daily bar is ready later. |
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
