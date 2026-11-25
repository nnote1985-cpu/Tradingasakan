import { useQueries, useQuery } from "@tanstack/react-query";
import { apiGet, type ApiError, type FuturesDesk, type PaperCompare, type WorkerActivity, type WorkerControl, type WorkerStatus } from "./api";
import type { BotList } from "./bots/types";
import { uniqueAccounts } from "./dashboardMetrics";
import { money, pct } from "./format";
import { useLiveInterval } from "./live/LiveProvider";
import { relativeTime, useWorkers, workerIssues } from "./Operations";

// Home (UX P1): answers "is anything wrong?" and "how is each bot doing?" before any detail.
// Every figure here comes from endpoints other pages already use; the detailed views stay below and on their own pages.

const CRITICAL_SAFETY = new Set(["SOFT_PAUSED", "REDUCING", "HALTING", "HALTED"]);
const FOCUS = { hedge: "pathA-r10a-hedge/paper", r10a: "pathA-r10a/paper" };

export type Alert = { level: "critical" | "warn"; where: string; text: string; href: string };

/** Critical = money may be at risk or the bot cannot act; everything else from workerIssues is a warning. */
export function workerAlerts(w: WorkerStatus): Alert[] {
  const critical = w.silent || CRITICAL_SAFETY.has(w.safety ?? "") || w.persist?.ok === false || !!w.execution?.unresolved.length || !!w.execution?.last_error;
  // The worker reconciles every few minutes, so a check under 15 minutes old is normal on the home page (the system page keeps the strict 1-minute rule).
  const checked = w.reconcile?.checked_at ? Date.parse(w.reconcile.checked_at) : NaN;
  const recent = Number.isFinite(checked) && Date.now() - checked <= 15 * 60_000;
  const issues = workerIssues(w).filter((text) => !(recent && text.startsWith("ผลตรวจบัญชีเกิน 1 นาที")));
  return issues.map((text, i) => ({ level: critical && i === 0 ? "critical" : "warn", where: w.account, text, href: "#system" }));
}

export function deskAlerts(d: FuturesDesk | undefined): Alert[] {
  if (!d || !d.available) return [];
  const out: Alert[] = [];
  for (const s of d.unprotected) out.push({ level: "critical", where: "Futures", text: `${s} ไม่มี Stop-loss บน exchange`, href: "#trade" });
  if (d.caps?.blocked) out.push({ level: "critical", where: "Futures", text: `ถึงเพดานขาดทุนแล้ว เปิดไม้ใหม่ไม่ได้${d.caps.reasons.length ? ` (${d.caps.reasons.join(", ")})` : ""}`, href: "#trade" });
  else if (d.caps && (d.caps.day.level === "NEAR" || d.caps.total.level === "NEAR")) out.push({ level: "warn", where: "Futures", text: "ขาดทุนใกล้เพดานที่ตั้งไว้", href: "#trade" });
  if (d.stale) out.push({ level: "warn", where: "Futures", text: "ข้อมูลบัญชี Futures ไม่อัปเดต", href: "#trade" });
  if (d.worker.last_error) out.push({ level: "warn", where: "Futures", text: "worker Futures มีข้อผิดพลาดล่าสุด", href: "#trade" });
  return out;
}

/** Readable bot name: the web bot's own name, else a short form of the book id. */
export function botName(book: string | null | undefined, bots: BotList | undefined): string {
  if (!book) return "ไม่มีบอทกลยุทธ์";
  const web = bots?.bots.find((b) => b.book === book);
  if (web) return web.name;
  const known: Record<string, string> = { "tn-r10a-v1.1": "R10a v1.1", "tn-r10a-code-v1": "R10a v1.1", "tn-r2-v1": "AI r2 (R10a + AI)", "tn-conformance-v1": "ทดสอบระบบส่งคำสั่ง" };
  return known[book] ?? book;
}

function Signed({ value, percent = false }: { value: string | null | undefined; percent?: boolean }) {
  if (value == null) return <span className="text-[var(--text-muted)]">—</span>;
  const n = Number(value);
  const cls = n > 0 ? "text-[var(--gain)]" : n < 0 ? "text-[var(--loss)]" : "";
  return <span className={cls}>{percent ? pct(value) : `${n > 0 ? "+" : ""}${money(value)}`}</span>;
}

