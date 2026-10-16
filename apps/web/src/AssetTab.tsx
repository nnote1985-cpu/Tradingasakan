import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { type ApiError, apiGet, type AssetStatus, type PortfolioRow, type Timeframe, type TrendState } from "./api";
import { armOf } from "./books";
import { money, pct } from "./format";
import { useLiveTicker } from "./liveTicker";

const SYMBOL = "BTCUSDT";   // the only asset the books trade; the API lists more once they are added
const TREND_TH: Record<TrendState, string> = { up: "ขาขึ้น", down: "ขาลง", mixed: "ไม่ชัด" };
const TREND_COLOR: Record<TrendState, string> = { up: "var(--gain)", down: "var(--loss)", mixed: "var(--text-secondary)" };

function Signed({ v }: { v: string | null | undefined }) {
  const n = v == null ? 0 : Number(v);
  return <span style={{ color: n > 0 ? "var(--gain)" : n < 0 ? "var(--loss)" : undefined }}>{pct(v ?? null)}</span>;
}

function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-xl border border-[var(--border)] bg-[var(--surface-1)] p-4">
      <h2 className="mb-2 font-semibold">{title}</h2>
      {children}
    </section>
  );
}

function Stat({ label, children, note }: { label: string; children: ReactNode; note?: ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-[var(--text-muted)]">{label}</dt>
      <dd className="font-medium">{children}</dd>
      {note && <dd className="text-xs text-[var(--text-muted)]">{note}</dd>}
    </div>
  );
}

function LivePrice({ stream }: { stream: string }) {
  const t = useLiveTicker(stream);
  const change = t.price && t.open24h ? String(Number(t.price) / Number(t.open24h) - 1) : null;
  return (
    <div data-testid="live-price">
      <div className="flex items-baseline gap-2">
        <span className="text-2xl font-semibold">{t.price ? money(t.price) : "–"}</span>
        <span className="text-sm text-[var(--text-secondary)]">USDT</span>
        {change && <span className="text-sm"><Signed v={change} /> 24 ชม.</span>}
      </div>
      <p className="text-xs text-[var(--text-muted)]">
        {t.status === "live" ? "ราคาสดจาก Binance spot (ใช้ดูอย่างเดียว)"
          : t.status === "stale" ? "ราคาสดขาดการเชื่อมต่อ กำลังต่อใหม่ ตัวเลขด้านบนอาจไม่ใช่ราคาล่าสุด"
          : t.status === "unsupported" ? "เบราว์เซอร์นี้เปิดราคาสดไม่ได้"
          : "กำลังเชื่อมต่อราคาสด…"}
      </p>
    </div>
  );
}

function TrendTile({ t }: { t: Timeframe }) {
  return (
    <div data-testid="trend-tile" className="rounded-lg border border-[var(--border)] p-3">
      <div className="text-xs text-[var(--text-muted)]">{t.timeframe}</div>
      <div className="font-semibold" style={{ color: t.state ? TREND_COLOR[t.state] : undefined }}>{t.state ? TREND_TH[t.state] : "ข้อมูลไม่พอ"}</div>
      <div className="mt-1 text-xs text-[var(--text-secondary)]">EMA20 {money(t.ema20)} · EMA50 {money(t.ema50)}</div>
    </div>
  );
}

