// Chart page: controls, data, the lazy chart, the marker details and a table view (plan §4.3).
import { useQuery } from "@tanstack/react-query";
import { lazy, Suspense, useMemo, useState } from "react";
import { type ApiError, apiGet } from "../api";
import { ModeBadge } from "../ModeBadge";
import {
  allBars, type BarsResponse, haltBars, type IndicatorSet, type IndicatorsResponse, lineColor, lookup, markers, type OverlaysResponse,
  type RunItem, runLines, runMarkers, type RunResponse, runWindow, type Timeframe, type Venue,
} from "./model";

const CandleChart = lazy(() => import("./CandleChart"));

const SYMBOL = "BTCUSDT";
const POLL_MS = 30_000;
const TIMEFRAMES: Timeframe[] = ["1h", "4h", "1d"];
const SETS: { key: IndicatorSet; label: string }[] = [
  { key: "basic", label: "พื้นฐาน (EMA, Donchian, ATR)" }, { key: "r10a", label: "R10a (SMA100 ±2%)" }, { key: "e1", label: "E1 (EMA, Donchian 20, ADX)" },
];
export const BOOKS = [
  { key: "DEFAULT/tn-r10a-code-v1", label: "R10a บน Testnet (DEFAULT)" },
  { key: "AI/tn-r2-v1", label: "AI r2 บน Testnet (AI)" },
];
const COLORS = { buy: "#1d7f3f", sell: "#c53b3a", decision: "#2a78d6", none: "#6f6e69" };

function retry(n: number, e: ApiError) {
  return e?.code === "chart_loading" && n < 20;
}

const btn = "min-h-10 rounded border border-[var(--border)] px-3 text-sm";
const on = "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900";

