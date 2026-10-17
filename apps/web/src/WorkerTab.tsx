import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { type ApiError, apiGet, type WorkerExecution, type WorkerStatus, type WorkersResponse } from "./api";

// Read-only status of the Testnet worker (testnet-development-plan.md §9). The worker writes this to
// Supabase about every 10 s; nothing on this page sends a command to it. Pause/resume come with P5.

const STREAM_TH: Record<string, string> = { klines: "แท่งราคา (Mainnet)", quotes: "ราคา Testnet", account: "บัญชี Testnet" };
const VERDICT_TH: Record<string, string> = { MATCH: "ตรงกัน", DISCREPANCY: "ไม่ตรง", INCONCLUSIVE: "ยังสรุปไม่ได้" };

function ago(iso: string | null): string {
  if (!iso) return "–";
  const s = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 1000));
  return s < 90 ? `${s} วินาทีที่แล้ว` : s < 5400 ? `${Math.round(s / 60)} นาทีที่แล้ว` : `${Math.round(s / 3600)} ชั่วโมงที่แล้ว`;
}

function utc(ms: number | null): string {
  return ms === null ? "–" : new Date(ms).toISOString().slice(0, 16).replace("T", " ") + " UTC";
}

function Dot({ ok }: { ok: boolean | null | undefined }) {
  const c = ok === null || ok === undefined ? "bg-[var(--series-cash)]" : ok ? "bg-[var(--gain)]" : "bg-[var(--loss)]";
  return <span aria-hidden className={`mr-1.5 inline-block h-2 w-2 rounded-full ${c}`} />;
}

function Row({ label, ok, children }: { label: string; ok?: boolean | null; children: ReactNode }) {
  return (
    <div className="flex items-baseline gap-2 border-t border-[var(--grid)] py-1.5 text-sm">
      <span className="w-40 shrink-0 text-[var(--text-secondary)]">{label}</span>
      <span>{ok !== undefined && <Dot ok={ok} />}{children}</span>
    </div>
  );
}

function ExecutionRows({ x }: { x: WorkerExecution }) {
  const c = x.counts;
  return (
    <>
      <Row label="คำสั่ง Testnet" ok={!x.last_error && x.unresolved.length === 0}>
        วันนี้ส่ง {x.orders_today} · ยกเลิก {x.cancels_today} · เปิดอยู่ {x.open_orders}
        {" "}· รอบนี้ เต็ม {c.filled ?? 0} ยกเลิก {c.canceled ?? 0} ถูกปฏิเสธ {c.rejected ?? 0} หมดอายุ {c.expired ?? 0}
        {x.last_error && <span className="text-[var(--loss)]"> · {x.last_error}</span>}
      </Row>
      {x.unresolved.length > 0 && (
        <ul role="alert" className="ml-40 list-disc pl-6 text-sm text-[var(--loss)]">
          {x.unresolved.map((u) => <li key={u.client_order_id}>{u.client_order_id} {u.state} มา {u.for_s} วินาที (ยังไม่เปิดคำสั่งใหม่จนกว่าจะรู้ผล)</li>)}
        </ul>
      )}
      <Row label="Conformance (ทดสอบระบบ)">
        {x.conformance ? `${x.conformance.cycle ?? "–"} · ${x.conformance.note || "–"} · ครบ ${x.conformance.cycles_done} รอบ` : "ปิดอยู่"}
      </Row>
    </>
  );
}

