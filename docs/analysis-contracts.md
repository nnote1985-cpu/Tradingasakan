# Common analysis contracts and graph config

BUILDPLAN v4.2 §24 lists "common analysis contracts/graph config" as part of Phase 1
Foundation, and §26.1 asks that the run/attempt envelope, graph config, v1 adapter and
mock extension fixtures pass without turning on real specialists. This is that slice.
No model is called anywhere in it; path A stays alert-only.

| Piece | Where | Spec |
| --- | --- | --- |
| Contracts (`AgentGraphConfig`, `AnalysisRequest`, `FilterResponse`, `AgentAnalysis`, `SynthesisResult`, `ProposalDraft`, `AttemptRecord`, `PolicyDecision`, `AnalysisRun`) | `backend/src/trading/contracts/analysis.py` | §7.2, §7.6–§7.8, §8.2 |
| Graph loading | `backend/src/trading/analysis/graphs.py` | §7.9 |
| Evidence/ID checks beyond the schema | `backend/src/trading/analysis/validation.py` | §7.2, §7.7 |
| v1 filter adapter + multi-filter policy | `backend/src/trading/analysis/policy.py` | §7.4, §7.9 |
| Graph drafts | `configs/analysis/*.yaml` | §7.9 |
| Mock fixtures and worked scenarios | `tests/fixtures/analysis/`, `tests/unit/test_analysis_contracts.py` | §7.9 table, §7.12 |

Rules the code enforces:

- Every model rejects unknown fields; expanding a contract means a new `schema_version`.
- Reason codes are an allowlist. A model may only give `SUPPORTED_CONTEXT`, `CONTEXT_CONFLICT`,
  `UNCERTAIN_CONTEXT` or `INSUFFICIENT_EVIDENCE`; reliability reasons are assigned by the backend.
- Timeouts, invalid output, provider errors, budget exhaustion and expired deadlines all end in
  `ABSTAIN`, never in a credited `VETO`. ALLOW still has to pass final risk.
- The deadline is `min(valid_until, run_start + total_timeout)`; AI never extends a proposal.
- A graph with null models, prompts, timeouts or budgets cannot be enabled, and the analyst
  cutoff, per-role timeouts and cost caps must be consistent before lock. `max_attempts_per_role`
  is fixed at 1 (no silent retries or best-of-n).
- `UNKNOWN` attempts keep a positive cost reservation and never record cost 0.
- Missing inputs come from the backend; a synthesis that omits them is invalid.
- `graph_hash()` covers every field, so any change is a new graph/experiment version.

Both repository graphs (`v1_filter`, `multi_filter_draft_1`) are disabled drafts with nulls until
provider discovery and an experiment lock; the fully pinned graphs live only in test fixtures
with a mock model id. The reason-code allowlist and `Assessment` values are this version's
choice where the buildplan gives only examples.
