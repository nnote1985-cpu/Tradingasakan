import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { AiTab } from "./AiTab";
import { AssetTab } from "./AssetTab";
import { type ApiError, apiGet, type Overview, type RiskStatusResponse, supabase } from "./api";
import { BookGroupCard } from "./BookGroupCard";
import { FuturesTab } from "./FuturesTab";
import { groupBooks, isFutures } from "./books";
import { Operations } from "./Operations";
import { PortfolioTable } from "./PortfolioTable";
import { ShadowBanner } from "./ShadowBanner";
import { StrategiesTab } from "./StrategiesTab";
import { NotOpen, sectionOf, TabBar, useHashTab, type ViewKey } from "./tabs";
import { WorkerTab } from "./WorkerTab";

type Health = { status: string; mode: string; live_enabled: boolean };

function SignIn() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  if (!supabase) return <p>ยังไม่ได้ตั้งค่า Supabase (VITE_SUPABASE_URL)</p>;
  return sent ? (
    <p>ส่งลิงก์เข้าสู่ระบบไปที่อีเมลแล้ว</p>
  ) : (
    <form
      className="flex gap-2"
      onSubmit={async (e) => {
        e.preventDefault();
        await supabase!.auth.signInWithOtp({ email });
        setSent(true);
      }}
    >
      <input className="rounded border border-[var(--border)] bg-[var(--surface-1)] px-2 py-1" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="อีเมล" />
      <button className="rounded bg-slate-900 px-3 py-1 text-white" type="submit">เข้าสู่ระบบ</button>
    </form>
  );
}

function SpotTab({ data }: { data: Overview }) {
  const risk = useQuery<RiskStatusResponse, ApiError>({ queryKey: ["risk-status"], queryFn: () => apiGet<RiskStatusResponse>("/api/v1/risk-status"), refetchInterval: 30_000 });
  const [showTable, setShowTable] = useState(false);
  const spot = data.portfolios.filter((r) => !isFutures(r.label));
  const groups = groupBooks(spot);
  const populated = groups.filter((g) => g.rows.some((r) => r.economic_equity !== null));
  const empty = groups.filter((g) => !g.rows.some((r) => r.economic_equity !== null));
  return (
    <div className="space-y-4">
      <p className="text-sm text-[var(--text-secondary)]">
        BTC spot ทุกพอร์ตเริ่มที่ 10,000 USDT ด้วยเงินสมมติ ราคาปิดรายวันตามเวลา UTC ตัวเลขเป็นมูลค่าหลังหักค่าธรรมเนียมและค่า AI
      </p>
      {risk.data && !risk.data.available && (
        <p className="text-xs text-[var(--text-muted)]">ยังไม่มีข้อมูลสถานะ HALT ในฐานข้อมูล</p>
      )}
      {groups.length === 0 ? <p>ยังไม่มีพอร์ต</p> : populated.map((g) => <BookGroupCard key={g.version} group={g} risk={risk.data?.portfolios} />)}
      {empty.length > 0 && <details className="panel"><summary className="cursor-pointer font-medium">ยังไม่เริ่ม / ยังไม่มีข้อมูล · {empty.length} พอร์ตทดลอง</summary><p className="mt-2 text-sm text-[var(--text-muted)]">ยังไม่มีมูลค่าพอร์ตสำหรับเปรียบเทียบ</p><div className="mt-4 space-y-4">{empty.map((g) => <BookGroupCard key={g.version} group={g} risk={risk.data?.portfolios} />)}</div></details>}
      {groups.length > 0 && (
        <div>
          <button className="text-sm text-[var(--text-secondary)] underline" aria-expanded={showTable} onClick={() => setShowTable((s) => !s)}>
            {showTable ? "ซ่อนตาราง" : "ดูเป็นตาราง"}
          </button>
          {showTable && <div className="mt-2 overflow-x-auto"><PortfolioTable rows={spot} /></div>}
        </div>
      )}
    </div>
  );
}

