import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { AiTab } from "./AiTab";
import { AssetTab } from "./AssetTab";
import { type ApiError, apiGet, type Overview, type RiskStatusResponse, supabase } from "./api";
import { BookGroupCard } from "./BookGroupCard";
import { FuturesTab } from "./FuturesTab";
import { groupBooks, isFutures } from "./books";
import { ModeBadge } from "./ModeBadge";
import { PortfolioTable } from "./PortfolioTable";
import { ShadowBanner } from "./ShadowBanner";
import { StrategiesTab } from "./StrategiesTab";
import { NotOpen, TabBar, useHashTab } from "./tabs";

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
  return (
    <div className="space-y-4">
      <p className="text-sm text-[var(--text-secondary)]">
        BTC spot ทุกพอร์ตเริ่มที่ 10,000 USDT ด้วยเงินสมมติ ราคาปิดรายวันตามเวลา UTC ตัวเลขเป็นมูลค่าหลังหักค่าธรรมเนียมและค่า AI
      </p>
      {risk.data && !risk.data.available && (
        <p className="text-xs text-[var(--text-muted)]">ยังไม่มีข้อมูลสถานะ HALT ในฐานข้อมูล</p>
      )}
      {groups.length === 0 ? <p>ยังไม่มีพอร์ต</p> : groups.map((g) => <BookGroupCard key={g.version} group={g} risk={risk.data?.portfolios} />)}
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
  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-xl font-semibold">ภาพรวมพอร์ต</h1>
        {q.data && <ModeBadge mode={q.data.mode} />}
        <button className="ml-auto text-sm text-[var(--text-secondary)] underline" onClick={() => supabase?.auth.signOut()}>ออกจากระบบ</button>
      </div>
      <TabBar tab={tab} onSelect={setTab} />
      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
        {tab === "futures" ? (
          <FuturesTab rows={q.data?.portfolios ?? []} />
        ) : tab === "strategies" ? (
          <StrategiesTab />
        ) : tab === "options" ? (
          <NotOpen title="Options ยังไม่เปิด">จะเปิดเมื่อตัวจำลอง options ผ่านเทสต์ และจะเริ่มด้วยเงินสมมติเหมือนพอร์ตอื่น</NotOpen>
        ) : q.isPending ? (
          <p>กำลังโหลด…</p>
        ) : q.isError ? (
          <div role="alert"><p>{q.error.message ?? "โหลดข้อมูลไม่สำเร็จ"}</p><button className="mt-2 rounded border px-3 py-1" onClick={() => void q.refetch()}>ลองใหม่</button>{["invalid_token", "missing_token"].includes(q.error.code) && <button className="ml-2 rounded border px-3 py-1" onClick={() => void supabase?.auth.signOut({ scope: "local" })}>เข้าสู่ระบบใหม่</button>}</div>
        ) : tab === "asset" ? (
          <AssetTab rows={q.data.portfolios} />
        ) : tab === "ai" ? (
          <AiTab rows={q.data.portfolios} />
        ) : (
          <SpotTab data={q.data} />
        )}
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
    <>
      {health.data && <ShadowBanner mode={health.data.mode} liveEnabled={health.data.live_enabled} />}
      <main className="mx-auto max-w-5xl p-4">{signedIn === null ? null : signedIn ? <Dashboard /> : <SignIn />}</main>
    </>
  );
}
