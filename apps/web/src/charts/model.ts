// Chart data from the API (docs/m2-charts-plan.md §4.2) and the pure helpers the chart uses.
// Values arrive as strings; they become numbers here only to be drawn.

export type Timeframe = "1h" | "4h" | "1d";
export type Venue = "spot" | "perp";
export type IndicatorSet = "basic" | "r10a" | "e1";
export type ChartMode = "TESTNET" | "SHADOW" | "BACKTEST";

export type ApiBar = { t: number; o: string; h: string; l: string; c: string; v: string | null };
export type BarsResponse = {
  symbol: string; venue: Venue; timeframe: Timeframe; bars: ApiBar[];
  forming: (ApiBar & { closed: false; minutes: number }) | null;
  gaps: { from: string; to: string }[]; sources: { from: string; to: string; source: "db" | "archive" }[]; as_of: string;
};
export type IndicatorSeries = { key: string; label: string; panel: "price" | "lower" | "equity" | "drawdown"; formula: string; values: { t: number; v: string | null }[] };
export type IndicatorsResponse = { set: IndicatorSet; series: IndicatorSeries[] };

export type Fill = { t: number; at: string; side: "BUY" | "SELL"; qty: string; price: string; fee: string | null; fee_asset: string | null;
  client_order_id: string; decision_id: string; kind: "strategy" | "halt"; mode: string };
export type Decision = { day: string; t: number; decided_at: string; rules_version: string; action: string; weight: string | null;
  previous_weight: string | null; notes: string; inputs_sha256: string; client_order_id: string | null; outcome: string | null; filled: boolean };
export type Halt = { from: string; t_from: number; to: string | null; t_to: number | null; flat_at: string | null; reason: string | null; resume_reason: string | null };
export type OverlaysResponse = { available: boolean; mode: ChartMode; fills: Fill[]; decisions: Decision[]; halts: Halt[];
  alerts: { t: number; at: string; kind: string; detail: string | null }[] };

export const STEP: Record<Timeframe, number> = { "1h": 3600, "4h": 14400, "1d": 86400 };

export type Candle = { time: number; open: number; high: number; low: number; close: number };
export type VolumeBar = { time: number; value: number; up: boolean };

export function toCandle(b: ApiBar): Candle {
  return { time: b.t, open: Number(b.o), high: Number(b.h), low: Number(b.l), close: Number(b.c) };
}

export function toVolume(b: ApiBar): VolumeBar {
  return { time: b.t, value: b.v == null ? 0 : Number(b.v), up: Number(b.c) >= Number(b.o) };
}

/** Closed bars plus the forming bar (drawn last, never part of the indicators). */
export function allBars(r: BarsResponse): ApiBar[] {
  return r.forming ? [...r.bars, r.forming] : r.bars;
}

/**
 * How to bring the chart from `prev` to `next` without reloading it (plan §5.4 C1): when every bar
 * of `prev` except its last is unchanged in `next`, only the bars from that last one on are sent
 * with `series.update()`; otherwise the whole set is replaced.
 */
export function planUpdate(prev: ApiBar[] | null, next: ApiBar[]): { kind: "set" } | { kind: "update"; bars: ApiBar[] } {
  if (!prev || prev.length === 0 || next.length < prev.length) return { kind: "set" };
  const keep = prev.length - 1;
  for (let i = 0; i < keep; i++) {
    const a = prev[i], b = next[i];
    if (a.t !== b.t || a.o !== b.o || a.h !== b.h || a.l !== b.l || a.c !== b.c || a.v !== b.v) return { kind: "set" };
  }
  if (next[keep].t !== prev[keep].t) return { kind: "set" };
  return { kind: "update", bars: next.slice(keep) };
}

export function floorTo(t: number, tf: Timeframe): number {
  return Math.floor(t / STEP[tf]) * STEP[tf];
}

export type Marker = { id: string; time: number; position: "aboveBar" | "belowBar" | "inBar"; shape: "arrowUp" | "arrowDown" | "circle" | "square";
  color: string; text: string };

export type MarkerColors = { buy: string; sell: string; decision: string; none: string };

