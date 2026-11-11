# AI agent book (agent-v1): plan

Written 2026-10-18 after Note chose "ร่างเลย" and then "เริ่มได้เลย". The full design, with the flow
diagram, is the Claude Doc "แผนออกแบบ AI Agent Book"
(https://claude.ai/code/artifact/09299346-5d66-43bd-91d7-d3d5f28fbd6d). This file records what the
code must follow. Simulated money only; nothing here sends orders.

## 1. Hypothesis and scope

Five model calls per trigger (three analysts with read-only MCP tools, one Decider, one Veto),
followed by the code Guardian, trade BTC better than the same triggers taken every time or at
random, after fees and API cost. It is a separate paper book, `agent-v1`, beside R10a and every
other book; it changes none of them. No backtest is possible: the models know past prices.

## 2. Decisions Note made (2026-10-18)

| Item | Choice |
| --- | --- |
| Market | BTCUSDT spot, long or cash, 1x |
| Paper money | 10,000 USDT per book |
| API budget | at most $25 a month and $1 a day; over budget = no call, never a silent fallback |
| Pass rule | after ≥ 60 closed trades and ≥ 8 weeks (2-week pilot not counted): net > 0 after fees + API; beats `always`; above the 80th percentile of 1,000 `random` seeds; Sharpe > B&H or max DD ≤ half of B&H |
| News source | Claude API web search (basic, at most 3 searches per analyst run, $10 per 1,000); chosen 2026-10-19 as the default, no extra key |

## 3. Signals (step 1)

`backend/src/trading/agent/signals.py`. The Decider picks a condition from a fixed allowlist
(price close above/below, price touch, RSI 14 below/above, signed % move over N hours, volume
spike vs the 20 bars before, funding above/below, time check); code evaluates it on each closed 1h
bar built from 1m bars (`trading.ingest.bars.aggregate`). A signal lives ≤ 72 h, ≤ 5 are active
per book, it never fires on a bar that closed before it was created, and missing data never
fires. With nothing active, a `time_check` 24 h out is added.

Tables: `supabase/migrations/20261019020014_agent_book.sql` (`trading.agent_signals`,
`agent_runs`, `agent_tool_calls`), applied to Supabase on 2026-10-19 after Note's "apply".

## 4. Decision runner (step 2)

`backend/src/trading/agent/roles.py` fixes the five roles (`PROMPT_VERSION` agent-v1-p1):

| Role | Model, effort | Tools |
| --- | --- | --- |
| analyst_news | Sonnet 5.5, low | web search, ≤ 3 |
| analyst_price | Sonnet 5.5, low | 1h/4h/1d candles with RSI and SMAs, 24 h ticker |
| analyst_market | Sonnet 5.5, low | order book summary; funding, mark/index, open interest |
| decider | Opus 5.5, medium | none: reads the three reports, answers BUY/SELL/HOLD and 1–3 new signals |
| veto | Sonnet 5.5, medium | none: PASS or VETO on a BUY or SELL only |

`backend/src/trading/agent/runner.py` runs them: analysts in parallel, then the Decider, then the
Veto. Each call reserves its role's maximum cost first and is not made when the $1/day or
$25/month cap cannot cover it (BUDGET_BLOCKED). One attempt per call; a failed analyst is passed
to the Decider as missing; a failed or vetoing Veto means HOLD; a BUY while long or a SELL while
flat is HOLD. Signals the Decider asks for go through `signals.parse_params`/`check_new`, and
invalid ones are kept with the reason. Every call and tool call (full response, sha256) is
recorded in the shape of `agent_runs` / `agent_tool_calls`. Market data is Binance mainnet
public REST (`market.py`, no key), so it runs where the worker runs; the cloud gets 451.

Dry run: `uv run python scripts/agent_dry_run.py --decisions 3` (or double-click
`scripts/windows/agent-dry-run.cmd`) with `AGENT_ANTHROPIC_KEY` set to a key nothing else uses.
It writes every decision to `var/agent_dry_run/` and keeps the caps across runs in its ledger.

## 5. Order of work

1. Signal rules + tables (this PR). Wiring into the worker belongs to the thread that owns the
   worker, after the tables are live.
2. Analysts, Decider and Veto with MCP tools and full tool-call logging, run dry (no book), to
   measure real cost per decision against the $25 budget.
3. Hook to the Guardian (Testnet plan P5) and open the books `agent-v1/ai`, `/always`,
   `/random`, `/buy_and_hold`.
4. Web tab: active signals, each decision with its reasons, API spend, books compared.
5. 2-week pilot, then lock prompts and limits; changing either afterwards is `agent-v2`.