function AlertStrip({ alerts, botCount, loading }: { alerts: Alert[]; botCount: number; loading: boolean }) {
  if (loading) return <div className="home-alert home-alert-muted" role="status">กำลังตรวจสถานะ…</div>;
  const critical = alerts.filter((a) => a.level === "critical");
  const warns = alerts.filter((a) => a.level === "warn");
  if (!alerts.length) return <div className="home-alert home-alert-ok" role="status" data-testid="home-alert"><span aria-hidden="true">●</span> ทุกอย่างปกติ · บอท {botCount} ตัวรายงานสถานะครบ</div>;
  return (
    <div className={`home-alert ${critical.length ? "home-alert-critical" : "home-alert-warn"}`} role="alert" data-testid="home-alert">
      <p className="font-semibold">{critical.length ? `ต้องดูด่วน ${critical.length} เรื่อง` : `มี ${warns.length} เรื่องที่ควรตรวจ`}</p>
      <ul className="mt-1 space-y-1 text-sm">
        {[...critical, ...warns].slice(0, 5).map((a) => <li key={`${a.where}/${a.text}`}><a className="underline-offset-2 hover:underline" href={a.href}><span className="font-medium">{a.where}</span> · {a.text}</a></li>)}
        {alerts.length > 5 && <li><a className="underline" href="#system">อีก {alerts.length - 5} เรื่อง ดูในหน้าระบบ →</a></li>}
      </ul>
    </div>
  );
}

function positionText(weight: string | null | undefined) {
  if (weight == null) return "—";
  const n = Number(weight);
  if (n <= 0) return "ถือเงินสด";
  return `ถือ BTC ${Math.round(n * 100)}%`;
}

function BotTable({ workers, bots }: { workers: WorkerStatus[]; bots: BotList | undefined }) {
  const every = useLiveInterval(15_000);
  const activity = useQueries({ queries: workers.map((w) => ({ queryKey: ["worker-activity", w.account], queryFn: () => apiGet<WorkerActivity>(`/api/v1/workers/${encodeURIComponent(w.account)}/activity`), refetchInterval: every, retry: false })) });
  const control = useQueries({ queries: workers.map((w) => ({ queryKey: ["worker-control", w.account], queryFn: () => apiGet<WorkerControl>(`/api/v1/workers/${encodeURIComponent(w.account)}/control`), refetchInterval: every, retry: false })) });
  if (!workers.length) return <p className="text-sm text-[var(--text-secondary)]">ยังไม่มีบอทรายงานสถานะ</p>;
  return (
    <div className="home-bots" data-testid="home-bots">
      {workers.map((w, i) => {
        const book = w.execution?.r10a?.book ?? w.execution?.conformance?.book ?? null;
        const pnl = activity[i].data?.pnl?.books.find((b) => b.book === book)?.pnl_usdt ?? null;
        const decision = control[i].data?.decisions?.find((d) => d.book === book) ?? control[i].data?.decisions?.[0];
        const issues = workerIssues(w);
        const status = w.silent ? ["ขาดการติดต่อ", "status-bad"] : CRITICAL_SAFETY.has(w.safety ?? "") ? [w.safety, "status-bad"] : issues.length ? ["ต้องตรวจ", "status-warn"] : ["ทำงาน", "status-ok"];
        const allocation = w.execution?.r10a?.allocation ?? null;
        return (
          <article key={w.account} className="home-bot">
            <div className="home-bot-name">
              <p className="font-semibold">{botName(book, bots)}</p>
              <p className="text-xs text-[var(--text-muted)]">บัญชี {w.account} · {w.mode === "TESTNET" ? "Testnet" : w.mode ?? "ไม่ทราบโหมด"}{allocation ? ` · ทุน ${money(allocation)} USDT` : ""}</p>
            </div>
            <div className="home-bot-cell"><span className="home-label">สถานะ</span><span className={`status-chip ${status[1]}`}>{status[0]}</span></div>
            <div className="home-bot-cell"><span className="home-label">กำไร/ขาดทุน · USDT</span><span className="text-lg font-semibold"><Signed value={pnl} /></span></div>
            <div className="home-bot-cell"><span className="home-label">ตอนนี้</span><span>{positionText(decision?.weight)}</span></div>
            <div className="home-bot-cell"><span className="home-label">ตัดสินใจล่าสุด</span><span>{decision ? `${decision.day} · ${decision.action === "HOLD" ? "คงเดิม" : decision.action === "REBALANCE" ? "ปรับสัดส่วน" : decision.action === "OPEN" ? "เปิด" : "ไม่ได้ตัดสินใจ"}` : "—"}</span></div>
            <div className="home-bot-cell"><span className="home-label">รายงานล่าสุด</span><span>{relativeTime(w.updated_at)}</span></div>
          </article>
        );
      })}
    </div>
  );
}

