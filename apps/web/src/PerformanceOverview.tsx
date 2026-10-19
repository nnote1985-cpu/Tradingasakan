import { useQueries, useQuery } from "@tanstack/react-query";
import { Fragment, useState, type ReactNode } from "react";
import { apiGet, type ApiError, type Overview, type WorkerActivity, type WorkerStatus } from "./api";
import { armOf, groupBooks, isFutures } from "./books";
import { BookGroupCard } from "./BookGroupCard";
import { accountEquity, btcValue, difference, isStrategyBook, pnlTotals, sumAmounts, uniqueAccounts } from "./dashboardMetrics";
import { money, pct } from "./format";
import { BotCard, relativeTime, useWorkers, workerIssues } from "./Operations";

type Mode = "LIVE" | "TESTNET" | "SHADOW";
const MODES: { key: Mode; label: string; caption: string }[] = [
  { key: "LIVE", label: "เงินจริง", caption: "บัญชีเงินจริง" },
  { key: "TESTNET", label: "Testnet", caption: "เงินทดสอบบน Exchange" },
  { key: "SHADOW", label: "พอร์ตจำลอง", caption: "เงินสมมติ · ผลทดลองแต่ละพอร์ต" },
];

function SignedAmount({ value, percent = false }: { value: string | null; percent?: boolean }) {
  const sign = Number(value);
  return <span className={value === null || sign === 0 ? "" : sign > 0 ? "text-[var(--gain)]" : "text-[var(--loss)]"}>{percent ? pct(value) : value === null ? "—" : `${sign > 0 ? "+" : ""}${money(value)}`}</span>;
}
function Metric({ label, children, note, primary = false }: { label: string; children: ReactNode; note: string; primary?: boolean }) {
  return <div className={`performance-metric ${primary ? "performance-metric-primary" : ""}`}><dt>{label}</dt><dd>{children}</dd><p>{note}</p></div>;
}
function Summary({ equity, values, scope }: { equity: string | null; values: (string | null)[]; scope: string }) {
  const totals = pnlTotals(values);
  return <dl className="performance-metrics">
    <Metric label="มูลค่ารวม · USDT" note={scope} primary>{equity === null ? "—" : money(equity)}</Metric>
    <Metric label="กำไรรวม · USDT" note="รวมเฉพาะพอร์ตที่เป็นบวก"><SignedAmount value={totals.gains} /></Metric>
    <Metric label="ขาดทุนรวม · USDT" note="รวมเฉพาะพอร์ตที่เป็นลบ"><SignedAmount value={totals.losses} /></Metric>
    <Metric label="กำไร/ขาดทุนสุทธิ · USDT" note="กำไรรวม + ขาดทุนรวม"><SignedAmount value={totals.net} /></Metric>
  </dl>;
}
function Empty({ children }: { children: ReactNode }) {
  return <div className="panel py-10 text-center text-[var(--text-secondary)]">{children}</div>;
}

