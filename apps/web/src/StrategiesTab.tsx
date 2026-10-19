import { useQuery } from "@tanstack/react-query";
import { type ApiError, apiGet, type BotProfile, type LibraryStrategy } from "./api";
import { DraftProfiles } from "./DraftProfiles";

const FAMILY_TH: Record<string, string> = { trend: "ตามเทรนด์", momentum: "โมเมนตัม", mean_reversion: "สวนกลับ" };
const COMBINE_TH: Record<BotProfile["combine"], string> = {
  average: "เฉลี่ยน้ำหนักของทุกกลยุทธ์ แล้วปัดลงเป็น 0 / 0.5 / 1",
  main_with_filters: "กลยุทธ์แรกเป็นตัวหลัก ถ้ากลยุทธ์อื่นตัวใดให้ 0 จะไม่ถือ BTC",
};
const STATUS_TH: Record<BotProfile["status"], string> = { draft: "ร่าง ยังไม่ผูกกับพอร์ต", active: "ระบุเป็น Active ในค่าตั้ง", retired: "เลิกใช้" };
const EVIDENCE_TH: Record<string, string> = { untested: "ยังไม่ทดสอบกับข้อมูลเรา" };

function params(p: Record<string, number>): string {
  const e = Object.entries(p);
  return e.length ? e.map(([k, v]) => `${k} ${v}`).join(", ") : "–";
}

