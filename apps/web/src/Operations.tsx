import { useQuery } from "@tanstack/react-query";
import { apiGet, type ApiError, type WorkerActivity, type WorkerStatus, type WorkersResponse } from "./api";
import { money } from "./format";

export function useWorkers() {
  return useQuery<WorkersResponse, ApiError>({ queryKey: ["workers"], queryFn: () => apiGet("/api/v1/workers"), refetchInterval: 10_000 });
}

export function relativeTime(iso: string | null) {
  if (!iso || !Number.isFinite(Date.parse(iso))) return "ยังไม่มีเวลาอัปเดต";
  const seconds = Math.max(0, Math.floor((Date.now() - Date.parse(iso)) / 1000));
  return seconds < 60 ? `${seconds} วินาทีที่แล้ว` : seconds < 3600 ? `${Math.floor(seconds / 60)} นาทีที่แล้ว` : `${Math.floor(seconds / 3600)} ชั่วโมงที่แล้ว`;
}

export function workerIssues(w: WorkerStatus): string[] {
  const issues: string[] = [];
  if (w.silent) issues.push("ขาดการติดต่อกับเครื่องที่รันบอต");
  if (w.safety !== "RUNNING") issues.push(({ RECOVERING: "กำลังตรวจสอบและกู้คืนสถานะ", SOFT_PAUSED: "พักการเพิ่มสถานะ", REDUCING: "กำลังลดความเสี่ยง", HALTING: "กำลังหยุดฉุกเฉิน", HALTED: "หยุดจากเงื่อนไขความเสี่ยง" } as Record<string, string>)[w.safety ?? ""] ?? "ยังไม่ทราบสถานะความปลอดภัย");
  if (w.execution_enabled !== true) issues.push("ยังไม่เปิดการส่งคำสั่ง");
  if (w.account_ok !== true) issues.push("กุญแจบัญชียังไม่ผ่านการตรวจสอบ");
  if (w.clock.signing_paused !== false) issues.push("ยังไม่พร้อมเซ็นคำสั่ง");
  if (w.quote_age_ms === null || w.quote_age_ms > 5000 || w.quote_age_ms < 0) issues.push("ราคาสำหรับส่งคำสั่งยังไม่พร้อมหรือเก่าเกิน 5 วินาที");
  const checked = w.reconcile?.checked_at ? Date.parse(w.reconcile.checked_at) : NaN;
  if (!w.reconcile || w.reconcile.verdict !== "MATCH") issues.push("บัญชียังไม่ยืนยันว่าตรงกับ Exchange");
  else if (!Number.isFinite(checked) || Date.now() - checked > 60_000 || checked > Date.now()) issues.push("ผลตรวจบัญชีเกิน 1 นาทีหรือยังยืนยันเวลาไม่ได้");
  if (w.persist?.ok !== true) issues.push(w.persist?.ok === false ? "บันทึกข้อมูลไม่ได้" : "ยังไม่ยืนยันการบันทึกข้อมูล");
  if (w.execution?.last_error) issues.push("ระบบส่งคำสั่งมีข้อผิดพลาด");
  if (w.execution?.unresolved.length) issues.push("มีคำสั่งที่ยังไม่ทราบผล");
  if (Object.values(w.streams).some((s) => !s.connected)) issues.push("มีการเชื่อมต่อข้อมูลที่หลุด");
  return issues;
}

function Stat({ label, value, note }: { label: string; value: string; note: string }) {
  return <div className="metric"><dt>{label}</dt><dd>{value}</dd><p>{note}</p></div>;
}