function AccountPerformance({ workers }: { workers: WorkerStatus[] }) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const requests = useQueries({ queries: workers.map((w) => ({ queryKey: ["worker-activity", w.account], queryFn: () => apiGet<WorkerActivity>(`/api/v1/workers/${encodeURIComponent(w.account)}/activity`), refetchInterval: 15_000, retry: false })) });
  const accounts = workers.map((w, i) => ({ w, query: requests[i], pnl: requests[i].isError || !requests[i].data?.available ? null : requests[i].data?.pnl }));
  const books = accounts.flatMap(({ w, pnl }) => (pnl?.books ?? []).filter((b) => isStrategyBook(b.book)).map((b) => ({ ...b, w })));
  const ready = accounts.every(({ pnl }) => pnl != null);
  const values = ready ? books.map((b) => b.pnl_usdt) : [null];
  const balancesOnly = accounts.length > 0 && ready && books.length === 0;
  const equity = sumAmounts(accounts.map(({ pnl }) => accountEquity(pnl)));
  const issues = workers.flatMap((w) => workerIssues(w).map((issue) => ({ account: w.account, issue })));
  return <>
    {balancesOnly ? <>
      <dl className="performance-metrics">
        <Metric label="มูลค่ารวม · USDT" note={`BTC + USDT · ${workers.length} บัญชี`} primary>{money(equity)}</Metric>
        <Metric label="เงินสด USDT" note="ยอดเงินสด รวมยอดที่ล็อกไว้">{money(sumAmounts(accounts.map(({ pnl }) => pnl?.balances.find((b) => b.asset === "USDT")?.now ?? null)))}</Metric>
        <Metric label="มูลค่า BTC · USDT" note="BTC ที่ถือทั้งหมดคิดตามราคาประเมิน">{money(sumAmounts(accounts.map(({ pnl }) => btcValue(pnl))))}</Metric>
        <Metric label="ยอดเปลี่ยนตั้งแต่เริ่มรอบ · USDT" note="รวมเงินเข้าออกและรายการนอกบอต"><SignedAmount value={sumAmounts(accounts.map(({ pnl }) => pnl?.equity_change_usdt ?? null))} /></Metric>
      </dl>
      <p className="performance-explanation">ยังไม่มีผลบอตกลยุทธ์ในบัญชีกลุ่มนี้ จึงแสดงยอดสินทรัพย์ก่อน · มูลค่ารวมรวม BTC ที่มีอยู่เดิมด้วย และยอดเปลี่ยนยังไม่ใช่กำไรของกลยุทธ์</p>
    </> : <>
      <Summary equity={equity} values={values} scope={`BTC + USDT · ${workers.length} บัญชี ไม่รวมยอดซ้ำ`} />
      <p className="text-xs text-[var(--text-muted)]">ผลบอตตั้งแต่เริ่มรอบบัญชีของแต่ละบัญชี · รวมรายการที่ปิดและ BTC สุทธิที่เหลือตามราคาประเมิน · ไม่รวมการทดสอบระบบและรายการนอกบอต · ค่าธรรมเนียม BNB ยังไม่แปลงเป็น USDT</p>
    </>}
    <section className="panel space-y-4">
      <div className="performance-section-heading"><div><h2 className="text-lg font-semibold">เงินในบัญชี</h2><p className="text-sm text-[var(--text-secondary)]">มูลค่าสินทรัพย์ของแต่ละบัญชี แยกจากผลซื้อขายของบอต</p></div><span className="performance-count">{workers.length} บัญชี</span></div>
      {!workers.length ? <p className="text-sm">ยังไม่มีบอตรายงานสถานะ</p> : <div className="account-grid">{accounts.map(({ w, pnl, query }) => <div key={w.account} className="account-tile">
        <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-semibold">{w.account}</h3><span className={`status-chip ${workerIssues(w).length ? "status-warn" : "status-ok"}`}>{w.silent ? "ขาดการติดต่อ" : workerIssues(w).length ? "ต้องตรวจสอบ" : "ทำงาน"}</span></div>
        <p className="mt-3 text-2xl font-semibold">{accountEquity(pnl) === null ? "—" : money(accountEquity(pnl))}<span className="ml-2 text-sm font-normal text-[var(--text-muted)]">USDT</span></p>
        <p className="mt-1 text-xs text-[var(--text-secondary)]">BTC + USDT · รอบ #{pnl?.epoch ?? "—"} · รับสถานะ {relativeTime(w.updated_at)}</p>
        {pnl && <p className="mt-2 text-sm">ยอดเปลี่ยนตั้งแต่เริ่มรอบ <SignedAmount value={pnl.equity_change_usdt} /> USDT</p>}
        <p className="mt-1 text-xs text-[var(--text-muted)]">ยอดเปลี่ยนอาจรวมเงินเข้าออกและรายการนอกบอต</p>
        {pnl?.mark && <p className="mt-1 text-xs text-[var(--text-muted)]">BTC @ {money(pnl.mark)} · {pnl.mark_source === "quote" ? "ราคา bid ที่รายงาน" : "ราคาเทรดล่าสุด"}</p>}
        {query.isPending && <p className="mt-2 text-sm" role="status">กำลังโหลดยอดบัญชี…</p>}
        {query.isError && <p className="mt-2 text-sm" role="alert">โหลดยอดไม่ได้ <button className="underline" onClick={() => void query.refetch()}>ลองใหม่</button></p>}
        {!query.isPending && !query.isError && !pnl && <p className="mt-2 text-sm">ยังไม่มีข้อมูลรอบบัญชี</p>}
        <button className="mt-3 text-sm underline" aria-expanded={expanded === w.account} aria-controls={`account-detail-${w.account}`} onClick={() => setExpanded(expanded === w.account ? null : w.account)}>{expanded === w.account ? "ซ่อนรายละเอียด" : "ดูรายละเอียดบัญชี →"}</button>
      </div>)}</div>}
      {expanded && workers.find((w) => w.account === expanded) && <div id={`account-detail-${expanded}`}><BotCard w={workers.find((w) => w.account === expanded)!} /></div>}
    </section>
    <section className="panel space-y-4">
      <div className="performance-section-heading"><div><h2 className="text-lg font-semibold">ผลรายพอร์ต / บอต</h2><p className="text-sm text-[var(--text-secondary)]">ดูว่ากำไรหรือขาดทุนมาจากบอตไหน</p></div><span className="performance-count">{books.length} พอร์ต</span></div>
      {!books.length ? <p className="text-sm text-[var(--text-secondary)]">{requests.some((q) => q.isPending) ? "กำลังโหลดผลบอต…" : "ยังไม่มีผลจากบอตกลยุทธ์ · รายการทดสอบระบบแสดงในรายละเอียดบัญชี"}</p> : <div className="overflow-x-auto"><table className="performance-table"><thead><tr><th>พอร์ต / บอต</th><th>บัญชี</th><th className="numeric">กำไร/ขาดทุน · USDT</th><th className="numeric">ซื้อ / ขาย</th><th className="numeric">ค่าธรรมเนียม BNB</th><th>สถานะ</th></tr></thead><tbody>{books.map((b) => <tr key={`${b.w.account}/${b.book}`}><td className="font-medium">{b.book}</td><td>{b.w.account}</td><td className="numeric font-semibold"><SignedAmount value={b.pnl_usdt} /></td><td className="numeric">{b.buys} / {b.sells}</td><td className="numeric">{b.bnb_fees}</td><td>{b.w.silent ? "ขาดการติดต่อ" : "มีผลซื้อขาย"}</td></tr>)}</tbody></table></div>}
    </section>
    {issues.length > 0 && <details className="performance-alert"><summary>มี {issues.length} รายการที่ต้องตรวจสอบ <span className="ml-2 text-xs">ดูรายละเอียด</span></summary><ul className="mt-3 space-y-2 text-sm">{issues.map((item) => <li key={`${item.account}/${item.issue}`}>{item.account} · {item.issue}</li>)}</ul><a href="#system" className="mt-3 inline-block text-sm underline">ไปหน้าสถานะระบบ →</a></details>}
  </>;
}