function WorkerCard({ w }: { w: WorkerStatus }) {
  const r = w.reconcile;
  return (
    <article data-testid="worker-card" className="rounded-xl border border-[var(--border)] bg-[var(--surface-1)] p-4">
      <div className="flex flex-wrap items-baseline gap-2">
        <h2 className="font-semibold">Worker บน {w.host}</h2>
        <span className="text-sm text-[var(--text-muted)]">บัญชี {w.account} · {w.mode ?? "–"}</span>
        <span className={`ml-auto rounded px-2 py-0.5 text-xs ${w.silent ? "bg-[var(--loss)] text-white" : "bg-[var(--warn-bg)] text-[var(--warn-fg)]"}`}>
          {w.silent ? "ไม่ตอบ" : "ทำงานอยู่"}
        </span>
      </div>
      {w.silent && (
        <p role="alert" className="mt-2 text-sm text-[var(--loss)]">
          Worker ไม่ได้ส่งสถานะมา {ago(w.updated_at)} ให้ดูที่เครื่อง {w.host} ว่ายังเปิดอยู่ หรือดับเบิลคลิก update-worker.cmd เพื่อเริ่มใหม่
        </p>
      )}
      <div className="mt-3">
        <Row label="อัปเดตล่าสุด" ok={!w.silent}>{ago(w.updated_at)} · เริ่มรัน {ago(w.started_at)}</Row>
        <Row label="สถานะความปลอดภัย">{w.safety ?? "–"}{w.lease_token !== null && ` · lease ${w.lease_token}`}</Row>
        <Row label="นาฬิกาเทียบ Testnet" ok={w.clock.status === null ? null : w.clock.status === "OK"}>
          {w.clock.offset_ms === null ? "–" : `${w.clock.offset_ms > 0 ? "+" : ""}${w.clock.offset_ms} ms`} {w.clock.status ?? ""}
          {w.clock.signing_paused && " · หยุดเซ็นคำสั่งชั่วคราว"}
        </Row>
        <Row label="กุญแจ Testnet" ok={w.account_ok}>{w.account_ok === null ? "ยังไม่ตรวจ" : w.account_ok ? "ใช้ได้" : "ใช้ไม่ได้"}</Row>
        <Row label="Dead-man ภายนอก" ok={w.deadman_ok}>{w.deadman_ok === null ? "ไม่ได้ตั้ง" : w.deadman_ok ? "ping ได้" : "ping ไม่ได้"}</Row>
        {Object.entries(w.streams).map(([name, s]) => (
          <Row key={name} label={STREAM_TH[name] ?? name} ok={s.connected}>
            {s.connected ? "เชื่อมอยู่" : "หลุด"} · ต่อแล้ว {s.connects} ครั้ง
            {s.silent_ms !== null && s.silent_ms !== undefined && ` · ข้อความล่าสุด ${Math.round(s.silent_ms / 1000)} วินาทีก่อนส่งสถานะ`}
            {!s.connected && s.error && <span className="text-[var(--text-muted)]"> · {s.error}</span>}
          </Row>
        ))}
        <Row label="แท่งราคาล่าสุด">1m {utc(w.bars.last_1m)} · 1d {utc(w.bars.last_1d)} · ช่องว่าง {w.bars.gaps ?? 0} · แก้ย้อนหลัง {w.bars.revisions ?? 0}</Row>
        <Row label="ตรวจบัญชีกับ REST" ok={r ? r.verdict === "MATCH" : null}>
          {r ? `${VERDICT_TH[r.verdict] ?? r.verdict} · ${ago(r.checked_at)} · order เปิด ${r.open_orders}` : "ยังไม่มี"}
        </Row>
        {r && r.differences.length > 0 && (
          <ul className="ml-40 list-disc pl-6 text-sm text-[var(--loss)]">{r.differences.slice(0, 5).map((d) => <li key={d}>{d}</li>)}</ul>
        )}
        <Row label="ยอดเงินทดสอบ">
          {w.balances.length === 0 ? "–" : w.balances.map((b) => `${b.asset} ${b.free}${b.locked !== "0" && Number(b.locked) !== 0 ? ` (ล็อก ${b.locked})` : ""}`).join(" · ")}
          {w.asset_count > w.balances.length && <span className="text-[var(--text-muted)]"> · รวม {w.asset_count} สินทรัพย์</span>}
        </Row>
        <Row label="รอบบัญชี (epoch)">{w.epoch ? `#${w.epoch.epoch} เริ่ม ${ago(w.epoch.opened_at)} · เทรด ${w.trades} รายการ` : "ยังไม่เปิด"}</Row>
        {w.execution && <ExecutionRows x={w.execution} />}
        {w.persist && (
          <Row label="บันทึกลง Supabase" ok={w.persist.ok}>
            {w.persist.ok === false ? "เขียนไม่ได้ กำลังลองใหม่" : "ปกติ"} · รอเขียน {w.persist.pending}
          </Row>
        )}
      </div>
    </article>
  );
}

export function WorkerTab() {
  const q = useQuery<WorkersResponse, ApiError>({ queryKey: ["workers"], queryFn: () => apiGet<WorkersResponse>("/api/v1/workers"), refetchInterval: 10_000 });
  if (q.isPending) return <p>กำลังโหลด…</p>;
  if (q.isError) return <p role="alert" className="text-sm text-[var(--loss)]">{q.error.message ?? "โหลดสถานะ worker ไม่สำเร็จ"}</p>;
  return (
    <div className="space-y-4">
      <p className="text-sm text-[var(--text-secondary)]">
        สถานะของ worker ที่รันกับ Binance Spot Testnet (เงินทดสอบ) หน้านี้อ่านอย่างเดียว ปุ่มหยุด/เริ่มบอทจะมากับ P5
      </p>
      {!q.data.available ? (
        <p data-testid="workers-unavailable">ยังไม่มีตารางสถานะ worker ในฐานข้อมูล (รอ apply migration ของ P3)</p>
      ) : q.data.workers.length === 0 ? (
        <p>ยังไม่มี worker ส่งสถานะมา</p>
      ) : (
        q.data.workers.map((w) => <WorkerCard key={w.account} w={w} />)
      )}
    </div>
  );
}
