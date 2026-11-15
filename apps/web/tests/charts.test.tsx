import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import CandleChart from "../src/charts/CandleChart";
import { ChartPanel } from "../src/charts/ChartPanel";
import { type ApiBar, type BarsResponse, haltBars, lookup, markers, type OverlaysResponse, planUpdate } from "../src/charts/model";

// --- a recording stand-in for lightweight-charts -------------------------------------------------
const calls: { series: string; op: string; arg?: unknown }[] = [];
let charts = 0;
vi.mock("lightweight-charts", () => {
  const series = (name: string) => ({
    setData: (d: unknown) => calls.push({ series: name, op: "setData", arg: d }),
    update: (d: unknown) => calls.push({ series: name, op: "update", arg: d }),
    applyOptions: () => {},
  });
  return {
    CandlestickSeries: "Candlestick", HistogramSeries: "Histogram", LineSeries: "Line",
    createChart: () => {
      charts += 1;
      let n = 0;
      return {
        addSeries: (kind: string) => series(`${kind}${n++}`), removeSeries: () => {}, priceScale: () => ({ applyOptions: () => {} }),
        subscribeClick: () => {}, subscribeCrosshairMove: () => {}, applyOptions: () => {}, remove: () => {},
      };
    },
    createSeriesMarkers: () => ({ setMarkers: (m: unknown) => calls.push({ series: "markers", op: "setMarkers", arg: m }) }),
  };
});

beforeEach(() => { calls.length = 0; charts = 0; });
afterEach(() => cleanup());

const bar = (t: number, c: string, o = "100"): ApiBar => ({ t, o, h: "110", l: "90", c, v: "5" });
const D = 86400;
const resp = (bars: ApiBar[], forming: ApiBar | null = null): BarsResponse => ({
  symbol: "BTCUSDT", venue: "spot", timeframe: "1d", bars, forming: forming && { ...forming, closed: false, minutes: 30 },
  gaps: [], sources: [], as_of: "2026-11-15T00:00:00Z",
});
const OV: OverlaysResponse = {
  available: true, mode: "TESTNET",
  fills: [
    { t: 1792398367, at: "2026-10-19T08:26:07.335000+00:00", side: "BUY", qty: "0.02411", price: "82786.01", fee: "0", fee_asset: "BTC",
      client_order_id: "tak-8e19", decision_id: "r10a-2026-10-18", kind: "strategy", mode: "TESTNET" },
    { t: 1792510817, at: "2026-10-20T15:40:17.786000+00:00", side: "SELL", qty: "0.02408", price: "82569.99", fee: "0", fee_asset: "USDT",
      client_order_id: "tak-2702", decision_id: "halt-e2", kind: "halt", mode: "TESTNET" },
  ],
  decisions: [
    { day: "2026-10-18", t: 1792281600, decided_at: "2026-10-19T08:25:50+00:00", rules_version: "tn-r10a-code-v1", action: "OPEN", weight: "1",
      previous_weight: "0", notes: "close 82,078", inputs_sha256: "a", client_order_id: "tak-8e19", outcome: "FILLED", filled: true },
    { day: "2026-10-29", t: 1793232000, decided_at: "2026-10-30T08:40:00+00:00", rules_version: "tn-r10a-code-v1", action: "NO_DECISION", weight: null,
      previous_weight: "1", notes: "ราคาย้อนหลัง 100 วันไม่ครบ", inputs_sha256: "b", client_order_id: null, outcome: null, filled: false },
  ],
  halts: [{ from: "2026-10-20T15:40:02+00:00", t_from: 1792510802, to: "2026-10-20T15:43:31+00:00", t_to: 1792511011, flat_at: null, reason: "drill", resume_reason: null }],
  alerts: [],
};
const COLORS = { buy: "g", sell: "r", decision: "b", none: "m" };

// --- model -----------------------------------------------------------------------------------------
test("planUpdate updates only the last bar when the history is unchanged", () => {
  const a = [bar(0, "100"), bar(D, "101"), bar(2 * D, "102")];
  expect(planUpdate(null, a)).toEqual({ kind: "set" });
  expect(planUpdate(a, [...a.slice(0, 2), bar(2 * D, "105")])).toEqual({ kind: "update", bars: [bar(2 * D, "105")] });
  expect(planUpdate(a, [...a.slice(0, 2), bar(2 * D, "105"), bar(3 * D, "106")])).toEqual({ kind: "update", bars: [bar(2 * D, "105"), bar(3 * D, "106")] });
  expect(planUpdate(a, [bar(0, "99"), ...a.slice(1)])).toEqual({ kind: "set" });          // history changed
  expect(planUpdate(a, a.slice(1))).toEqual({ kind: "set" });                            // window moved
});

