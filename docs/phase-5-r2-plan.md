# Phase 5 r2 on path A: the AI proposes the weight (plan, declared before the code)

Written 2026-10-16, after Note chose "เขียนแผนเลย" for an AI that trades by itself. No r2
code exists yet. BUILDPLAN v4.2 stages the AI as FILTER_ONLY → MULTI_ANALYST_FILTER →
BOUNDED_PROPOSAL (§7.1, §7.6); r1 (`docs/phase-5-paired-plan.md`) is FILTER_ONLY. r2 is a
**BOUNDED_PROPOSAL** run: the AI proposes the daily BTC weight, and code checks, sizes and fills
it under the same loss limits as every book. The AI never sends orders and never sees an
exchange. Simulated money only.

r2 runs **beside** r1 and changes nothing in it: r1's design is locked, so r2 is a separate
experiment with its own portfolios, budget and decisions.

## What this run can and cannot tell us

- One proposal a day gives about 28 AI decisions in 28 days, much more than r1's ~0.6 filter
  questions, but still far too few to show that the AI beats Code or buy & hold. The day-28
  outcome on any return effect is expected to be `INSUFFICIENT_EVIDENCE`.
- What it can show in 28 days: whether the proposer pipeline works end to end, how often the
  AI's weight differs from Code's, turnover and fees, reliability, latency and billed cost.
- No historical backtest of the AI arm: the model's training data covers past prices, so
  replaying them would let it remember outcomes. Forward only.
- Exploratory (§17.2). Confirmation would need a separate locked design.

## Portfolios (all open the same day from 10,000 USDT, `spot.v1`, same fill rule)

| Portfolio | Policy |
| --- | --- |
| `pathA-p5-r2/ai` | the AI's proposed weight each day, after the code checks below |
| `pathA-p5-r2/code` | pathA-v1.2 rules (the same Code as r1 and the v1.2 book) |
| `pathA-p5-r2/buy_and_hold` | 100% BTC from the first fill |
| `pathA-p5-r2/cash` | stays in USDT |
| Random proposer | 200 seeds: each day a random weight from {0, 0.5, 1}, changing on the same number of days as the AI did; replayed as full portfolio paths |

The random proposer is the control for "any active weight changes at this frequency": the AI is
only interesting if it does better than random proposals with the same turnover.

## The proposal (BOUNDED_PROPOSAL, §7.6)

- **When:** once a day after the daily close, at the same decision time as the Code books.
- **Template:** `pathA_spot_weight_v1`, the only allowed template. The AI fills in
  `target_weight` ∈ {0, 0.5, 1}, a `reason_code` from an allowlist, a `reason` (≤ 500 chars) and
  ≤ 8 `evidence_refs` that must name snapshot fields. No side other than long or flat, no
  leverage, no other symbol, no limit prices, no timing choices.
- **Input (`quant_only`):** the same frozen snapshot as r1 (perp close, SMA100, trend flag,
  7-day funding with each day's source, the last 30 daily closes, the arm's own weight and
  drawdown), **without** Code's suggested weight. Showing it would test whether the AI copies
  Code, not whether it decides. Code's weight is recorded beside each proposal for the report.
- **Compiled by code, never by the model:** the template compiler checks the schema, the weight
  set and the evidence refs, then turns the weight into a target exactly like a Code decision.
  Fills, fees, slippage and timing come from the shared `PathABook` code.
- **Failure means no change:** invalid JSON, a weight outside {0, 0.5, 1}, unknown evidence, a
  late answer, a refusal or a provider error all keep the current weight and are logged in their
  own category. A failure never moves the book to cash or to BTC.
- **Never overridden by the AI:** `spot.v1` alerts and the 25% drawdown HALT. A HALT sells to
  cash and is absorbing until the endpoint (as in r1); proposals after it are recorded and
  ignored.
- **Model, locked for the run:** `claude-opus-5-5`, effort `medium`, one attempt, 120 s
  deadline, SDK retries off, no fallback. Changing the model, prompt or effort starts r3.

## Costs

- One call a day. r1's real test call cost $0.013 (1,770 input / 300 output tokens); r2's
  prompt is similar, so about **$0.40 a month**. Booked as an operating cost in the AI
  portfolio, so it lowers economic equity only.
- **Budget cap: $5 a month, separate from r1's.** At the cap the AI fails closed (no change)
  and the run reports a reliability incident.

## Timebox and reviews

28 days from the opening day, with reviews on days 7, 14 and 28, the same rules as r1. Day-28
decision with reasons: `CONTINUE`, `REVISE`, `PAUSE` or `INSUFFICIENT_EVIDENCE`. Reported:
economic and trading return of every portfolio, drawdown, exposure, number of weight changes and
fees, agreement with Code's weight per day, failure counts by category, latency p50/p95, billed
cost, and the AI's result against the random-proposer distribution. Nothing is annualized.

## Acceptance tests before the first forward day

- An AI mock that always proposes Code's weight reproduces the Code portfolio exactly.
- Every invalid answer (bad JSON, weight 0.7, unknown template, evidence not in the snapshot,
  late, refused) keeps the current weight and is never counted as a decision.
- The prompt and snapshot never contain Code's suggested weight (checked on the request bytes).
- HALT sells and is absorbing; proposals after it change nothing.
- The random proposer is deterministic from seed and day, matches the AI's change count, and
  each seed is a full path replay.
- Cost entries balance; economic equity = trading equity − booked AI cost; the cap fails closed.
- The contract change that enables `BOUNDED_PROPOSAL` accepts only `pathA_spot_weight_v1`.

## What it needs

1. Note approves this plan (merge the PR).
2. Code: the template compiler and contract change, the r2 book and runner, the tests above,
   a frozen branch `frozen/pathA-p5-r2`, and a step in the daily task. It reuses
   `P5_ANTHROPIC_KEY`; no new secret.
3. Defaults Note can change: no Code weight in the AI's input, $5/month cap, 28 days.

Simulated money only. No exchange orders, no Live, no order adapter.
