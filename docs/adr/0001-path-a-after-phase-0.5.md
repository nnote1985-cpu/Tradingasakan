# ADR 0001: Phase 0.5 findings; path A as one research track; keep buildplan Phase 1 Foundation

Status: accepted 2026-10-15, revised 2026-10-15 after the owner's review.

## Context

BUILDPLAN v4.2 §24 says that when Phase 0.5 shows few signals, missing data or
high costs, the hypothesis or scope changes through an ADR and the trial
registry. Phase 0.5 results, with dates, costs, trades, exposure, commands and
commits: docs/research/results-register.md. Research hypotheses are named
R1–R5 there so they are not confused with the roadmap's v1/v2/v3.

## Finding

**ยังไม่พบหลักฐานเพียงพอว่ากลยุทธ์ที่ทดลองในข้อมูลและเงื่อนไขชุดนี้เหนือกว่า Buy & Hold**
(no sufficient evidence yet that the strategies tested, on this data and under
these conditions, beat buy & hold).

- This is about R1–R5 on BTC/ETH/SOL 2022–2025 only. It does not show that other
  strategies or AI cannot add value: no AI was tested.
- R5 (pathA-v1) on the holdout 2025-01-25 → 2025-12-31: net −6.9% against buy &
  hold −16.5%. It is not profitable, it beats the benchmark on Sharpe only, and
  one year is not enough evidence either way. Losing less in a falling market is
  interesting for loss reduction, not evidence of profit.
- Power assumptions and limits: docs/research/pathA-power-assumptions.md.

## Decision

1. **The system goal does not change.** It stays a system where Code and AI
   strategies keep improving, built to support Futures, Spot and Options,
   enabled step by step.
2. **Path A is one research track and paper book**, not a replacement for that
   goal. Its frozen book (`pathA-v1`) keeps running as a forward test.
3. **Phase 1 stays as defined in §24 (Foundation)**: pinned monorepo/config/CI,
   single-workspace auth, minimum ledger schema in the private `trading` schema,
   API shell, common analysis contracts/graph config. It is strategy-agnostic and
   does not wait for any strategy to prove a profit.
4. The 2025 holdout is spent (opened 2026-10-15 08:57 UTC). It may be used for
   development but must not be called unseen confirmation data again.
5. Work done ahead of order (receipt-time stamps, checksums, gap checks, paper
   ledger, fill timing) is kept and later moved onto the Foundation schema.
6. The interim `paper` schema in Supabase is a one-way mirror of the JSON books;
   the JSON files are the source of truth until a separate cutover decision.