test("fills are arrows by side, decisions circles, no-decision squares", () => {
  const m = markers(OV, "1d", COLORS);
  expect(m.map((x) => [x.shape, x.position])).toEqual([["circle", "aboveBar"], ["arrowUp", "belowBar"], ["arrowDown", "aboveBar"], ["square", "aboveBar"]]);
  expect(m[1].time).toBe(1792368000);                         // floored to the 10-19 daily bar
  expect(m[1].text).toBe("ซื้อ 0.02411");
  expect(m[2].text).toBe("ขาย 0.02408 HALT");
  expect(markers(OV, "1h", COLORS)[0].time).toBe(1792396800);   // intraday: when the decision was made (08:00 bar)
});

test("HALT shading covers the bars that overlap a HALT period", () => {
  const h = 1792508400;   // 2026-10-20 15:00
  const bars = [bar(h - 3600, "1"), bar(h, "1"), bar(h + 3600, "1")];
  expect(haltBars(OV, bars, "1h", 1800000000)).toEqual([h]);
});

test("clicking a marker finds its fill and the decision behind it", () => {
  expect(lookup("fill:0", OV)?.decision?.day).toBe("2026-10-18");
  expect(lookup("decision:0", OV)?.fill?.qty).toBe("0.02411");
  expect(lookup("fill:1", OV)?.decision).toBeUndefined();
});

// --- the chart component (C1) --------------------------------------------------------------------
test("a refresh updates the last bar without reloading the chart", () => {
  const base = [bar(0, "100"), bar(D, "101")];
  const props = { indicators: null, hidden: new Set<string>(), overlays: null, timeframe: "1d" as const, onMarker: () => {}, onHover: () => {} };
  const r = render(<CandleChart bars={resp(base, bar(2 * D, "102"))} {...props} />);
  expect(calls.filter((c) => c.series === "Candlestick0").map((c) => c.op)).toEqual(["setData"]);
  calls.length = 0;
  r.rerender(<CandleChart bars={resp(base, bar(2 * D, "103"))} {...props} />);
  expect(calls.filter((c) => c.series === "Candlestick0").map((c) => c.op)).toEqual(["update"]);
  expect(charts).toBe(1);
});

// --- the panel (C5) -------------------------------------------------------------------------------
let api: Record<string, unknown> = {};
vi.mock("../src/api", () => ({
  supabase: null,
  apiGet: async (path: string) => {
    const k = Object.keys(api).find((p) => path.startsWith(p));
    if (!k) throw { code: "not_found", message: "no" };
    return api[k];
  },
}));

function show(initialBook = "") {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ChartPanel initialBook={initialBook} /></QueryClientProvider>);
}

test("market chart has no bot label; a bot's overlays come with its mode badge", async () => {
  api = { "/api/v1/charts/BTCUSDT/bars": resp([bar(0, "100")]), "/api/v1/charts/BTCUSDT/indicators": { set: "basic", series: [] },
    "/api/v1/bots/DEFAULT/tn-r10a-code-v1/overlays": OV };
  show();
  await waitFor(() => expect(screen.getByTestId("market-label")).toBeTruthy());
  expect(screen.queryByTestId("mode-badge")).toBeNull();
  cleanup();
  show("DEFAULT/tn-r10a-code-v1");
  await waitFor(() => expect(screen.getByTestId("mode-badge").textContent).toBe("TESTNET"));
  fireEvent.click(await screen.findByText(/▲ ซื้อ 0.02411/));
  expect(screen.getByTestId("marker-detail").textContent).toContain("OPEN");
});

test("overlays without a mode are not drawn", async () => {
  api = { "/api/v1/charts/BTCUSDT/bars": resp([bar(0, "100")]), "/api/v1/charts/BTCUSDT/indicators": { set: "basic", series: [] },
    "/api/v1/bots/DEFAULT/tn-r10a-code-v1/overlays": { ...OV, mode: undefined } };
  show("DEFAULT/tn-r10a-code-v1");
  await waitFor(() => expect(screen.getByTestId("market-label")).toBeTruthy());
  await act(async () => { await new Promise((r) => setTimeout(r, 50)); });
  expect(screen.queryByTestId("mode-badge")).toBeNull();
  expect(screen.queryByText(/▲ ซื้อ/)).toBeNull();
});
