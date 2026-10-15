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

## Meanings of reason codes and assessments (`reason_codes.v1`)

Each reason code has a kind so reports never mix a market judgement with a system failure.
`PolicyDecision.reason_kind` carries it; system and eligibility reasons can only end in
`ABSTAIN`, and the validator refuses anything else. Adding or changing a code needs a new
`REASON_CODES_VERSION`.

| Code | Kind | Meaning | Who sets it |
| --- | --- | --- | --- |
| `SUPPORTED_CONTEXT` | market | The evidence supports the proposal's context. | model or synthesis |
| `CONTEXT_CONFLICT` | market | The evidence contradicts the proposal's context. | model or synthesis |
| `UNCERTAIN_CONTEXT` | market | Evidence points both ways; no clear view. | model or synthesis |
| `INSUFFICIENT_EVIDENCE` | market | Not enough evidence to form a view. | model or synthesis |
| `UNRESOLVED_CONTEXT` | market | Conflicts left unresolved by synthesis, or required inputs were missing so an ALLOW cannot stand; always ABSTAIN. | backend policy |
| `EXPIRED_DECISION` | system | The deadline passed before a decision. | backend |
| `BUDGET_UNAVAILABLE` | system | Cost budget could not be reserved. | backend |
| `INVALID_RESPONSE` | system | A model reply failed the schema or evidence checks. | backend |
| `INVALID_REPORT_SET` | system | Analyst reports were missing or inconsistent. | backend |
| `INVALID_SYNTHESIS` | system | The synthesis failed its checks. | backend |
| `REQUIRED_ROLE_UNAVAILABLE` | system | A required analyst did not return a valid report. | backend |
| `OPTIONAL_ROLE_REQUIRED_BY_POLICY` | system | Policy needed an optional analyst that did not answer. | backend |
| `PROVIDER_UNAVAILABLE` | system | The model provider errored or was unreachable. | backend |
| `NOT_EVALUATED_PORTFOLIO_INELIGIBLE` | eligibility | The portfolio was not eligible, so nothing was evaluated (never counted as a veto). | backend |

| Assessment | Meaning |
| --- | --- |
| `SUPPORTS` | The analyst's evidence favours the proposal. |
| `MIXED` | Evidence on both sides. |
| `OPPOSES` | The evidence argues against the proposal. |
| `INSUFFICIENT_DATA` | The analyst could not judge from its inputs. |

Schema versions: `filter_response.v1`, `policy_decision.v1`, `instrument.v1`
(`backend/src/trading/contracts/instruments.py`). Instrument types SPOT, PERP_FUTURE,
DATED_FUTURE and OPTION are defined; only SPOT and PERP_FUTURE are enabled in this build.
Average cost applies to SPOT only; futures use mark-to-market and options premium accounting,
each added when that type is enabled.
