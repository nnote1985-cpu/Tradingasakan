import { useQuery } from "@tanstack/react-query";
import { AiDecisions } from "./AiDecisions";
import { type AiSettings, type ApiError, apiGet, type PortfolioRow, type ScorecardResponse, type Verdict } from "./api";
import { armOf } from "./books";
import { pct } from "./format";

const STAGE_TH: Record<string, string> = { FILTER_ONLY: "กรองเฉพาะการเพิ่ม BTC (ลดหรือขายตอน HALT ผ่านเสมอ)" };
const CONTEXT_TH: Record<string, string> = { quant_only: "ตัวเลขราคาและสัญญาณเท่านั้น ไม่มีข่าว" };

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between gap-4 border-b border-[var(--grid)] py-1.5 last:border-0">
      <dt className="text-[var(--text-secondary)]">{k}</dt>
      <dd className="text-right">{v}</dd>
    </div>
  );
}

/** Read-only: the Phase 5 run is locked; changing any value means a new run, not an edit. */
function Settings() {
  const q = useQuery<AiSettings, ApiError>({ queryKey: ["ai-settings"], queryFn: () => apiGet<AiSettings>("/api/v1/ai/settings"), staleTime: Infinity });
  if (q.isPending) return null;
  if (q.isError) return <p role="alert" className="text-sm text-[var(--loss)]">{q.error.message ?? "โหลดค่าตั้ง AI ไม่สำเร็จ"}</p>;
  const s = q.data;
  return (
    <section data-testid="ai-settings" className="rounded-xl border border-[var(--border)] bg-[var(--surface-1)] p-4">
      <div className="flex flex-wrap items-baseline gap-2">
        <h2 className="font-semibold">ค่าตั้ง AI</h2>
        <span className="text-sm text-[var(--text-muted)]">{s.experiment_id}</span>
        {s.locked && <span className="ml-auto rounded bg-[var(--warn-bg)] px-2 py-0.5 text-xs text-[var(--warn-fg)]">ล็อกไว้ตลอดรอบทดลอง เปลี่ยนค่า = เริ่มรอบใหม่</span>}
      </div>
      <dl className="mt-3 grid gap-x-8 text-sm sm:grid-cols-2">
        <div>
          <Row k="โมเดล" v={`${s.model_id} (effort ${s.effort})`} />
          <Row k="หน้าที่" v={STAGE_TH[s.capability_stage] ?? s.capability_stage} />
          <Row k="ถ้า AI ตอบไม่ได้" v="ไม่เพิ่ม BTC (ปลอดภัยไว้ก่อน)" />
          <Row k="ข้อมูลที่ AI เห็น" v={CONTEXT_TH[s.context_profile] ?? s.context_profile} />
          <Row k="เครื่องมือ" v={s.tools.length ? s.tools.join(", ") : "ไม่มี"} />
          <Row k="รอคำตอบนานสุด" v={`${s.timeout_ms / 1000} วินาที, ถามได้ ${s.max_attempts} ครั้ง`} />
        </div>
        <div>
          <Row k="งบต่อครั้ง" v={`$${s.max_run_cost_usd}`} />
          <Row k="งบต่อวัน" v={`$${s.daily_cost_cap_usd}`} />
          <Row k="งบต่อเดือน" v={`$${s.monthly_cap_usd}`} />
          <Row k="ระยะทดลอง" v={`${s.timebox_days} วัน`} />
          <Row k="เวอร์ชัน prompt" v={`${s.prompt_version} · ${s.prompt_hash.slice(0, 8)}`} />
          <Row k="เวอร์ชันนโยบาย" v={s.policy_version} />
        </div>
      </dl>
    </section>
  );
}

const VERDICT_TH: Record<Verdict, string> = {
  avoided_loss: "ไม่ให้เพิ่ม แล้วราคาลง",
  missed_gain: "ไม่ให้เพิ่ม แต่ราคาขึ้น",
  allowed_gain: "ให้เพิ่ม แล้วราคาขึ้น",
  allowed_loss: "ให้เพิ่ม แต่ราคาลง",
  flat: "ราคาไม่เปลี่ยน",
  pending: "รอผล",
};
const HELPED: Verdict[] = ["avoided_loss", "allowed_gain"];
const MISSED: Verdict[] = ["missed_gain", "allowed_loss"];
const OUTCOME_TH: Record<string, string> = { ALLOW: "ให้เพิ่ม", VETO: "ไม่ให้เพิ่ม", ABSTAIN: "งดออกความเห็น" };

function tally(c: Partial<Record<Verdict, number>> | undefined, vs: Verdict[]) {
  return vs.reduce((n, v) => n + (c?.[v] ?? 0), 0);
}