function SimulatedPerformance() {
  const q = useQuery<Overview, ApiError>({ queryKey: ["overview"], queryFn: () => apiGet("/api/v1/overview"), refetchInterval: 10_000, retry: false });
  const [selected, setSelected] = useState<string | null>(null);
  const [benchmarks, setBenchmarks] = useState(false);
  const [market, setMarket] = useState<"spot" | "futures">("spot");
  if (q.isPending) return <Empty>กำลังโหลดพอร์ตจำลอง…</Empty>;
  if (q.isError) return <Empty>โหลดพอร์ตจำลองไม่ได้ <button className="underline" onClick={() => void q.refetch()}>ลองใหม่</button></Empty>;
  const simulated = q.data.portfolios.filter((p) => p.mode === "SHADOW" && p.currency === "USDT");
  const portfolios = simulated.filter((p) => isFutures(p.label) === (market === "futures"));
  const marketLabel = market === "spot" ? "Spot" : "Futures";
  const strategies = portfolios.filter((p) => !armOf(p.label).benchmark);
  const rows = portfolios.filter((p) => benchmarks || !armOf(p.label).benchmark);
  const group = selected ? groupBooks(portfolios).find((g) => g.rows.some((r) => r.label === selected)) : null;
  return <>
    <div className="performance-toolbar">
      <div className="flex flex-wrap gap-2" role="group" aria-label="ตลาดของพอร์ตจำลอง">
        {(["spot", "futures"] as const).map((key) => <button type="button" key={key} className={`filter-button ${market === key ? "filter-active" : ""}`} aria-pressed={market === key} onClick={() => { setMarket(key); setSelected(null); }}>{key === "spot" ? "Spot" : "Futures"}{" "}<span className="ml-2 text-xs">{simulated.filter((p) => isFutures(p.label) === (key === "futures") && !armOf(p.label).benchmark).length} พอร์ต</span></button>)}
      </div>
      <p className="text-sm text-[var(--text-secondary)]">สรุปเฉพาะ {marketLabel} · แยกเงินจำลองจากอีกตลาด</p>
    </div>
    <Summary equity={sumAmounts(strategies.map((p) => p.economic_equity))} values={strategies.map((p) => difference(p.economic_equity, p.initial_equity))} scope={`เงินสมมติ ${marketLabel} · ${strategies.length} พอร์ตกลยุทธ์ ไม่รวมตัวเปรียบเทียบ`} />
    <p className="text-xs text-[var(--text-muted)]">ตั้งแต่เริ่มแต่ละพอร์ต · กำไร/ขาดทุน = มูลค่าหลังค่าใช้จ่าย − เงินตั้งต้น · อิงราคาปิดรายวัน UTC · <a href={market === "futures" ? "#futures" : "#spot"} className="underline">{market === "futures" ? "ดูงานวิจัย Futures / Backtest →" : "ดูงานวิจัย Spot →"}</a> · ผล Backtest ไม่รวมอยู่ในยอดนี้</p>
    {strategies.some((p) => p.stale) && <p className="notice">บางพอร์ตเป็นข้อมูลเก่า ยอดรวมรวมข้อมูลล่าสุดที่มีของแต่ละพอร์ต</p>}
    <section className="panel space-y-4">
      <div className="performance-section-heading"><div><h2 className="text-lg font-semibold">เปรียบเทียบพอร์ตจำลอง {marketLabel}</h2><p className="text-sm text-[var(--text-secondary)]">เงินตั้งต้นและระยะเวลาของแต่ละพอร์ตอาจต่างกัน</p></div><label className="flex min-h-11 cursor-pointer items-center gap-2 text-sm"><input type="checkbox" checked={benchmarks} onChange={(e) => setBenchmarks(e.target.checked)} />แสดงตัวเปรียบเทียบ</label></div>
      {!rows.length ? <p>ยังไม่มีพอร์ตจำลอง</p> : <div className="overflow-x-auto"><table className="performance-table"><thead><tr><th>พอร์ต</th><th className="numeric">มูลค่า · USDT</th><th className="numeric">กำไร/ขาดทุน · USDT</th><th className="numeric">ผลตอบแทน</th><th className="numeric">ลดจากจุดสูงสุด</th><th>ข้อมูลล่าสุด</th><th><span className="sr-only">รายละเอียด</span></th></tr></thead><tbody>{rows.map((p) => <Fragment key={p.label}><tr><td><p className="font-medium">{p.label}</p><p className="text-xs text-[var(--text-muted)]">{armOf(p.label).name}{armOf(p.label).benchmark ? " · ตัวเปรียบเทียบ" : ""}</p></td><td className="numeric font-semibold">{money(p.economic_equity)}</td><td className="numeric font-semibold"><SignedAmount value={difference(p.economic_equity, p.initial_equity)} /></td><td className="numeric"><SignedAmount value={p.return} percent /></td><td className="numeric">{pct(p.drawdown, true)}</td><td>{p.as_of?.slice(0, 10) ?? "ยังไม่มีข้อมูล"}{p.stale && <span className="ml-2 text-[var(--warn-fg)]">ข้อมูลเก่า</span>}</td><td><button className="filter-button whitespace-nowrap" aria-expanded={selected === p.label} aria-label={`ดูรายละเอียด ${p.label}`} onClick={() => setSelected(selected === p.label ? null : p.label)}>{selected === p.label ? "ซ่อน" : "ดูพอร์ต →"}</button></td></tr>{selected === p.label && group && <tr><td colSpan={7}><BookGroupCard group={group} /></td></tr>}</Fragment>)}</tbody></table></div>}
    </section>
  </>;
}

