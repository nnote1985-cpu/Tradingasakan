import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { FuturesDesk as Desk } from "../src/api";
import { FuturesDesk, held } from "../src/futures/FuturesDesk";

let reply: Desk;
const calls: string[] = [];
vi.mock("../src/api", () => ({ supabase: null, apiGet: async (path: string) => { calls.push(path); return reply; } }));
afterEach(() => { cleanup(); calls.length = 0; });

const cap = (level: "OK" | "NEAR" | "BLOCKED", loss: string, used: string) => ({ loss, cap: "0.03", used, level, room: "250" });
const READY: Desk = {
  available: true, accounts: ["FUT1"], account: "FUT1", venue: "BINANCE_USDM_DEMO", synced_at: "2026-11-18T01:00:00+00:00", stale: false,
  worker: { orders_enabled: false, last_error: null, started_at: null, host: "vm" },
  equity: "10050", wallet_balance: "10000", available_balance: "8600", unrealized_pnl: "50",
  rules: { risk_per_trade: "0.01", day_loss_cap: "0.03", total_loss_cap: "0.1", symbols: ["BTCUSDT", "ETHUSDT"] },
  caps: { day: { ...cap("OK", "0", "0"), day_open: "10000", day: "2026-11-18" }, total: { ...cap("NEAR", "0.075", "0.75"), cap: "0.1", hwm: "10860", hwm_at: "" }, blocked: false, reasons: [] },
  start_equity: "10000", started_at: null,
  positions: [
    { symbol: "BTCUSDT", side: "LONG", qty: "0.05", entry_price: "79000", break_even_price: "79031.6", mark_price: "80000", liquidation_price: "63500.5",
      unrealized_pnl: "50", notional: "4000", margin: "1350", margin_type: "ISOLATED", leverage: 3, updated_at: null,
      stop_price: "77000", take_profit: null, has_stop: true, loss_at_stop: "150", loss_at_stop_pct: "0.0149", initial_risk: "100",
      initial_risk_pct: "0.01", over_rule: false, liq_distance_pct: "0.2062", stop_before_liq: true },
    { symbol: "ETHUSDT", side: "SHORT", qty: "1", entry_price: "3000", break_even_price: "3000", mark_price: "3010", liquidation_price: null,
      unrealized_pnl: "-10", notional: "3010", margin: "300", margin_type: "CROSS", leverage: 10, updated_at: null,
      stop_price: null, take_profit: null, has_stop: false },
  ],
  orders: [{ source: "algo", id: "7", symbol: "BTCUSDT", side: "SELL", type: "STOP_MARKET", kind: "SL", price: "0", trigger_price: "77000", qty: "0",
    close_position: true, reduce_only: false, created_at: null }],
  unprotected: ["ETHUSDT"], markets: {},
  trades: [
    { symbol: "BTCUSDT", side: "LONG", opened_at: "2026-11-18T00:50:00+00:00", closed_at: null, open: true, qty: "0.05", entry_price: "79000",
      exit_price: null, exit_qty: "0", realized_pnl: "0", fees: "1.58", funding: "0", net_pnl: "-1.58", return_pct: "-0.0002", fills: 1, order_ids: [5] },
    { symbol: "BTCUSDT", side: "SHORT", opened_at: "2026-11-17T10:00:00+00:00", closed_at: "2026-11-17T12:30:00+00:00", open: false, qty: "0.02",
      entry_price: "81000", exit_price: "80000", exit_qty: "0.02", realized_pnl: "20", fees: "1", funding: "-0.5", net_pnl: "18.5", return_pct: "0.0018", fills: 2, order_ids: [1, 2] },
  ],
  summary: { count: 2, closed: 1, open: 1, wins: 1, losses: 0, win_rate: "1", net_pnl: "18.5", fees: "2.58", funding: "-0.5", today_net: "0", avg_win: "18.5", avg_loss: null },
};

function renderDesk() {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><FuturesDesk /></QueryClientProvider>);
}

test("shows positions with stop, liquidation and risk, flags the one without a stop", async () => {
  reply = READY;
  renderDesk();
  const cards = await screen.findAllByTestId("position");
  expect(calls[0]).toBe("/api/v1/futures/desk");
  expect(cards[0].textContent).toContain("77,000.00");
  expect(cards[0].textContent).toContain("63,500.50");
  expect(cards[0].textContent).toContain("ห่าง 20.62%");
  expect(cards[0].textContent).toContain("100.00 (1.00%)");
  expect(cards[0].textContent).toContain("เสีย 150.00");
  expect(cards[1].textContent).toContain("ไม่มี Stop-loss บน exchange");
  expect(screen.getAllByRole("alert").some((a) => a.textContent?.includes("Position ที่ไม่มี Stop-loss: ETHUSDT"))).toBe(true);
  expect(screen.getByTestId("cap-ขาดทุนรวม").textContent).toContain("ใกล้เพดาน");
  expect(screen.getByTestId("cap-ขาดทุนวันนี้").textContent).toContain("ปกติ");
  expect(screen.getByText(/อ่านอย่างเดียว/)).toBeTruthy();
});

test("per-trade log: summary, open trade marked, filter to closed", async () => {
  reply = READY;
  renderDesk();
  const table = await screen.findByTestId("trades");
  const rows = [...table.querySelectorAll("tbody tr")].map((r) => r.textContent ?? "");
  expect(rows).toHaveLength(2);
  expect(rows[0]).toContain("ยังเปิดอยู่");
  expect(rows[1]).toContain("18.50");
  expect(rows[1]).toContain("2 ชม. 30 นาที");
  expect(screen.getByTestId("trade-summary").textContent).toContain("100.00%");
  fireEvent.click(screen.getByLabelText("เฉพาะไม้ที่ปิดแล้ว"));
  expect(table.querySelectorAll("tbody tr")).toHaveLength(1);
});

test("explains what is missing before the worker runs", async () => {
  reply = { available: false, reason: "no_worker", accounts: [] };
  renderDesk();
  expect((await screen.findByTestId("not-open")).textContent).toContain("demo.binance.com");
});

test("blocked caps show the reason", async () => {
  reply = { ...READY, unprotected: [], caps: { ...READY.caps!, blocked: true, reasons: ["วันนี้ขาดทุน 3.10% ถึงเพดาน 3.00%"] } } as Desk;
  renderDesk();
  expect((await screen.findAllByRole("alert")).some((a) => a.textContent?.includes("ห้ามเปิดไม้ใหม่: วันนี้ขาดทุน 3.10%"))).toBe(true);
});

test("held formats minutes, hours and days", () => {
  const t = "2026-11-18T00:00:00Z";
  expect(held(t, "2026-11-18T00:45:00Z")).toBe("45 นาที");
  expect(held(t, "2026-11-19T03:00:00Z")).toBe("1 วัน 3 ชม.");
});
