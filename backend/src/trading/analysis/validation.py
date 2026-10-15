"""Backend checks on model output beyond schema shape (§7.2, §7.7, §7.8 step 4).

Schema validation proves a response is well formed, not that its claims are
true. These checks add what the schema cannot know: IDs match the request,
evidence exists in the frozen snapshot, and reports belong to this run.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from trading.contracts.analysis import (
    AgentAnalysis,
    AgentGraphConfig,
    AnalysisRequest,
    FilterResponse,
    RoleKind,
    SynthesisResult,
)


def _unknown_refs(refs: Iterable[str], evidence: frozenset[str]) -> list[str]:
    return sorted(set(refs) - evidence)


def filter_response_problems(req: AnalysisRequest, resp: FilterResponse, evidence: frozenset[str]) -> list[str]:
    p: list[str] = []
    if req.proposal_id is None or resp.proposal_id != req.proposal_id:
        p.append("proposal_id does not match the request")
    if resp.snapshot_id != req.snapshot_id:
        p.append("snapshot_id does not match the request")
    if bad := _unknown_refs(resp.evidence_refs, evidence):
        p.append(f"evidence not in snapshot {bad}")
    return p


def analysis_problems(req: AnalysisRequest, graph: AgentGraphConfig, report: AgentAnalysis,
                      evidence: frozenset[str]) -> list[str]:
    p: list[str] = []
    if report.run_id != req.run_id:
        p.append("run_id does not match the request")
    if report.snapshot_id != req.snapshot_id:
        p.append("snapshot_id does not match the request")
    analysts = {r.role_id for r in graph.roles_of(RoleKind.ANALYST)}
    if report.role_id not in analysts:
        p.append(f"role {report.role_id} is not an analyst in this graph")
    refs = [r for f in report.findings for r in f.evidence_refs] + list(report.counterevidence_refs)
    if bad := _unknown_refs(refs, evidence):
        p.append(f"evidence not in snapshot {bad}")
    return p


def synthesis_problems(req: AnalysisRequest, valid_reports: Mapping[str, AgentAnalysis], synth: SynthesisResult,
                       evidence: frozenset[str], missing_required_inputs: frozenset[str]) -> list[str]:
    """Synthesis may cite only validated reports from this run and must trace back to original evidence."""
    p: list[str] = []
    if synth.run_id != req.run_id:
        p.append("run_id does not match the request")
    if bad := sorted(set(synth.cited_report_ids) - set(valid_reports)):
        p.append(f"cites reports that are missing or invalid {bad}")
    claims = {f.claim_id for r in valid_reports.values() for f in r.findings}
    if bad := sorted(set(synth.cited_claim_ids) - claims):
        p.append(f"cites unknown claims {bad}")
    if bad := _unknown_refs(synth.evidence_refs, evidence):
        p.append(f"evidence not in snapshot {bad}")
    # Missing inputs come from the backend; the model cannot declare data complete.
    if bad := sorted(missing_required_inputs - set(synth.missing_inputs)):
        p.append(f"omits missing required inputs {bad}")
    return p