export function PerformanceOverview() {
  const q = useWorkers();
  const [selection, setSelection] = useState<Mode | null>(null);
  const all = uniqueAccounts(q.data?.workers ?? []);
  const mode = selection ?? (all.some((w) => w.mode === "LIVE") ? "LIVE" : "TESTNET");
  const workers = all.filter((w) => w.mode === mode);
  return <div className="space-y-5">
    <div className="performance-toolbar"><div className="performance-mode-switch" role="group" aria-label="ประเภทเงิน">{MODES.map((item) => <button type="button" key={item.key} aria-pressed={mode === item.key} className={mode === item.key ? "is-selected" : ""} onClick={() => setSelection(item.key)}>{item.label}</button>)}</div><p className="text-sm text-[var(--text-secondary)]">{MODES.find((item) => item.key === mode)?.caption} <span className="mx-2">·</span> ตั้งแต่เริ่มรอบ</p></div>
    {q.isPending && selection === null ? <Empty>กำลังตรวจสถานะบอต…</Empty> : mode === "SHADOW" ? <SimulatedPerformance /> : q.isError ? <Empty>โหลดบัญชีไม่ได้ <button className="underline" onClick={() => void q.refetch()}>ลองใหม่</button></Empty> : <AccountPerformance key={mode} workers={workers} />}
    {q.isError && mode === "SHADOW" && <p role="alert" className="notice">ยังตรวจสถานะบัญชี Exchange ไม่ได้ <button onClick={() => void q.refetch()}>ลองใหม่</button></p>}
    <p className="text-xs text-[var(--text-muted)]">เครื่องหมาย — หมายถึงยังไม่มีข้อมูลครบสำหรับคำนวณ</p>
  </div>;
}