function PaperCard() {
  const q = useQuery<PaperCompare, ApiError>({ queryKey: ["paper-compare", null], queryFn: () => apiGet<PaperCompare>("/api/v1/paper-compare"), refetchInterval: 60_000 });
  const books = q.data?.books ?? [];
  const rows = [
    { label: "3a · R10a + hedge", book: books.find((b) => b.label === FOCUS.hedge) },
    { label: "R10a", book: books.find((b) => b.label === FOCUS.r10a) },
    { label: "ซื้อถือ BTC", book: books.find((b) => b.benchmark) },
  ];
  return (
    <a href="#compare" className="panel shortcut home-card" data-testid="home-paper">
      <p className="home-label">พอร์ตจำลอง (เงินสมมติ)</p>
      <h2 className="font-semibold">3a เทียบ R10a</h2>
      {q.isPending ? <p className="text-sm">กำลังโหลด…</p> : q.isError || !q.data?.from ? <p className="text-sm text-[var(--text-secondary)]">ยังไม่มีข้อมูล</p> : <>
        <dl className="mt-3 space-y-2">{rows.map((r) => <div key={r.label} className="flex items-baseline justify-between gap-3"><dt className="text-sm text-[var(--text-secondary)]">{r.label}</dt><dd className="font-semibold"><Signed value={r.book?.return ?? null} percent /></dd></div>)}</dl>
        <p className="mt-3 text-xs text-[var(--text-muted)]">ผลตอบแทนตั้งแต่ {q.data.from} · ปิดล่าสุด {rows[0].book?.as_of.slice(0, 10) ?? "—"}</p>
      </>}
      <p className="mt-3 text-sm">ดูกราฟเทียบ →</p>
    </a>
  );
}

function FuturesCard({ desk }: { desk: { data?: FuturesDesk; isPending: boolean; isError: boolean } }) {
  const d = desk.data;
  return (
    <a href="#trade" className="panel shortcut home-card" data-testid="home-futures">
      <p className="home-label">Futures (Demo)</p>
      <h2 className="font-semibold">โต๊ะเทรด Futures</h2>
      {desk.isPending ? <p className="text-sm">กำลังโหลด…</p> : desk.isError || !d || !d.available ? <p className="text-sm text-[var(--text-secondary)]">ยังไม่มีข้อมูลบัญชี Futures</p> : <dl className="mt-3 space-y-2">
        <div className="flex items-baseline justify-between gap-3"><dt className="text-sm text-[var(--text-secondary)]">เงินในบัญชี</dt><dd className="font-semibold">{money(d.equity)} USDT</dd></div>
        <div className="flex items-baseline justify-between gap-3"><dt className="text-sm text-[var(--text-secondary)]">กำไร/ขาดทุนที่ยังไม่ปิด</dt><dd className="font-semibold"><Signed value={d.unrealized_pnl} /></dd></div>
        <div className="flex items-baseline justify-between gap-3"><dt className="text-sm text-[var(--text-secondary)]">ปิดแล้ววันนี้</dt><dd className="font-semibold"><Signed value={d.summary.today_net} /></dd></div>
        <div className="flex items-baseline justify-between gap-3"><dt className="text-sm text-[var(--text-secondary)]">Position ที่เปิด</dt><dd className="font-semibold">{d.positions.length ? d.positions.map((p) => `${p.side} ${p.symbol}`).join(", ") : "ไม่มี"}</dd></div>
      </dl>}
      <p className="mt-3 text-sm">ไปหน้า Futures →</p>
    </a>
  );
}

export function HomeSummary() {
  const workersQ = useWorkers();
  const bots = useQuery<BotList, ApiError>({ queryKey: ["bots"], queryFn: () => apiGet("/api/v1/bots"), refetchInterval: 30_000, retry: false });
  const desk = useQuery<FuturesDesk, ApiError>({ queryKey: ["futures-desk", null], queryFn: () => apiGet<FuturesDesk>("/api/v1/futures/desk"), refetchInterval: 10_000, retry: false });
  const workers = uniqueAccounts(workersQ.data?.workers ?? []);
  const alerts = [...workers.flatMap(workerAlerts), ...deskAlerts(desk.data)];
  if (workersQ.isError) alerts.unshift({ level: "critical", where: "ระบบ", text: "ตรวจสถานะบอทไม่ได้", href: "#system" });
  return (
    <div className="space-y-5" data-testid="home-summary">
      <AlertStrip alerts={alerts} botCount={workers.length} loading={workersQ.isPending} />
      <section className="panel space-y-4">
        <div className="performance-section-heading"><div><h2 className="text-lg font-semibold">บอทที่กำลังรัน</h2><p className="text-sm text-[var(--text-secondary)]">หนึ่งแถวต่อบอท · กำไร/ขาดทุนนับเฉพาะการซื้อขายของบอทตั้งแต่เริ่มรอบบัญชี</p></div><a className="text-sm underline" href="#bot-config">ตั้งค่าบอท →</a></div>
        {workersQ.isPending ? <p className="text-sm" role="status">กำลังโหลด…</p> : <BotTable workers={workers} bots={bots.data} />}
      </section>
      <div className="home-cards">
        <PaperCard />
        <FuturesCard desk={desk} />
      </div>
    </div>
  );
}
