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
| News source | not chosen; proposed in step 2 |

## 3. Signals (step 1, this PR)

`backend/src/trading/agent/signals.py`. The Decider picks a condition from a fixed allowlist
(price close above/below, price touch, RSI 14 below/above, signed % move over N hours, volume
spike vs the 20 bars before, funding above/below, time check); code evaluates it on each closed 1h
bar built from 1m bars (`trading.ingest.bars.aggregate`). A signal lives ≤ 72 h, ≤ 5 are active
per book, it never fires on a bar that closed before it was created, and missing data never
fires. With nothing active, a `time_check` 24 h out is added.

Tables: `supabase/pending/agent_book.sql` (`trading.agent_signals`, `agent_runs`,
`agent_tool_calls`). Not applied: it is applied once Note says "Apply", then moved to
`supabase/migrations/` with history and fingerprint as `supabase/README.md` says.

## 4. Order of work

1. Signal rules + tables (this PR). Wiring into the worker belongs to the thread that owns the
   worker, after the tables are live.
2. Analysts, Decider and Veto with MCP tools and full tool-call logging, run dry (no book), to
   measure real cost per decision against the $25 budget.
3. Hook to the Guardian (Testnet plan P5) and open the books `agent-v1/ai`, `/always`,
   `/random`, `/buy_and_hold`.
4. Web tab: active signals, each decision with its reasons, API spend, books compared.
5. 2-week pilot, then lock prompts and limits; changing either afterwards is `agent-v2`.
