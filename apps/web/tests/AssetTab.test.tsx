import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { AssetStatus, PortfolioRow } from "../src/api";
import { AssetTab } from "../src/AssetTab";

let asset: AssetStatus;
vi.mock("../src/api", () => ({ supabase: null, apiGet: async () => asset }));

class FakeSocket {
  static last: FakeSocket | null = null;
  url: string;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  constructor(url: string) { this.url = url; FakeSocket.last = this; }
  close() {}
}
vi.stubGlobal("WebSocket", FakeSocket);
afterEach(() => { cleanup(); FakeSocket.last = null; });

const info = { symbol: "BTCUSDT", base: "BTC", name: "Bitcoin", live_stream: "btcusdt@miniTicker" };
const tf = (timeframe: "1h" | "4h" | "1d", state: "up" | "down" | "mixed" | null) => ({ timeframe, bar_open: "2026-10-15T00:00:00+00:00", close: "82195.40", ema20: "81906.14", ema50: "82089.35", state });
const row = (label: string, exposure_share: string | null): PortfolioRow => ({
  label, mode: "SHADOW", currency: "USDT", initial_equity: "10000", as_of: null, trading_equity: "10500", economic_equity: "10500",
  high_water_mark: null, quality: null, stale: false, return: "0.05", drawdown: "0", exposure_share,
});

function show(rows: PortfolioRow[] = []) {
  return render(<QueryClientProvider client={new QueryClient()}><AssetTab rows={rows} /></QueryClientProvider>);
}

test("shows trend per timeframe, the SMA100 line, market risk and which books hold BTC", async () => {
  asset = {
    ...info, available: true, data_through: "2026-10-15", stale: false, close: "82195.40",
    change: { d1: "0.006785", d7: "-0.005928", d30: "0.087242" }, range_30d: { high: "87385.10", low: "75025.00" },
    regime: { sma100: "73651.48", dist_to_sma: "0.116005", trend_up: true },
    timeframes: [tf("1h", "mixed"), tf("4h", "down"), tf("1d", null)],
    funding: { per_8h_7d: "0.000016", hot: false, estimated: true }, vol_30d: "0.374318", drawdown_365d: "0.282122",
    open_interest: { usdt: "7755582544.50", change_7d: "0.020953" }, events: ["ราคาปิดหลุดค่าเฉลี่ย 100 วัน"], missing_inputs: [],
  };
  show([row("pathA-v1.2/paper", "1"), row("pathA-v1.2/cash", null)]);
  const tiles = await screen.findAllByTestId("trend-tile");
  expect(tiles.map((t) => t.textContent)).toEqual([
    expect.stringContaining("ไม่ชัด"), expect.stringContaining("ขาลง"), expect.stringContaining("ข้อมูลไม่พอ"),
  ]);
  expect(screen.getByTestId("regime").textContent).toContain("เหนือ");
  expect(screen.getByText("0.0016% / 8 ชม.")).toBeTruthy();
  expect(screen.getByText("7.76 พันล้าน USDT")).toBeTruthy();
  expect(screen.getByTestId("asset-events").textContent).toContain("หลุดค่าเฉลี่ย");
  expect(screen.queryByTestId("asset-stale")).toBeNull();
  const books = within(screen.getByTestId("asset-books")).getAllByRole("row");
  expect(books).toHaveLength(2);       // header + the one book holding BTC
  expect(screen.getByText(/ไม่ใช่สัญญาณซื้อขาย/)).toBeTruthy();
});

test("live price comes from the browser's Binance stream and is display only", async () => {
  asset = { ...info, available: false, loading: true };
  show();
  expect(await screen.findByTestId("asset-loading")).toBeTruthy();
  expect(FakeSocket.last!.url).toBe("wss://stream.binance.com:9443/ws/btcusdt@miniTicker");
  act(() => FakeSocket.last!.onmessage!({ data: JSON.stringify({ e: "24hrMiniTicker", E: 1, s: "BTCUSDT", c: "83000.12000000", o: "82000.00000000" }) }));
  const live = screen.getByTestId("live-price");
  expect(live.textContent).toContain("83,000.12");
  expect(live.textContent).toContain("+1.22%");
  expect(live.textContent).toContain("ใช้ดูอย่างเดียว");
  act(() => FakeSocket.last!.onclose!());
  expect(screen.getByTestId("live-price").textContent).toContain("ขาดการเชื่อมต่อ");
});