function Books({ rows }: { rows: PortfolioRow[] }) {
  const held = rows.filter((r) => r.exposure_share !== null);
  if (held.length === 0) return <p className="text-sm text-[var(--text-secondary)]">ยังไม่มีพอร์ตที่ถือ BTC</p>;
  return (
    <div className="overflow-x-auto">
      <table data-testid="asset-books" className="w-full text-sm">
        <thead className="text-left text-[var(--text-secondary)]">
          <tr><th className="py-1 pr-3">พอร์ต</th><th className="pr-3">ถือ BTC</th><th>ผลตอบแทน</th></tr>
        </thead>
        <tbody>
          {held.map((r) => (
            <tr key={r.label} className="border-t border-[var(--grid)]">
              <td className="py-1 pr-3">{r.label.split("/")[0]} <span className="text-[var(--text-muted)]">{armOf(r.label).name}</span></td>
              <td className="pr-3">{pct(r.exposure_share, true)}</td>
              <td><Signed v={r.return} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** One asset's market status: information to follow, not a trade signal. Built for one symbol per page so more can be added. */
export function AssetTab({ rows }: { rows: PortfolioRow[] }) {
  const q = useQuery<AssetStatus, ApiError>({
    queryKey: ["asset", SYMBOL],
    queryFn: () => apiGet<AssetStatus>(`/api/v1/assets/${SYMBOL}`),
    refetchInterval: (query) => (query.state.data?.loading || query.state.data?.refreshing ? 5_000 : 300_000),
  });
  const a = q.data;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-4">
        <div>
          <h2 className="text-lg font-semibold">BTC <span className="text-sm font-normal text-[var(--text-secondary)]">Bitcoin · {SYMBOL}</span></h2>
          <LivePrice stream={a?.live_stream ?? "btcusdt@miniTicker"} />
        </div>
      </div>
      <p className="text-sm text-[var(--text-secondary)]">
        ข้อมูลไว้ติดตามตลาด ไม่ใช่สัญญาณซื้อขาย บอททุกตัวยังตัดสินใจจากราคาปิดรายวันตามกติกาของตัวเอง ตัวเลขด้านล่างคำนวณจากแท่งที่ปิดแล้วของ BTCUSDT perpetual ใน data.binance.vision ซึ่งช้ากว่าเวลาจริงประมาณหนึ่งวัน
      </p>
      {q.isPending ? (
        <p>กำลังโหลด…</p>
      ) : q.isError ? (
        <p role="alert" className="text-sm text-[var(--loss)]">{q.error.message ?? "โหลดข้อมูลสินทรัพย์ไม่สำเร็จ"}</p>
      ) : !a!.available ? (
        <p data-testid="asset-loading" className="text-sm text-[var(--text-secondary)]">
          {a!.failed ? "ดาวน์โหลดข้อมูลจาก data.binance.vision ไม่สำเร็จ จะลองใหม่อัตโนมัติในไม่กี่นาที" : "กำลังดาวน์โหลดข้อมูลย้อนหลังจาก data.binance.vision ครั้งแรกใช้เวลาประมาณหนึ่งนาที…"}
        </p>
      ) : (
        <>
          <Card title={`ราคาปิดวันที่ ${a!.data_through} (UTC)`}>
            {a!.stale && <p data-testid="asset-stale" className="mb-2 rounded bg-[var(--warn-bg)] px-2 py-1 text-xs text-[var(--warn-fg)]">ข้อมูลเก่ากว่าที่ควร archive ยังไม่มีวันล่าสุด</p>}
            <dl className="grid grid-cols-2 gap-3 sm:grid-cols-5">
              <Stat label="ราคาปิด">{money(a!.close ?? null)}</Stat>
              <Stat label="1 วัน"><Signed v={a!.change?.d1} /></Stat>
              <Stat label="7 วัน"><Signed v={a!.change?.d7} /></Stat>
              <Stat label="30 วัน"><Signed v={a!.change?.d30} /></Stat>
              <Stat label="กรอบราคา 30 วัน">{money(a!.range_30d?.low ?? null)} – {money(a!.range_30d?.high ?? null)}</Stat>
            </dl>
          </Card>
          <Card title="เทรนด์แต่ละ timeframe">
            <div className="grid grid-cols-3 gap-2">{a!.timeframes?.map((t) => <TrendTile key={t.timeframe} t={t} />)}</div>
            <p className="mt-2 text-xs text-[var(--text-muted)]">ขาขึ้น = ราคาปิด &gt; EMA20 &gt; EMA50, ขาลง = กลับกัน, นอกนั้นไม่ชัด ใช้แท่งที่ปิดแล้วเท่านั้น</p>
            <p data-testid="regime" className="mt-2 text-sm">
              ค่าเฉลี่ย 100 วัน (เส้นที่บอท spot ใช้) {money(a!.regime?.sma100 ?? null)}:{" "}
              {a!.regime?.trend_up == null ? "ข้อมูลไม่พอ" : <>ราคาอยู่<b>{a!.regime.trend_up ? "เหนือ" : "ใต้"}</b>เส้น ห่าง <Signed v={a!.regime.dist_to_sma} /></>}
            </p>
          </Card>
          <Card title="ความเสี่ยงของตลาด">
            <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Stat label="Funding เฉลี่ย 7 วัน" note={a!.funding?.hot == null ? undefined : `${a!.funding.hot ? "สูงผิดปกติ" : "ปกติ"}${a!.funding.estimated ? " · บางวันเป็นค่าประมาณ" : ""}`}>
                {a!.funding?.per_8h_7d == null ? "–" : `${(Number(a!.funding.per_8h_7d) * 100).toFixed(4)}% / 8 ชม.`}
              </Stat>
              <Stat label="ความผันผวน 30 วัน" note="ต่อปี">{pct(a!.vol_30d ?? null, true)}</Stat>
              <Stat label="ลงจากจุดสูงสุด 1 ปี">{pct(a!.drawdown_365d ?? null, true)}</Stat>
              <Stat label="Open interest" note={a!.open_interest ? <>7 วัน <Signed v={a!.open_interest.change_7d} /></> : undefined}>
                {a!.open_interest?.usdt ? `${(Number(a!.open_interest.usdt) / 1e9).toFixed(2)} พันล้าน USDT` : "–"}
              </Stat>
            </dl>
            {!!a!.events?.length && (
              <ul data-testid="asset-events" className="mt-3 list-disc pl-5 text-sm">{a!.events.map((e) => <li key={e}>{e}</li>)}</ul>
            )}
          </Card>
        </>
      )}
      <Card title="พอร์ตที่ถือ BTC ตอนนี้">
        <Books rows={rows} />
      </Card>
    </div>
  );
}