function AccountSnapshot({ w }: { w: WorkerStatus }) {
  const q = useQuery<WorkerActivity, ApiError>({ queryKey: ["worker-activity", w.account], queryFn: () => apiGet(`/api/v1/workers/${encodeURIComponent(w.account)}/activity`), refetchInterval: 15_000 });
  const p = q.data?.pnl;
  const btc = p?.balances.find((b) => b.asset === "BTC")?.now;
  const usdt = p?.balances.find((b) => b.asset === "USDT")?.now;
  const latest = q.data?.trades[0];
  return <div className="mt-5 space-y-4">
    <dl className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <Stat label="ยอด USDT" value={usdt == null ? "—" : money(usdt)} note="เฉพาะบัญชีนี้ · รวมยอดล็อก" />
      <Stat label="ถือ BTC" value={btc == null ? "—" : Number(btc).toLocaleString("en-US", { maximumFractionDigits: 8 })} note="BTC · เฉพาะบัญชีนี้" />
      <Stat label="เปลี่ยนแปลงตั้งแต่เริ่มรอบ" value={p?.equity_change_usdt == null ? "—" : `${money(p.equity_change_usdt)} USDT`} note="BTC + USDT · ไม่ใช่กำไรวันนี้" />
      <Stat label="คำสั่งเปิดอยู่" value={String(w.execution?.open_orders ?? w.reconcile?.open_orders ?? "—")} note={`ส่งวันนี้ ${w.execution?.orders_today ?? "—"} คำสั่ง`} />
    </dl>
    <p className="text-xs text-[var(--text-muted)]">ยอดจากข้อมูลบัญชีล่าสุด · ตรวจบัญชี {relativeTime(w.reconcile?.checked_at ?? null)} · ผลต่างยังไม่รวมการแปลงค่าธรรมเนียม BNB เป็น USDT</p>
    {q.isPending && <p className="text-sm">กำลังโหลดยอดบัญชี…</p>}
    {q.isError && <p role="alert" className="notice">โหลดยอดบัญชีไม่สำเร็จ · <button onClick={() => void q.refetch()}>ลองใหม่</button></p>}
    {q.data && !q.data.available && <p className="notice">ยังไม่มีข้อมูลกิจกรรมของบัญชีนี้</p>}
    <div className="rounded-xl bg-[var(--surface-0)] p-4">
      <h3 className="text-sm font-semibold">กิจกรรมล่าสุด</h3>
      <p className="mt-1 text-sm">{latest ? `${latest.side === "BUY" ? "ซื้อ" : "ขาย"} ${latest.qty} BTC ที่ ${money(latest.price)} USDT` : "ยังไม่มีรายการเทรดที่รายงาน"}</p>
      {latest && <p className="mt-1 text-xs text-[var(--text-muted)]">{relativeTime(latest.traded_at)} · {latest.book ?? "ไม่ทราบที่มา"}{latest.book === "tn-conformance-v1" && " · ทดสอบระบบ ไม่ใช่ผลกลยุทธ์"}</p>}
    </div>
  </div>;
}

export function BotCard({ w, snapshot = true }: { w: WorkerStatus; snapshot?: boolean }) {
  const issues = workerIssues(w);
  const conformance = !!w.execution?.conformance;
  return <article className="panel" data-testid="bot-summary">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><p className="eyebrow">{w.mode === "TESTNET" ? "BINANCE SPOT TESTNET · เงินทดสอบ" : `${w.mode ?? "ไม่ทราบโหมด"} · โปรดตรวจโหมดบัญชี`}</p>
        <h2 className="mt-1 text-xl font-semibold">{conformance ? "ทดสอบระบบส่งคำสั่ง" : "บัญชีซื้อขาย"}</h2>
        <p className="mt-1 text-sm text-[var(--text-secondary)]">บัญชี {w.account} · {conformance ? "Conformance · ไม่ใช่บอตกลยุทธ์" : "ยังไม่มีข้อมูลยืนยันกลยุทธ์ที่เชื่อมกับบัญชีนี้"}</p>
      </div>
      <span className={`status-chip ${issues.length ? "status-warn" : "status-ok"}`}>{w.silent ? "ขาดการติดต่อ" : issues.length ? "ต้องตรวจสอบ" : "สถานะล่าสุดผ่านการตรวจ"}</span>
    </div>
    <p className="mt-4 text-sm">{issues[0] ?? "ข้อมูลสถานะล่าสุดไม่พบข้อขัดข้องที่รายงาน"}</p>
    <p className="mt-1 text-xs text-[var(--text-muted)]">รับสถานะ {relativeTime(w.updated_at)} · {conformance ? "ทดสอบรอบละ 4 ชั่วโมง ไม่ใช่รอบตัดสินใจของกลยุทธ์" : "ยังไม่มีข้อมูลเวลาตัดสินใจครั้งถัดไป"}</p>
    <a className="mt-3 inline-block text-sm underline" href="#settings">ดู Bot profiles ในการตั้งค่า →</a>
    {snapshot && <AccountSnapshot w={w} />}
  </article>;
}

