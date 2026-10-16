import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { type ApiError, apiGet, type Overview, type RiskStatusResponse, supabase } from "./api";
import { BookGroupCard } from "./BookGroupCard";
import { groupBooks } from "./books";
import { ModeBadge } from "./ModeBadge";
import { PortfolioTable } from "./PortfolioTable";
import { ShadowBanner } from "./ShadowBanner";

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

function OverviewPage() {
  // DEFAULT transport is REST polling (§21.3); paused automatically when the tab is hidden.
  const q = useQuery<Overview, ApiError>({ queryKey: ["overview"], queryFn: () => apiGet<Overview>("/api/v1/overview"), refetchInterval: 10_000 });
  const risk = useQuery<RiskStatusResponse, ApiError>({ queryKey: ["risk-status"], queryFn: () => apiGet<RiskStatusResponse>("/api/v1/risk-status"), refetchInterval: 30_000 });
  const [showTable, setShowTable] = useState(false);
  if (q.isPending) return <p>กำลังโหลด…</p>;
  if (q.isError) return <p role="alert">{q.error.message ?? "โหลดข้อมูลไม่สำเร็จ"}</p>;
  const groups = groupBooks(q.data.portfolios);
  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-xl font-semibold">ภาพรวมพอร์ต</h1>
        <ModeBadge mode={q.data.mode} />
        <button className="ml-auto text-sm text-[var(--text-secondary)] underline" onClick={() => supabase?.auth.signOut()}>ออกจากระบบ</button>
      </div>
      <p className="text-sm text-[var(--text-secondary)]">
        ทุกพอร์ตเริ่มที่ 10,000 USDT ด้วยเงินสมมติ ราคาปิดรายวันตามเวลา UTC ตัวเลขเป็นมูลค่าหลังหักค่าธรรมเนียมและค่า AI
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
          {showTable && <div className="mt-2 overflow-x-auto"><PortfolioTable rows={q.data.portfolios} /></div>}
        </div>
      )}
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
      <main className="mx-auto max-w-5xl p-4">{signedIn === null ? null : signedIn ? <OverviewPage /> : <SignIn />}</main>
    </>
  );
}