/** Fills as ▲ buy / ▼ sell (shape, not only colour), decisions as ● (□ when no decision was made). */
export function markers(o: OverlaysResponse | null | undefined, tf: Timeframe, c: MarkerColors): Marker[] {
  if (!o) return [];
  const out: Marker[] = [];
  o.fills.forEach((f, i) => out.push({
    id: `fill:${i}`, time: floorTo(f.t, tf), position: f.side === "BUY" ? "belowBar" : "aboveBar",
    shape: f.side === "BUY" ? "arrowUp" : "arrowDown", color: f.side === "BUY" ? c.buy : c.sell,
    text: `${f.side === "BUY" ? "ซื้อ" : "ขาย"} ${f.qty}${f.kind === "halt" ? " HALT" : ""}`,   // price and the rest: click the marker
  }));
  o.decisions.forEach((d, i) => {
    // daily decisions sit on the bar of the day they used; intraday charts show them when they were made
    const t = tf === "1d" ? d.t : floorTo(Date.parse(d.decided_at) / 1000, tf);
    const none = d.action === "NO_DECISION";
    out.push({ id: `decision:${i}`, time: t, position: "aboveBar", shape: none ? "square" : "circle", color: none ? c.none : c.decision,
      text: none ? "ไม่ตัดสิน" : d.action === "HOLD" ? "" : `${d.action} ${d.previous_weight ?? "–"}→${d.weight ?? "–"}` });
  });
  return out.sort((a, b) => a.time - b.time);
}

/** Bars (open times) that fall inside a HALT period, for the shaded band. */
export function haltBars(o: OverlaysResponse | null | undefined, bars: ApiBar[], tf: Timeframe, now: number): number[] {
  if (!o || o.halts.length === 0) return [];
  const step = STEP[tf];
  return bars.filter((b) => o.halts.some((h) => b.t + step > h.t_from && b.t < (h.t_to ?? now))).map((b) => b.t);
}

export function lineData(s: IndicatorSeries): ({ time: number; value: number } | { time: number })[] {
  // a null value is a whitespace point: the line breaks there instead of joining across the gap
  return s.values.map((p) => (p.v == null ? { time: p.t } : { time: p.t, value: Number(p.v) }));
}

export function lookup(id: string, o: OverlaysResponse | null | undefined): { fill?: Fill; decision?: Decision } | null {
  if (!o) return null;
  const [kind, n] = id.split(":");
  const i = Number(n);
  if (kind === "fill" && o.fills[i]) {
    const fill = o.fills[i];
    return { fill, decision: o.decisions.find((d) => d.client_order_id === fill.client_order_id) };
  }
  if (kind === "decision" && o.decisions[i]) {
    const decision = o.decisions[i];
    return { decision, fill: o.fills.find((f) => f.client_order_id === decision.client_order_id) };
  }
  return null;
}

/** Line colours by position in the set, shared by the chart and the legend chips. */
export const PALETTE = ["#2a78d6", "#eb6834", "#7a5bd0", "#1f9d8a", "#c2410c", "#64748b", "#b45309", "#0e7490"];
export const lineColor = (i: number) => PALETTE[i % PALETTE.length];

export type RunEvent = { t: number; side: "BUY" | "SELL"; action: "open" | "close" | "rebalance"; price: string | null; qty: string | null; text: string };
export type RunItem = { kind: "backtest" | "shadow"; id: string; label: string; mode: ChartMode; venue: Venue; range: [string, string] | null };
export type RunResponse = RunItem & { note: string | null; events: RunEvent[]; equity: { t: number; v: string }[];
  benchmark: { t: number; v: string }[]; drawdown: { t: number; v: string }[] };

/** Backtest / shadow trades: ▲ buy below the bar, ▼ sell above it. */
export function runMarkers(r: RunResponse | null | undefined, c: MarkerColors): Marker[] {
  if (!r) return [];
  const quiet = r.events.length > 30;   // a long run: shapes only, the labels would cover the bars
  return r.events.map((e, i) => ({ id: `run:${i}`, time: e.t, position: e.side === "BUY" ? "belowBar" : "aboveBar",
    shape: e.side === "BUY" ? "arrowUp" : "arrowDown", color: e.side === "BUY" ? c.buy : c.sell, text: quiet ? "" : e.text }));
}

/** Equity (with buy-and-hold when the run has one) and drawdown as lower-panel lines. */
export function runLines(r: RunResponse | null | undefined): IndicatorSeries[] {
  if (!r) return [];
  const out: IndicatorSeries[] = [{ key: "run_equity", label: "มูลค่าพอร์ต", panel: "equity", formula: "equity", values: r.equity }];
  if (r.benchmark.length) out.push({ key: "run_benchmark", label: "ถือ BTC เฉย ๆ", panel: "equity", formula: "buy and hold", values: r.benchmark });
  out.push({ key: "run_drawdown", label: "Drawdown", panel: "drawdown", formula: "1 - equity / peak", values: r.drawdown.map((p) => ({ t: p.t, v: p.v === null ? null : String(-Number(p.v)) })) });
  return out;
}

/** Bars to request for a run: its whole range (daily). */
export function runWindow(r: RunItem): string {
  if (!r.range) return "";
  const to = new Date(Date.parse(`${r.range[1]}T00:00:00Z`) + 86400_000).toISOString();
  return `&from=${r.range[0]}T00:00:00Z&to=${to}`;
}
