import { useQuery } from "@tanstack/react-query";
import { type ApiError, apiGet, type DecisionsResponse } from "./api";
import { pct } from "./format";

const OUTCOME_TH: Record<string, string> = { ALLOW: "ให้เพิ่ม", VETO: "ไม่ให้เพิ่ม", ABSTAIN: "งดออกความเห็น" };

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

/** Phase 5 AI arm: what the filter decided and what it cost. Diagnostic answers are shown apart: they are recorded only. */
export function AiDecisions({ label }: { label: string }) {
  const q = useQuery<DecisionsResponse, ApiError>({
    queryKey: ["decisions", label], queryFn: () => apiGet<DecisionsResponse>(`/api/v1/portfolios/${label}/decisions`), refetchInterval: 60_000,
  });
  if (q.isPending) return null;
  if (q.isError) return <p role="alert" className="text-sm text-[var(--loss)]">{q.error.message ?? "โหลดการตัดสินใจของ AI ไม่สำเร็จ"}</p>;
  if (!q.data.available) return <p className="text-sm text-[var(--text-muted)]">ยังไม่มีบันทึกการตัดสินใจของ AI ในฐานข้อมูล</p>;
  const d = q.data;
  return (
    <section data-testid="ai-decisions" className="space-y-2">
      <h3 className="text-sm font-medium text-[var(--text-secondary)]">การตัดสินใจของ AI</h3>
      <div className="grid gap-2 sm:grid-cols-3">
        <Counts title="กรองการเพิ่ม BTC (มีผลกับพอร์ต)" counts={d.counts.filter} />
        <Counts title="ความเห็นรายวัน (บันทึกไว้เฉยๆ ไม่มีผล)" counts={d.counts.diagnostic} />
        <div className="rounded-lg border border-[var(--border)] p-2">
          <div className="text-xs text-[var(--text-muted)]">ค่า AI รวม</div>
          <div className="mt-0.5 text-sm">{usd(d.cost_usd)}</div>
        </div>
      </div>
      {d.items.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs [&_td]:py-1 [&_td]:pr-3 [&_th]:pr-3 [&_th]:text-left [&_th]:font-normal [&_th]:text-[var(--text-muted)]">
            <thead>
              <tr><th>ราคาปิดวันที่</th><th>ประเภท</th><th>สัดส่วน BTC</th><th>ผล</th><th>เหตุผล</th><th className="!text-right">ค่าใช้จ่าย</th><th className="!text-right">เวลาตอบ</th></tr>
            </thead>
            <tbody>
              {d.items.map((i) => {
                const diag = i.kind === "diagnostic";
                return (
                  <tr key={i.proposal_id} data-testid="decision-row" className={diag ? "text-[var(--text-muted)]" : undefined}>
                    <td className="whitespace-nowrap">{i.decided_on}</td>
                    <td className="whitespace-nowrap">{diag ? "ความเห็น (ไม่มีผล)" : "กรอง"}</td>
                    <td className="whitespace-nowrap">{pct(i.current_weight, true)} → {pct(i.target_weight, true)}</td>
                    <td className="whitespace-nowrap font-medium">
                      {i.attempt_status !== "SUCCESS" && <span className="mr-1 rounded bg-[var(--warn-bg)] px-1 text-[var(--warn-fg)]">ผิดพลาด</span>}
                      {OUTCOME_TH[i.outcome] ?? i.outcome}
                    </td>
                    <td title={i.reason ?? undefined}>{i.reason_code ?? "–"}</td>
                    <td className="text-right whitespace-nowrap">{usd(i.cost_usd)}</td>
                    <td className="text-right whitespace-nowrap">{i.latency_ms === null ? "–" : `${(i.latency_ms / 1000).toFixed(1)} วิ`}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {d.items.length >= d.limit && <p className="text-xs text-[var(--text-muted)]">แสดง {d.limit} รายการล่าสุด</p>}
        </div>
      )}
    </section>
  );
}
