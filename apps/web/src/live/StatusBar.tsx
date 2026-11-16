import { useEffect, useState } from "react";
import { money } from "../format";
import { useLiveTicker } from "../liveTicker";
import { type LiveHeartbeat, type LiveStatus, useLive } from "./LiveProvider";

// F1 status bar on every page (m1-realtime-plan.md §3.2). Every value shows its age; stale values say so in words.

const QUOTE_STALE_MS = 30_000;
const NORMAL_SAFETY = new Set(["RUNNING", "RECOVERING"]);
const STATUS_TEXT: Record<LiveStatus, { icon: string; text: string }> = {
  live: { icon: "●", text: "ข้อมูลสด" },
  connecting: { icon: "◌", text: "กำลังเชื่อมต่อ" },
  stale: { icon: "⚠", text: "ข้อมูลค้าง (ใช้รอบดึงปกติ)" },
  off: { icon: "○", text: "ไม่ได้เชื่อมต่อ" },
};

function ago(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  return s < 60 ? `${s} วิ.` : `${Math.floor(s / 60)} นาที`;
}

/** The Testnet quote to show: DEFAULT first, else the first account that has one. */
export function testnetQuote(beats: LiveHeartbeat[]): { account: string; bid: string; at: number } | null {
  const withQuote = beats.filter((b) => b.quote_bid && b.updated_at);
  const b = withQuote.find((x) => x.account === "DEFAULT") ?? withQuote[0];
  if (!b) return null;
  return { account: b.account, bid: b.quote_bid!, at: Date.parse(b.updated_at!) - (b.quote_age_ms ?? 0) };
}

export function StatusBar() {
  const live = useLive();
  const mainnet = useLiveTicker("btcusdt@miniTicker");
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1_000);
    return () => clearInterval(t);
  }, []);
  const beats = live.heartbeats ?? [];
  const silent = beats.filter((b) => b.silent);
  const guarded = beats.filter((b) => b.safety && !NORMAL_SAFETY.has(b.safety));
  const quote = testnetQuote(beats);
  const quoteAge = quote ? now - quote.at : null;
  const quoteStale = quoteAge !== null && (quoteAge > QUOTE_STALE_MS || live.status !== "live");
  const spread = quote && mainnet.price ? (Number(quote.bid) / Number(mainnet.price) - 1) * 100 : null;
  const st = STATUS_TEXT[live.status];
  return (
    <div className="live-bar" role="status" aria-live="polite" data-testid="status-bar">
      <span className={`live-chip live-${live.status}`} data-testid="live-status">
        <span aria-hidden="true">{st.icon}</span> {st.text}
        {live.status === "live" && !live.dbOk && " · ฐานข้อมูลไม่ตอบ"}
        {live.error && ` · ${live.error}`}
      </span>
      {live.heartbeats && (
        <span data-testid="live-workers" className={silent.length ? "live-warn" : ""}>
          worker {beats.length - silent.length}/{beats.length}
          {silent.length > 0 && <> · <span aria-hidden="true">⚠</span> เงียบ: {silent.map((b) => b.account).join(", ")}</>}
        </span>
      )}
      {guarded.length > 0 && (
        <span data-testid="live-guardian" className="live-warn">
          <span aria-hidden="true">⛔</span> Guardian: {guarded.map((b) => `${b.account} ${b.safety}`).join(", ")}
        </span>
      )}
      <span data-testid="live-quote" className="live-quote">
        BTC Testnet {quote ? money(quote.bid) : "–"}
        {quote && quoteAge !== null && <span className={quoteStale ? "live-warn" : "live-muted"}> ({quoteStale ? "ค้าง · " : ""}{ago(quoteAge)})</span>}
        <span className="live-extra">
          {" · "}Mainnet {mainnet.price ? money(mainnet.price) : "–"}
          {mainnet.status === "stale" && <span className="live-warn"> (ค้าง)</span>}
          {spread !== null && <span className="live-muted"> · ต่าง {spread >= 0 ? "+" : ""}{spread.toFixed(2)}%</span>}
        </span>
      </span>
    </div>
  );
}