export function Dashboard() {
  // DEFAULT transport is REST polling (§21.3); paused automatically when the tab is hidden.
  const q = useQuery<Overview, ApiError>({ queryKey: ["overview"], queryFn: () => apiGet<Overview>("/api/v1/overview"), refetchInterval: 10_000, retry: false, networkMode: "always" });
  const [tab, setTab] = useHashTab();
  const [menuOpen, setMenuOpen] = useState(false);
  const contentRef = useRef<HTMLDivElement>(null);
  useEffect(() => { if (contentRef.current) contentRef.current.scrollTop = 0; }, [tab]);
  const section = sectionOf(tab);
  const researchViews: { key: ViewKey; label: string }[] = [{ key: "spot", label: "พอร์ตจำลอง Spot" }, { key: "futures", label: "Futures / Backtest" }, { key: "ai", label: "ผลทดลอง AI" }];
  const researchView = tab === "research" ? "spot" : tab;
  const titles = { overview: ["ภาพรวมการทำงาน", "ติดตามบอต บัญชี และสิ่งที่ต้องจัดการในที่เดียว"], bots: ["บอตและพอร์ต", "บัญชีที่เชื่อมกับ Exchange และข้อมูลตลาดที่ใช้ติดตาม"], research: ["งานวิจัยและผลทดลอง", "แยกผลจำลองและผลย้อนหลังออกจากบัญชีที่กำลังซื้อขาย"], system: ["สถานะระบบ", "ตรวจการเชื่อมต่อ บันทึกข้อมูล และรายละเอียดเครื่องที่รันบอต"], settings: ["ตั้งค่าบอตและกลยุทธ์", "ดูชุดกลยุทธ์ใน Bot profiles และวิธีเทรดที่มีในคลัง"] };
  const needsOverview = (section === "research" && tab !== "options") || tab === "asset";
  return (
    <section className="dashboard-shell">
      <aside className="dashboard-sidebar" aria-label="แถบเมนูหลัก" data-open={menuOpen}>
        <div className="sidebar-brand">
          <div><p className="font-semibold tracking-wide">TRADINGASAKAN</p><p className="eyebrow mt-1">CONTROL ROOM</p></div>
          <button type="button" className="action sidebar-toggle" aria-expanded={menuOpen} aria-controls="sidebar-menu" onClick={() => setMenuOpen((open) => !open)}>{menuOpen ? "ปิดเมนู" : "เมนู"}</button>
        </div>
        <div id="sidebar-menu" className="sidebar-menu">
          <nav aria-label="เมนูหลัก"><TabBar tab={section} onSelect={(next) => { setTab(next); setMenuOpen(false); }} /></nav>
          <div className="sidebar-account">
            <p className="eyebrow mb-3">บัญชีผู้ใช้งาน</p>
            <button className="action w-full text-left text-sm" onClick={() => supabase?.auth.signOut()}>ออกจากระบบ</button>
          </div>
        </div>
      </aside>
      <div className="dashboard-workspace">
      <header className="dashboard-header">
        <div><h1 className="text-2xl font-semibold sm:text-3xl">{titles[section][0]}</h1><p className="mt-2 text-sm text-[var(--text-secondary)]">{titles[section][1]}</p></div>
      </header>
      <div ref={contentRef} role="tabpanel" tabIndex={0} id={`panel-${section}`} aria-labelledby={`tab-${section}`} className="dashboard-content space-y-5">
        {section === "research" && <><div className="notice">พื้นที่ทดลอง · เงินสมมติและผลย้อนหลัง ไม่รวมอยู่ในยอดบัญชีของบอต</div><div className="flex flex-wrap gap-2" role="group" aria-label="หมวดงานวิจัย">{researchViews.map((v) => <button key={v.key} className={`filter-button ${researchView === v.key ? "filter-active" : ""}`} aria-pressed={researchView === v.key} onClick={() => setTab(v.key)}>{v.label}</button>)}</div></>}
        {section === "bots" && <div className="flex gap-2" role="group" aria-label="หมวดบอตและพอร์ต"><button className={`filter-button ${tab !== "asset" ? "filter-active" : ""}`} aria-pressed={tab !== "asset"} onClick={() => setTab("bots")}>บัญชีและบอต</button><button className={`filter-button ${tab === "asset" ? "filter-active" : ""}`} aria-pressed={tab === "asset"} onClick={() => setTab("asset")}>ตลาด BTC</button></div>}
        {section === "settings" ? <StrategiesTab view={tab === "strategies" ? "library" : "profiles"} onSelect={(view) => setTab(view === "library" ? "strategies" : "settings")} /> : section === "overview" ? <Operations overview /> : section === "system" ? <WorkerTab /> : section === "bots" && tab !== "asset" ? <Operations /> : needsOverview && q.isPending ? (
          <p>กำลังโหลด…</p>
        ) : needsOverview && q.isError ? (
          <div role="alert"><p>{q.error.message ?? "โหลดข้อมูลไม่สำเร็จ"}</p><button className="mt-2 rounded border px-3 py-1" onClick={() => void q.refetch()}>ลองใหม่</button>{["invalid_token", "missing_token"].includes(q.error.code) && <button className="ml-2 rounded border px-3 py-1" onClick={() => void supabase?.auth.signOut({ scope: "local" })}>เข้าสู่ระบบใหม่</button>}</div>
        ) : tab === "asset" ? <AssetTab rows={q.data?.portfolios ?? []} /> : researchView === "futures" ? <FuturesTab rows={q.data?.portfolios ?? []} /> : researchView === "ai" ? <AiTab rows={q.data?.portfolios ?? []} /> : tab === "options" ? <NotOpen title="Options ยังไม่เปิด">ยังไม่มีการทดลอง Options ที่เปิดใช้งาน</NotOpen> : q.data ? (
          <SpotTab data={q.data} />
        ) : null}
      </div>
      </div>
    </section>
  );
}

export function App() {
  const [signedIn, setSignedIn] = useState<boolean | null>(null);
  // /health is public, so the mode banner shows even before sign-in.
  const health = useQuery<Health, ApiError>({ queryKey: ["health"], queryFn: () => apiGet<Health>("/health"), refetchInterval: 60_000 });
  useEffect(() => {
    if (!supabase) return setSignedIn(false);
    supabase.auth.getSession().then(({ data }) => setSignedIn(!!data.session));
    const { data } = supabase.auth.onAuthStateChange((_e, s) => setSignedIn(!!s));
    return () => data.subscription.unsubscribe();
  }, []);
  return (
    <div className="app-shell">
      {health.data && <ShadowBanner mode={health.data.mode} liveEnabled={health.data.live_enabled} />}
      <main className={`app-main ${signedIn ? "app-main-dashboard" : "mx-auto w-full max-w-6xl px-4 py-6 sm:px-6 sm:py-9"}`}>{signedIn === null ? null : signedIn ? <Dashboard /> : <SignIn />}</main>
    </div>
  );
}