export function Operations({ overview = false }: { overview?: boolean }) {
  const q = useWorkers();
  if (q.isPending) return <div className="panel" role="status">กำลังตรวจสถานะบอต…</div>;
  if (q.isError) return <div className="panel" role="alert"><h2 className="font-semibold">ยังตรวจสถานะบอตไม่ได้</h2><p className="mt-2 text-sm">{q.error.message}</p><button className="action mt-4" onClick={() => void q.refetch()}>ลองเชื่อมต่อใหม่</button></div>;
  const workers = q.data.workers;
  const alerts = workers.flatMap((w) => workerIssues(w).map((issue) => ({ account: w.account, issue })));
  return <div className="space-y-5">
    {overview && <section className={`panel ${alerts.length ? "attention-panel" : ""}`}>
      <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="font-semibold">สิ่งที่ต้องจัดการ</h2><a className="text-sm underline" href="#system">ดูรายละเอียดระบบ →</a></div>
      {!q.data.available || !workers.length ? <p className="mt-3 text-sm text-[var(--text-secondary)]">ยังไม่มีสถานะจากบอต จึงยังสรุปความพร้อมของระบบไม่ได้</p> : alerts.length ? <ul className="mt-3 space-y-2 text-sm">{alerts.slice(0, 4).map((a) => <li key={`${a.account}:${a.issue}`}><span className="font-medium">{a.account}</span> · {a.issue}</li>)}{alerts.length > 4 && <li>มีอีก {alerts.length - 4} รายการ ดูต่อในหน้าระบบ</li>}</ul> : <p className="mt-3 text-sm">ไม่พบเหตุที่ต้องจัดการจากสถานะล่าสุด · ตรวจสอบเป็นระยะทุก 10 วินาที</p>}
    </section>}
    <div className="flex items-baseline justify-between gap-3"><h2 className="text-lg font-semibold">บัญชีและบอตที่รายงานสถานะ</h2><span className="text-sm text-[var(--text-muted)]">{workers.length} บัญชี</span></div>
    {!q.data.available || !workers.length ? <div className="panel"><h3 className="font-semibold">ยังไม่มีบอตรายงานสถานะ</h3><p className="mt-2 text-sm text-[var(--text-secondary)]">พอร์ตจำลองและผลย้อนหลังอยู่ในงานวิจัย ยอดเหล่านั้นไม่ใช่ยอดบัญชี Exchange</p><a className="action mt-4 inline-block" href="#research">ดูงานวิจัย</a></div> : workers.map((w) => <BotCard key={w.account} w={w} />)}
    {overview && <div className="grid gap-4 sm:grid-cols-2"><a className="panel shortcut" href="#research"><h2 className="font-semibold">ผลทดลองและ Backtest →</h2><p className="mt-2 text-sm text-[var(--text-secondary)]">พอร์ตจำลอง · Backtest · เปรียบเทียบ Code / AI</p></a><a className="panel shortcut" href="#asset"><h2 className="font-semibold">ติดตามตลาด BTC →</h2><p className="mt-2 text-sm text-[var(--text-secondary)]">ราคาและแนวโน้ม · แยกจากข้อมูลสถานะของบอต</p></a></div>}
  </div>;
}