/** Diagnostic only (§16.4): each answer against the BTC close N days later. It never changes a portfolio. */
function Scorecard({ label }: { label: string }) {
  const q = useQuery<ScorecardResponse, ApiError>({
    queryKey: ["ai-scorecard", label], queryFn: () => apiGet<ScorecardResponse>(`/api/v1/portfolios/${label}/ai-scorecard`), refetchInterval: 300_000,
  });
  if (q.isPending) return null;
  if (q.isError) return <p role="alert" className="text-sm text-[var(--loss)]">{q.error.message ?? "โหลดสกอร์ AI ไม่สำเร็จ"}</p>;
  if (!q.data.available) return <p className="text-sm text-[var(--text-muted)]">ยังไม่มีบันทึกการตัดสินใจของ AI ในฐานข้อมูล</p>;
  const d = q.data;
  const f = d.counts.filter;
  return (
    <section data-testid="ai-scorecard" className="space-y-2">
      <h3 className="font-medium">AI ช่วยหรือพลาด</h3>
      <p className="text-xs text-[var(--text-muted)]">
        เทียบคำตอบของ AI กับราคาปิด BTC อีก {d.horizon_days} วันถัดมา ใช้ดูอย่างเดียว ไม่มีผลกับพอร์ต และยังสรุปไม่ได้จนกว่าจะมีหลายสิบครั้ง
      </p>
      <div className="grid gap-2 sm:grid-cols-3">
        <Stat title="ช่วย" n={tally(f, HELPED)} tone="gain" />
        <Stat title="พลาด" n={tally(f, MISSED)} tone="loss" />
        <Stat title={`รอครบ ${d.horizon_days} วัน`} n={tally(f, ["pending"])} />
      </div>
      {d.items.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs [&_td]:py-1 [&_td]:pr-3 [&_th]:pr-3 [&_th]:text-left [&_th]:font-normal [&_th]:text-[var(--text-muted)]">
            <thead>
              <tr><th>ราคาปิดวันที่</th><th>ประเภท</th><th>คำตอบ</th><th className="!text-right">BTC {d.horizon_days} วันถัดมา</th><th>ผล</th></tr>
            </thead>
            <tbody>
              {d.items.map((i) => {
                const diag = i.kind === "diagnostic";
                const tone = HELPED.includes(i.verdict) ? "text-[var(--gain)]" : MISSED.includes(i.verdict) ? "text-[var(--loss)]" : "";
                return (
                  <tr key={i.proposal_id} data-testid="score-row" className={diag ? "text-[var(--text-muted)]" : undefined}>
                    <td className="whitespace-nowrap">{i.decided_on}</td>
                    <td className="whitespace-nowrap">{diag ? "ความเห็น (ไม่มีผล)" : "กรอง"}</td>
                    <td className="whitespace-nowrap">{OUTCOME_TH[i.outcome] ?? `${i.outcome} (ไม่เพิ่ม)`}</td>
                    <td className="text-right whitespace-nowrap">{i.forward_return === null ? "–" : pct(i.forward_return)}</td>
                    <td className={`whitespace-nowrap ${diag ? "" : tone}`}>{VERDICT_TH[i.verdict]}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Stat({ title, n, tone }: { title: string; n: number; tone?: "gain" | "loss" }) {
  return (
    <div className="rounded-lg border border-[var(--border)] p-2">
      <div className="text-xs text-[var(--text-muted)]">{title}</div>
      <div className={`mt-0.5 text-lg font-medium ${tone === "gain" ? "text-[var(--gain)]" : tone === "loss" ? "text-[var(--loss)]" : ""}`}>{n}</div>
    </div>
  );
}

export function AiTab({ rows }: { rows: PortfolioRow[] }) {
  const ai = rows.filter((r) => armOf(r.label).key === "ai").map((r) => r.label);
  return (
    <div className="space-y-4">
      {ai.length === 0 ? (
        <p className="panel text-sm text-[var(--text-secondary)]">ยังไม่มีพอร์ต AI ที่รายงานผล การตัดสินใจจะแสดงที่นี่เมื่อเริ่มบันทึกการทดลอง</p>
      ) : (
        ai.map((label) => (
          <section key={label} className="space-y-4 rounded-xl border border-[var(--border)] bg-[var(--surface-1)] p-4">
            <h2 className="font-semibold">{label}</h2>
            <Scorecard label={label} />
            <AiDecisions label={label} />
          </section>
        ))
      )}
      <details className="panel"><summary className="cursor-pointer font-medium">ค่าตั้งและขอบเขตการทดลอง AI</summary><div className="mt-4"><Settings /></div></details>
    </div>
  );
}
