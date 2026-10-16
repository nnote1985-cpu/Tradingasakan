# Phase 5 on path A: Code vs AI paired exploratory run (plan, declared before the code)

Written 2026-10-16, after Note chose "Phase 5 paired". No Phase 5 code exists yet and no
forward data has been collected for it. BUILDPLAN v4.2 §24 Phase 5: "v1 exploratory paired:
Code/AI, Random Veto pilot, error/cost/effect reports"; gate "next hypothesis /
working-baseline decision with uncertainty". Rules come from §7.1–7.4 (FILTER_ONLY), §9.6
(HALT in a paired run), §16.1–16.4 (paired design, controls, Random Veto, counterfactual),
§17.1–17.2 (metrics, exploratory) and §18.2 (exploratory selection).

The strategy is path A (ADR 0001), not the v1 breakout. The Code rules are exactly those of
the frozen pathA-v1.2 book (daily BTC weight from SMA100 trend and 7-day funding, spot,
`spot.v1` limits, fills at the next 1h open after the decision).

## What this run can and cannot tell us

- **Few candidates.** On development + validation data (2022-04 → 2025-01, 1,019 days) path A
  changed its weight 15.4 times a year (`/mnt/project-files/pathA-power/report.json`). About half
  of those are increases, so the AI filter below sees roughly **0.6 candidates per 28 days**.
  A 28-day exploratory window will almost certainly end `INSUFFICIENT_EVIDENCE` on any effect.
  This is stated now so it is not read as a failure later.
- What it can measure in 28 days: whether the pipeline works end to end, AI reliability
  (invalid JSON, timeouts, bad evidence refs), latency, real billed cost, and the AI's daily
  diagnostic verdicts (below), which accumulate about 28 per window.
- **No historical backtest of the AI arm.** The model's training data covers 2022–2025 prices,
  so replaying the past would let it "remember" outcomes. The AI arm is evaluated forward only.
- Nothing here is confirmatory (§17.2). Confirmatory needs a separate locked design (Phase 6).

## Arms (shared candidate stream, independent portfolios, §16.1)

All arms open on the same day from 10,000 USDT on the same `SpotLedger` and `spot.v1` profile,
read the same daily snapshot (the `decision_inputs` already recorded by pathA-v1.1/v1.2), and
fill at the open of the first 1h bar after their own decision time.

| Portfolio | Policy |
| --- | --- |
| `pathA-p5/code` | Code target weight every day (same rules as pathA-v1.2) |
| `pathA-p5/ai` | Code target, filtered by the AI (below) |
| `pathA-p5/buy_and_hold` | 100% BTC from the first fill |
| `pathA-p5/cash` | stays in USDT (no LLM cost) |
| Random Veto | 200 seeds × p ∈ {0.25, 0.50, 0.75}, replayed as full portfolio paths (§16.3) |

The existing forward books pathA-v1.1 and pathA-v1.2 are not touched and are not part of this
experiment.

## The AI filter (FILTER_ONLY, §7.2–7.4)

- **Candidate:** a day on which the Code target weight is **higher** than the AI arm's current
  (or pending) weight. Possible weights are 0, 0.5 and 1.
- The AI answers ALLOW, VETO or ABSTAIN. ALLOW → the AI arm takes the Code target. VETO,
  ABSTAIN, timeout, invalid JSON, bad evidence or a refused/unavailable model → the AI arm keeps
  its current weight (no increase); each is logged in its own category, and only VETO counts as
  a deliberate decision.
- **Never filtered:** decreases and HALT sells. When the Code target is at or below the AI arm's
  weight, the AI arm follows it directly (deterministic exits always apply).
- **Daily diagnostic verdict (not acted on):** every daily close the AI also answers ALLOW/VETO
  on "keep the Code target exposure into the next day". It is recorded with the label
  `DIAGNOSTIC`, never changes any portfolio, and is reported separately from the filter.
- **Input:** `context_profile = quant_only`. Only the frozen snapshot: perp close, SMA100,
  trend flag, 7-day funding with each day's source (actual or proxy), suggested weight, the
  last 30 daily closes and the arm's own weight and drawdown. No news, no browsing, no tools.
  Adding news later is a new experiment, never a mid-run change (§7.1).
- **Output:** strict JSON schema of §7.2 (`decision`, `reason_code` from an allowlist,
  `reason` ≤ 500 chars, ≤ 8 `evidence_refs` that must name fields of the snapshot). Model id,
  request id, prompt hash, token usage, billed cost and the request/response/validation times
  are recorded by the system, not taken from the model.
- **Model, locked for the whole run:** `claude-opus-5-5`, effort `medium`, one attempt per
  question, deadline 120 s, SDK retries off, no fallback model. Changing the model, prompt or
  effort starts a new experiment revision.

## Costs (§11.1, §24.2)

- Each call's billed cost is booked in the AI arm's ledger as an operating cost, so it lowers
  economic equity but not trading equity. Diagnostic calls are booked too.
- Estimate (list price $4 / $20 per million input / output tokens): about 5k input and 2k
  output tokens per call ≈ $0.06, so about **$2 per month** with one call a day. This is an
  estimate; the reports use actual billed usage.
- **Budget cap: $5 per month.** When it is reached the AI arm fails closed (no increases, no
  diagnostic calls) and the run reports it as a reliability incident; no cheaper model is
  swapped in.

## HALT (§9.6)

Each paired arm uses `halt_followup = ABSORBING_UNTIL_ENDPOINT`: a HALT sells to cash and the arm
stays halted until the planned endpoint, with costs still accruing. There is no resume inside
this experiment. (This differs from the pathA-v1.2 forward book, which Note can resume.)

## Timebox and reviews (§17.2, §18.2)

- 28 days from the opening day, with reviews on days 7, 14 and 28. Reviews fix pipeline bugs;
  a fix that changes behaviour opens a new revision and its data is not pooled with the old.
- Day-28 decision, recorded with reasons: `CONTINUE`, `REVISE`, `PAUSE` or
  `INSUFFICIENT_EVIDENCE`.
- Reported (§17.1): economic and trading net return of every arm; drawdown; exposure;
  ALLOW/VETO/ABSTAIN/ERROR counts and eligible coverage; latency p50/p95; billed cost; Random
  Veto distribution vs the AI arm; per-veto missed-gain / avoided-loss counterfactual, marked
  diagnostic only (§16.4); diagnostic verdict counts. Nothing is annualized.

## Acceptance tests before the first forward day

- Code arm reproduces the pathA-v1.2 book exactly on the same bars (same fills, equity within 1e-9).
- AI arm with an always-ALLOW mock equals the Code arm; with an always-VETO mock it never
  increases; decreases and HALT sells pass through in both.
- Invalid JSON, unknown reason code, evidence not in the snapshot, extra fields, a late answer
  and a refusal all become ERROR categories with no increase, and are never counted as VETO.
- Random Veto is deterministic from seed + candidate id, and each seed is a full path replay.
- Cost booking: entries balance; economic equity = trading equity − booked AI cost; the cap
  fails closed.
- The model call has no tools and the snapshot is the only context (prompt hash recorded).

## Needed from Note before code runs live

1. Approve this plan (merge the PR).
2. Put an Anthropic API key in the Asakantrading environment settings as `ANTHROPIC_API_KEY`.
   Never send it in chat. Until then everything is built and tested with recorded mock answers.
3. The $5/month cap and the model above are defaults; say if either should change.

Simulated money only. No exchange orders, no Live, no order adapter.