function Profiles() {
  const q = useQuery<{ items: BotProfile[] }, ApiError>({ queryKey: ["bot-profiles"], queryFn: () => apiGet("/api/v1/bot-profiles"), staleTime: 60_000 });
  if (q.isPending) return <p>กำลังโหลด…</p>;
  if (q.isError) return <p role="alert" className="text-sm text-[var(--loss)]">{q.error.message ?? "โหลด bot profile ไม่สำเร็จ"}</p>;
  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold">Bot profiles · ชุดกลยุทธ์ของบอต</h2>
      <p className="text-sm text-[var(--text-secondary)]">
        Profile กำหนดว่าบอตจะใช้กลยุทธ์อะไรบ้าง พร้อม timeframe พารามิเตอร์ วิธีรวมสัญญาณ และความเสี่ยง การเปลี่ยนชุดกลยุทธ์ต้องออกเวอร์ชันใหม่
      </p>
      {q.data.items.length === 0 && <p>ยังไม่มี profile</p>}
      {q.data.items.map((p) => (
        <article key={`${p.profile_id}@${p.version}`} data-testid="bot-profile" className="rounded-xl border border-[var(--border)] bg-[var(--surface-1)] p-4">
          <div className="flex flex-wrap items-baseline gap-2">
            <h3 className="font-semibold">{p.name}</h3>
            <span className="text-sm text-[var(--text-muted)]">เวอร์ชัน {p.version}</span>
            <span className="ml-auto rounded bg-[var(--warn-bg)] px-2 py-0.5 text-xs text-[var(--warn-fg)]">{STATUS_TH[p.status]}</span>
          </div>
          <p className="mt-2 text-sm">
            กลยุทธ์ <span className="font-medium">{p.strategies.map((s) => `#${s.number}`).join(", ")}</span> · {COMBINE_TH[p.combine]}
          </p>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-[var(--text-secondary)]">
                <tr><th className="py-1 pr-3">#</th><th className="pr-3">กลยุทธ์</th><th className="pr-3">Timeframe</th><th>ค่าตั้ง</th></tr>
              </thead>
              <tbody>
                {p.strategies.map((s, i) => (
                  <tr key={s.number} className="border-t border-[var(--grid)]">
                    <td className="py-1 pr-3">{s.number}</td>
                    <td className="pr-3">{s.name}{p.combine === "main_with_filters" && <span className="text-[var(--text-muted)]">{i === 0 ? " (หลัก)" : " (ตัวกรอง)"}</span>}</td>
                    <td className="pr-3">{s.timeframe}</td>
                    <td>{params(s.params)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-xs text-[var(--text-muted)]">{p.instrument} · ตัดสินใจทุกวันตอนปิดแท่งรายวัน (UTC) · ความเสี่ยง {p.risk_profile} · AI {p.ai_mode === "off" ? "ปิด" : p.ai_mode}{p.notes && ` · ${p.notes}`}</p>
          <details className="mt-3 text-sm text-[var(--text-secondary)]"><summary className="cursor-pointer">รหัสและเวอร์ชันอ้างอิง</summary><p className="mt-2 break-all">{p.profile_id} · v{p.version} · {p.hash}</p></details>
        </article>
      ))}
    </section>
  );
}

function Library() {
  const q = useQuery<{ items: LibraryStrategy[] }, ApiError>({ queryKey: ["strategies"], queryFn: () => apiGet("/api/v1/strategies"), staleTime: 60_000 });
  if (q.isPending) return <p role="status">กำลังโหลดคลังกลยุทธ์…</p>;
  if (q.isError) return <p role="alert" className="text-sm text-[var(--loss)]">{q.error.message ?? "โหลดคลังกลยุทธ์ไม่สำเร็จ"}</p>;
  return (
    <section className="space-y-2">
      <h2 className="text-lg font-semibold">คลังกลยุทธ์ · วิธีเทรดสำหรับประกอบ profile</h2>
      <p className="text-sm text-[var(--text-secondary)]">กลยุทธ์เป็นส่วนประกอบของ Bot profile ไม่ใช่บอตที่กำลังรัน ทุกกลยุทธ์ให้น้ำหนัก BTC spot เป็น 0, 0.5 หรือ 1 ไม่มี short หรือ leverage</p>
      {q.data.items.length === 0 && <p className="panel">ยังไม่มีกลยุทธ์ในคลัง</p>}
      <div className="overflow-x-auto rounded-xl border border-[var(--border)]">
        <table data-testid="strategy-library" className="w-full text-sm">
          <thead className="bg-[var(--surface-1)] text-left text-[var(--text-secondary)]">
            <tr><th className="px-3 py-2">#</th><th className="pr-3">ชื่อ</th><th className="pr-3">ประเภท</th><th className="pr-3">Timeframe</th><th className="pr-3">ค่าตั้งเริ่มต้น</th><th>ผลทดสอบ</th></tr>
          </thead>
          <tbody>
            {q.data.items.map((s) => (
              <tr key={s.number} className="border-t border-[var(--grid)] align-top">
                <td className="px-3 py-2">{s.number}</td>
                <td className="py-2 pr-3"><div className="font-medium">{s.name}</div><div className="text-xs text-[var(--text-muted)]">{s.summary}</div></td>
                <td className="py-2 pr-3">{FAMILY_TH[s.family] ?? s.family}</td>
                <td className="py-2 pr-3">{s.timeframes.join(", ")}</td>
                <td className="py-2 pr-3">{params(Object.fromEntries(Object.entries(s.params).map(([k, b]) => [k, b.default])))}</td>
                <td className="py-2 text-[var(--text-secondary)]">{EVIDENCE_TH[s.evidence] ?? s.evidence}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function StrategiesTab({ view, onSelect }: { view: "profiles" | "library"; onSelect: (view: "profiles" | "library") => void }) {
  return (
    <div className="space-y-6">
      <div className="notice">ตั้งค่าและบันทึก Draft Profile ได้ · ยังไม่เชื่อม worker, Backtest หรือการส่งคำสั่งเทรด สถานะ profile ไม่ได้ยืนยันว่าบอตกำลังใช้ชุดนี้</div>
      <div className="flex flex-wrap gap-2" role="group" aria-label="หมวดการตั้งค่า">
        <button type="button" className={`filter-button ${view === "profiles" ? "filter-active" : ""}`} aria-pressed={view === "profiles"} onClick={() => onSelect("profiles")}>Bot profiles</button>
        <button type="button" className={`filter-button ${view === "library" ? "filter-active" : ""}`} aria-pressed={view === "library"} onClick={() => onSelect("library")}>คลังกลยุทธ์</button>
      </div>
      <p className="text-sm text-[var(--text-secondary)]">คลังกลยุทธ์ → จัดเป็น Bot profile → ผูกกับบอต ส่วนผล Backtest และการทดลองอยู่ใน <a className="underline" href="#research">งานวิจัย</a></p>
      {view === "profiles" ? <><DraftProfiles /><details className="panel"><summary className="cursor-pointer font-medium">ดู preset รุ่นเดิมและชุดสัญญาณที่ใช้เป็นต้นแบบ</summary><div className="mt-4"><Profiles /></div></details></> : <><Library /><section className="panel"><h2 className="font-semibold">กฎ Stop-loss สำหรับร่าง</h2><p className="mt-2 text-sm">ATR จากราคาเข้า · Trailing ATR · เปอร์เซ็นต์จากราคาเข้า</p><p className="mt-2 text-sm text-[var(--text-secondary)]">เลือกและตั้งค่าได้ใน Bot profiles พร้อม Risk / Trade และ Max Exposure กฎเหล่านี้ยังเป็นข้อกำหนดของร่าง ไม่มี engine ประเมินหรือส่งคำสั่ง</p></section></>}
    </div>
  );
}
