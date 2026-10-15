import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { type ApiError, apiGet, type Overview, supabase } from "./api";
import { ModeBadge } from "./ModeBadge";

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
      <input className="rounded border px-2 py-1" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="อีเมล" />
      <button className="rounded bg-slate-900 px-3 py-1 text-white" type="submit">เข้าสู่ระบบ</button>
    </form>
  );
}

function OverviewPage() {
  // DEFAULT transport is REST polling (§21.3); paused automatically when the tab is hidden.
  const q = useQuery<Overview, ApiError>({ queryKey: ["overview"], queryFn: () => apiGet<Overview>("/api/v1/overview"), refetchInterval: 10_000 });
  if (q.isPending) return <p>กำลังโหลด…</p>;
  if (q.isError) return <p role="alert">{q.error.message ?? "โหลดข้อมูลไม่สำเร็จ"}</p>;
  return (
    <section className="space-y-2">
      <div className="flex items-center gap-2">
        <h1 className="text-xl font-semibold">ภาพรวม</h1>
        <ModeBadge mode={q.data.mode} />
      </div>
      <p>พอร์ต: {q.data.portfolios.length}</p>
    </section>
  );
}

export function App() {
  const [signedIn, setSignedIn] = useState<boolean | null>(null);
  useEffect(() => {
    if (!supabase) return setSignedIn(false);
    supabase.auth.getSession().then(({ data }) => setSignedIn(!!data.session));
    const { data } = supabase.auth.onAuthStateChange((_e, s) => setSignedIn(!!s));
    return () => data.subscription.unsubscribe();
  }, []);
  return <main className="mx-auto max-w-3xl p-4">{signedIn === null ? null : signedIn ? <OverviewPage /> : <SignIn />}</main>;
}