export function ChartPanel({ initialBook = "" }: { initialBook?: string }) {
  const [tf, setTf] = useState<Timeframe>("1d");
  const [marketVenue, setVenue] = useState<Venue>("spot");
  const [set, setSet] = useState<IndicatorSet>("basic");
  // what is drawn over the bars: nothing, a Testnet bot ("bot:ACCOUNT/BOOK") or one run ("run:KIND/ID"); never two
  const [source, setSource] = useState(initialBook ? `bot:${initialBook}` : "");
  const book = source.startsWith("bot:") ? source.slice(4) : "";
  const runKey = source.startsWith("run:") ? source.slice(4) : "";
  const runs = useQuery<{ items: RunItem[] }, ApiError>({ queryKey: ["chart-runs"], queryFn: () => apiGet("/api/v1/charts/runs"), staleTime: 300_000 });
  const runItem = runs.data?.items.find((r) => `${r.kind}/${r.id}` === runKey) ?? null;
  const run = useQuery<RunResponse, ApiError>({ queryKey: ["chart-run", runKey], queryFn: () => apiGet(`/api/v1/charts/runs/${runKey}`), enabled: runKey !== "" });
  const venue: Venue = runItem?.venue ?? marketVenue;
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [picked, setPicked] = useState<string | null>(null);
  const [hover, setHover] = useState<Record<string, number | null> | null>(null);
  const [table, setTable] = useState(false);
  const timeframe: Timeframe = runItem ? "1d" : tf;          // runs are daily
  const q = `venue=${venue}&timeframe=${timeframe}${runItem ? runWindow(runItem) : ""}`;
  const bars = useQuery<BarsResponse, ApiError>({
    queryKey: ["chart-bars", q], queryFn: () => apiGet(`/api/v1/charts/${SYMBOL}/bars?${q}`),
    refetchInterval: runItem ? false : POLL_MS, retry, retryDelay: 3_000,
  });
  const ind = useQuery<IndicatorsResponse, ApiError>({
    queryKey: ["chart-ind", q, set], queryFn: () => apiGet(`/api/v1/charts/${SYMBOL}/indicators?${q}&set=${set}`),
    refetchInterval: POLL_MS * 4, retry, retryDelay: 3_000,
  });
  const ov = useQuery<OverlaysResponse, ApiError>({
    queryKey: ["chart-overlays", book], queryFn: () => apiGet(`/api/v1/bots/${book}/overlays`),
    enabled: book !== "", refetchInterval: POLL_MS,
  });
  // overlays are drawn only with their mode label (plan §5.4 C5)
  const overlays = book && ov.data?.mode ? ov.data : null;
  const runData = runKey && run.data?.mode ? run.data : null;
  const mode = overlays?.mode ?? runData?.mode ?? null;
  const marks = useMemo(() => [...markers(overlays, timeframe, COLORS), ...runMarkers(runData, COLORS)].sort((a, b) => a.time - b.time),
    [overlays, runData, timeframe]);
  const halts = useMemo(() => (bars.data ? haltBars(overlays, allBars(bars.data), timeframe, Date.now() / 1000) : []), [overlays, bars.data, timeframe]);
  const extra = useMemo(() => runLines(runData), [runData]);
  const detail = picked ? lookup(picked, overlays) : null;
  const labels = useMemo(() => new Map([...(ind.data?.series ?? []), ...runLines(run.data)].map((s) => [s.key, s.label])), [ind.data, run.data]);
  const toggle = (key: string) => setHidden((h) => { const n = new Set(h); if (n.has(key)) n.delete(key); else n.add(key); return n; });

  return (
    <section className="panel space-y-3" aria-label="กราฟแท่งเทียน BTCUSDT">
      <div className="flex flex-wrap items-center gap-2">
        {mode ? <ModeBadge mode={mode} /> : <span data-testid="market-label" className="rounded bg-[var(--surface-0)] px-2 py-0.5 text-sm">ราคาตลาด Mainnet</span>}
        <h2 className="font-semibold">{SYMBOL} {venue === "spot" ? "Spot" : "Perpetual"}</h2>
        <div role="group" aria-label="ช่วงเวลาแท่ง" className="flex gap-1">
          {TIMEFRAMES.map((t) => <button key={t} type="button" aria-pressed={timeframe === t} disabled={!!runItem && t !== "1d"}
            className={`${btn} ${timeframe === t ? on : ""} disabled:opacity-40`} onClick={() => setTf(t)}>{t}</button>)}
        </div>
      </div>
      <div className="flex flex-wrap gap-2 text-sm">
        <label className="flex min-w-0 max-w-full items-center gap-1">ตลาด
          <select className={`${btn} min-w-0 max-w-full`} value={venue} disabled={!!runItem} onChange={(e) => setVenue(e.target.value as Venue)}>
            <option value="spot">Spot</option><option value="perp">Perpetual (สัญญาณ R10a)</option>
          </select>
        </label>
        <label className="flex min-w-0 max-w-full items-center gap-1">อินดิเคเตอร์
          <select className={`${btn} min-w-0 max-w-full`} value={set} onChange={(e) => { setSet(e.target.value as IndicatorSet); setHidden(new Set()); }}>
            {SETS.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
        </label>
        <label className="flex min-w-0 max-w-full items-center gap-1">แสดง
          <select className={`${btn} min-w-0 max-w-full`} value={source} onChange={(e) => { setSource(e.target.value); setPicked(null); }}>
            <option value="">ราคาอย่างเดียว</option>
            <optgroup label="บอทบน Testnet">{BOOKS.map((b) => <option key={b.key} value={`bot:${b.key}`}>{b.label}</option>)}</optgroup>
            {(runs.data?.items ?? []).some((r) => r.kind === "backtest") && (
              <optgroup label="Backtest">{runs.data!.items.filter((r) => r.kind === "backtest").map((r) => <option key={r.id} value={`run:backtest/${r.id}`}>{r.label}</option>)}</optgroup>
            )}
            {(runs.data?.items ?? []).some((r) => r.kind === "shadow") && (
              <optgroup label="พอร์ตจำลอง (Shadow)">{runs.data!.items.filter((r) => r.kind === "shadow").map((r) => <option key={r.id} value={`run:shadow/${r.id}`}>{r.label}</option>)}</optgroup>
            )}
          </select>
        </label>
      </div>
      {ind.data && (
        <div className="flex flex-wrap gap-1" role="group" aria-label="เปิด/ปิดเส้นอินดิเคเตอร์">
          {ind.data.series.map((s, i) => (
            <button key={s.key} type="button" title={s.formula} aria-pressed={!hidden.has(s.key)}
              className={`${btn} flex items-center gap-1 text-xs ${hidden.has(s.key) ? "opacity-50 line-through" : ""}`} onClick={() => toggle(s.key)}>
              <span aria-hidden="true" className="inline-block h-0.5 w-4" style={{ background: lineColor(i) }} />{s.label}
            </button>
          ))}
        </div>
      )}
      <p className="min-h-5 text-xs text-[var(--text-secondary)]" aria-live="polite">
        {hover ? Object.entries(hover).filter(([, v]) => v != null).map(([k, v]) => `${k === "close" ? "ปิด" : labels.get(k) ?? k} ${v!.toLocaleString("en-US", { maximumFractionDigits: 2 })}`).join(" · ")
          : bars.data?.forming ? `แท่งปัจจุบันยังไม่ปิด (${bars.data.forming.minutes} นาที) ไม่ใช้คำนวณอินดิเคเตอร์` : " "}
      </p>
      {bars.isPending ? <p>กำลังโหลดกราฟ…</p>
        : bars.isError ? <p role="alert" className="text-sm text-[var(--loss)]">{bars.error.code === "chart_loading" ? "กำลังดาวน์โหลดแท่งย้อนหลังจาก archive…" : bars.error.message}</p>
          : (
            <Suspense fallback={<p>กำลังโหลดกราฟ…</p>}>
              <CandleChart bars={bars.data} indicators={ind.data ?? null} hidden={hidden} marks={marks} halts={halts} extra={extra} timeframe={timeframe} fit={!!runItem}
                onMarker={setPicked} onHover={setHover} />
            </Suspense>
          )}
      {bars.data && bars.data.gaps.length > 0 && (
        <p className="text-xs text-[var(--text-muted)]">ช่วงที่ไม่มีข้อมูล {bars.data.gaps.length} ช่วง แสดงเป็นช่องว่าง ไม่เติมเอง (ล่าสุด {bars.data.gaps.at(-1)!.from.slice(0, 16).replace("T", " ")} UTC)</p>
      )}
      {detail && <MarkerDetail detail={detail} onClose={() => setPicked(null)} />}
      {overlays && <Events o={overlays} onPick={setPicked} />}
      {runData && (
        <p data-testid="run-summary" className="text-sm text-[var(--text-secondary)]">
          {runData.mode === "BACKTEST" ? "ผลย้อนหลังจาก simulator" : "พอร์ตจำลองด้วยเงินสมมติ"} · {runData.range?.join(" → ")} · {runData.events.length} จุดซื้อขาย
          {runData.note ? ` · ${runData.note}` : ""}
        </p>
      )}
      <button type="button" className="text-sm text-[var(--text-secondary)] underline" aria-expanded={table} onClick={() => setTable((t) => !t)}>
        {table ? "ซ่อนตาราง" : "ดูเป็นตาราง"}
      </button>
      {table && bars.data && <BarTable bars={bars.data} />}
      <p className="text-xs text-[var(--text-muted)]">
        กราฟใช้ <a className="underline" href="https://www.tradingview.com/" target="_blank" rel="noreferrer">TradingView Lightweight Charts™</a> ©
        TradingView, Inc. (Apache-2.0) · แท่งที่ปิดแล้วจาก Binance (ฐานข้อมูลของ worker และ data.binance.vision)
      </p>
    </section>
  );
}

function MarkerDetail({ detail, onClose }: { detail: NonNullable<ReturnType<typeof lookup>>; onClose: () => void }) {
  const { fill, decision } = detail;
  return (
    <div data-testid="marker-detail" className="rounded border border-[var(--border)] p-3 text-sm">
      <div className="flex justify-between"><strong>รายละเอียด</strong><button type="button" className="underline" onClick={onClose}>ปิด</button></div>
      {fill && (
        <p>{fill.side === "BUY" ? "ซื้อ" : "ขาย"} {fill.qty} BTC @ {fill.price} เมื่อ {fill.at.slice(0, 19).replace("T", " ")} UTC
          {fill.kind === "halt" ? " · Guardian ขายตอน HALT" : ""} · order {fill.client_order_id.slice(0, 12)}…</p>
      )}
      {decision && (
        <p>การตัดสินใจวันที่ {decision.day} ({decision.rules_version}): {decision.action} น้ำหนัก {decision.previous_weight ?? "–"} → {decision.weight ?? "–"}
          {decision.outcome ? ` · ผล ${decision.outcome}` : ""}<br /><span className="text-[var(--text-secondary)]">{decision.notes}</span></p>
      )}
      {!decision && fill && <p className="text-[var(--text-secondary)]">คำสั่งนี้ไม่ได้มาจากการตัดสินใจรายวัน ({fill.decision_id})</p>}
    </div>
  );
}

function Events({ o, onPick }: { o: OverlaysResponse; onPick: (id: string) => void }) {
  return (
    <details className="text-sm">
      <summary className="cursor-pointer">เหตุการณ์ของบอท · {o.fills.length} เทรด · {o.decisions.length} การตัดสินใจ · {o.halts.length} ช่วง HALT</summary>
      <ul className="mt-2 space-y-1">
        {o.fills.map((f, i) => <li key={`f${i}`}><button type="button" className="underline" onClick={() => onPick(`fill:${i}`)}>{f.side === "BUY" ? "▲ ซื้อ" : "▼ ขาย"} {f.qty} @ {f.price} · {f.at.slice(0, 16).replace("T", " ")}</button></li>)}
        {o.halts.map((h, i) => <li key={`h${i}`}>■ HALT {h.from.slice(0, 16).replace("T", " ")} → {h.to ? h.to.slice(0, 16).replace("T", " ") : "ยังไม่ RESUME"}</li>)}
        {o.decisions.map((d, i) => <li key={`d${i}`}><button type="button" className="underline" onClick={() => onPick(`decision:${i}`)}>{d.action === "NO_DECISION" ? "□" : "●"} {d.day} {d.action}</button></li>)}
      </ul>
    </details>
  );
}

function BarTable({ bars }: { bars: BarsResponse }) {
  const rows = bars.bars.slice(-100).reverse();
  return (
    <div className="max-h-80 overflow-auto">
      <table className="w-full text-xs">
        <caption className="text-left text-[var(--text-muted)]">100 แท่งล่าสุดที่ปิดแล้ว ({bars.timeframe}, UTC)</caption>
        <thead><tr><th className="text-left">เวลาเปิด</th><th>เปิด</th><th>สูง</th><th>ต่ำ</th><th>ปิด</th><th>ทิศ</th></tr></thead>
        <tbody>
          {rows.map((b) => (
            <tr key={b.t}><td>{new Date(b.t * 1000).toISOString().slice(0, 16).replace("T", " ")}</td><td>{b.o}</td><td>{b.h}</td><td>{b.l}</td><td>{b.c}</td>
              <td>{Number(b.c) >= Number(b.o) ? "▲ ขึ้น" : "▼ ลง"}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
