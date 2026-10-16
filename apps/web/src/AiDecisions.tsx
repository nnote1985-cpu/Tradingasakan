import { useQuery } from "@tanstack/react-query";
import { type AgentDecision, type ApiError, apiGet, type DecisionsResponse, type ScorecardItem, type ScorecardResponse, type Verdict } from "./api";
import { pct } from "./format";

const OUTCOME_TH: Record<string, string> = { ALLOW: "ให้เพิ่ม", VETO: "ไม่ให้เพิ่ม", ABSTAIN: "งดออกความเห็น" };
const VERDICT_BADGE: Record<Verdict, { text: string; cls: string }> = {
  avoided_loss: { text: "ช่วย: เลี่ยงขาดทุน", cls: "bg-[var(--gain)] text-white" },
  allowed_gain: { text: "ช่วย: ได้กำไร", cls: "bg-[var(--gain)] text-white" },
  missed_gain: { text: "พลาด: ตกรถ", cls: "bg-[var(--loss)] text-white" },
  allowed_loss: { text: "พลาด: ขาดทุน", cls: "bg-[var(--loss)] text-white" },
  flat: { text: "ราคาไม่เปลี่ยน", cls: "border border-[var(--border)]" },
  pending: { text: "รอผล", cls: "bg-[var(--warn-bg)] text-[var(--warn-fg)]" },
};

function usd(v: string | null) {
  if (v === null) return "–";
  const [i, f = ""] = v.split(".");
  return `$${i}.${(f + "0000").slice(0, 4)}`;
}

function Counts({ title, counts }: { title: string; counts: Record<string, number> | undefined }) {
  const entries = Object.entries(counts ?? {});
  return (
    <div className="rounded-lg border border-[var(--border)] p-2">
      <div className="text-xs text-[var(--text-muted)]">{title}</div>
      <div className="mt-0.5 text-sm">
        {entries.length === 0 ? "ยังไม่มี" : entries.map(([k, n]) => `${OUTCOME_TH[k] ?? k} ${n}`).join(" · ")}
      </div>
    </div>
  );
}

/** One decision as a card: what it changed, why, and how it scored N days later (diagnostic only). */
function DecisionCard({ i, score, horizon }: { i: AgentDecision; score: ScorecardItem | undefined; horizon: number | undefined }) {
  const diag = i.kind === "diagnostic";
  const failed = i.attempt_status !== "SUCCESS";
  const badge = score && !diag ? VERDICT_BADGE[score.verdict] : null;
  return (
    <li data-testid="decision-row" className={`rounded-lg border border-[var(--border)] p-3 ${diag ? "text-[var(--text-muted)]" : ""}`}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
        <span className="rounded border border-[var(--border)] px-1.5 text-xs">{diag ? "ความเห็น (ไม่มีผล)" : "กรอง"}</span>
        <span className="font-medium">{i.decided_on}</span>
        <span className="tabular-nums">BTC {pct(i.current_weight, true)} → {pct(i.target_weight, true)}</span>
        <span className="font-medium">
          {failed && <span className="mr-1 rounded bg-[var(--warn-bg)] px-1 text-[var(--warn-fg)]">ผิดพลาด</span>}
          {OUTCOME_TH[i.outcome] ?? i.outcome}
        </span>
        {badge && (
          <span className="ml-auto flex items-center gap-2">
            {score?.forward_return != null && <span className="text-xs tabular-nums">BTC {horizon} วันถัดมา {pct(score.forward_return)}</span>}
            <span data-testid="verdict" className={`rounded px-1.5 text-xs ${badge.cls}`}>{badge.text}</span>
          </span>
        )}
      </div>
      <p className="mt-1.5 text-sm">
        <span className="text-[var(--text-muted)]">เหตุผล: </span>
        {i.reason ?? (failed ? `ไม่ได้คำตอบ (${i.error_category ?? i.outcome})` : "–")}
      </p>
      <p className="mt-1 text-xs text-[var(--text-muted)]">
        {i.reason_code ?? "–"} · ค่าใช้จ่าย {usd(i.cost_usd)} · ตอบใน {i.latency_ms === null ? "–" : `${(i.latency_ms / 1000).toFixed(1)} วิ`}
      </p>
    </li>
  );
}

/** Phase 5 AI arm: what the filter decided and what it cost. Diagnostic answers are shown apart: they are recorded only. */
export function AiDecisions({ label }: { label: string }) {
  const q = useQuery<DecisionsResponse, ApiError>({
    queryKey: ["decisions", label], queryFn: () => apiGet<DecisionsResponse>(`/api/v1/portfolios/${label}/decisions`), refetchInterval: 60_000,
  });
  // Same query as the scorecard above, so it is fetched once.
  const sc = useQuery<ScorecardResponse, ApiError>({
    queryKey: ["ai-scorecard", label], queryFn: () => apiGet<ScorecardResponse>(`/api/v1/portfolios/${label}/ai-scorecard`), refetchInterval: 300_000,
  });
  if (q.isPending) return null;
  if (q.isError) return <p role="alert" className="text-sm text-[var(--loss)]">{q.error.message ?? "โหลดการตัดสินใจของ AI ไม่สำเร็จ"}</p>;
  if (!q.data.available) return <p className="text-sm text-[var(--text-muted)]">ยังไม่มีบันทึกการตัดสินใจของ AI ในฐานข้อมูล</p>;
  const d = q.data;
  const scores = new Map((sc.data?.available ? sc.data.items : []).map((s) => [s.proposal_id, s]));
  return (
    <section data-testid="ai-decisions" className="space-y-2">
      <h3 className="text-sm font-medium text-[var(--text-secondary)]">บันทึกการตัดสินใจของ AI (พร้อมเหตุผล)</h3>
      <div className="grid gap-2 sm:grid-cols-3">
        <Counts title="กรองการเพิ่ม BTC (มีผลกับพอร์ต)" counts={d.counts.filter} />
        <Counts title="ความเห็นรายวัน (บันทึกไว้เฉยๆ ไม่มีผล)" counts={d.counts.diagnostic} />
        <div className="rounded-lg border border-[var(--border)] p-2">
          <div className="text-xs text-[var(--text-muted)]">ค่า AI รวม</div>
          <div className="mt-0.5 text-sm">{usd(d.cost_usd)}</div>
        </div>
      </div>
      {d.items.length > 0 && (
        <>
          <ul className="space-y-2">
            {d.items.map((i) => <DecisionCard key={i.proposal_id} i={i} score={scores.get(i.proposal_id)} horizon={sc.data?.horizon_days} />)}
          </ul>
          {d.items.length >= d.limit && <p className="text-xs text-[var(--text-muted)]">แสดง {d.limit} รายการล่าสุด</p>}
        </>
      )}
    </section>
  );
}
