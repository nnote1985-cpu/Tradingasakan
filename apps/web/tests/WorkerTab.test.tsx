import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import type { WorkerActivity, WorkersResponse, WorkerStatus } from "../src/api";
import { WorkerTab } from "../src/WorkerTab";
import { workerIssues } from "../src/Operations";

const response: { current: WorkersResponse } = { current: { available: false, workers: [] } };
const activity: { current: WorkerActivity } = { current: { available: true, orders: [], trades: [], pnl: null } };
vi.mock("../src/api", () => ({
  supabase: null,
  apiGet: async (path: string) => (path.endsWith("/activity") ? activity.current : response.current),
}));

const now = new Date().toISOString();
const worker = (over: Partial<WorkerStatus> = {}): WorkerStatus => ({
  account: "DEFAULT", owner: "PC:1", host: "PC", started_at: now, last_tick_at: now, updated_at: now, silent: false,
  mode: "TESTNET", execution_enabled: true, safety: "RECOVERING", lease_token: 3,
  clock: { offset_ms: 25, status: "OK", signing_paused: false }, account_ok: true, deadman_ok: true,
  streams: { klines: { connected: true, connects: 1, silent_ms: 900, error: null }, account: { connected: false, connects: 2, silent_ms: null, error: "timeout" } },
  quote_age_ms: 200, bars: { last_1m: 1792227600000, last_1d: 1792195200000, gaps: 0, revisions: 0 },
  persist: { ok: true, pending: 0, epoch: 1 }, execution: null, epoch: { epoch: 1, opened_at: now },
  reconcile: { verdict: "DISCREPANCY", checked_at: now, differences: ["order 9 (web_1) was not placed by the bot"], open_orders: 1 },
  balances: [{ asset: "BTC", free: "1", locked: "0" }, { asset: "USDT", free: "9000", locked: "1000" }], asset_count: 412, trades: 3,
  ...over,
});

function show() {
  render(<QueryClientProvider client={new QueryClient()}><WorkerTab /></QueryClientProvider>);
}

test("before the P3 migration the tab says the table is missing", async () => {
  response.current = { available: false, workers: [] };
  show();
  expect(await screen.findByTestId("workers-unavailable")).toBeTruthy();
});

test("a running worker shows clock, streams, reconcile differences and balances", async () => {
  response.current = { available: true, workers: [worker()] };
  show();
  expect(await screen.findByTestId("worker-card")).toBeTruthy();
  expect(screen.getByText("ทำงานอยู่")).toBeTruthy();
  expect(screen.getByText(/\+25 ms/)).toBeTruthy();
  expect(screen.getAllByText(/หลุด/).length).toBeGreaterThan(0);
  expect(screen.getByText(/was not placed by the bot/)).toBeTruthy();
  expect(screen.getByText(/USDT 9000 \(ล็อก 1000\)/)).toBeTruthy();
  expect(screen.getByText(/รวม 412 สินทรัพย์/)).toBeTruthy();
});

test("a recent heartbeat cannot hide stale reconciliation or unknown persistence", () => {
  const w = worker({ safety: "RUNNING", streams: {}, execution_enabled: true,
    reconcile: { verdict: "MATCH", checked_at: new Date(Date.now() - 120000).toISOString(), differences: [], open_orders: 0 },
    persist: { ok: null, pending: 0, epoch: 1 } });
  expect(workerIssues(w)).toContain("ผลตรวจบัญชีเกิน 1 นาทีหรือยังยืนยันเวลาไม่ได้");
  expect(workerIssues(w)).toContain("ยังไม่ยืนยันการบันทึกข้อมูล");
  expect(workerIssues({ ...w, reconcile: { ...w.reconcile!, checked_at: new Date().toISOString() }, persist: { ...w.persist!, ok: true } })).toEqual([]);
});

test("a silent worker is flagged with what to do", async () => {
  response.current = { available: true, workers: [worker({ silent: true })] };
  show();
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(screen.getByText("ไม่ตอบ")).toBeTruthy();
});

test("execution shows today's orders, unknown orders and the conformance cycle", async () => {
  const execution = {
    safety: "SOFT_PAUSED", open_orders: 1, waiting: 0, orders_today: 4, cancels_today: 1, last_error: null,
    unresolved: [{ client_order_id: "tak-abc", state: "RECONCILING", for_s: 42 }],
    counts: { sent: 4, filled: 2, canceled: 1, rejected: 0, expired: 0, unknown: 1 },
    conformance: { book: "tn-conformance-v1", cycle: "20261017T0800Z", cycles_done: 1, note: "limit-near: acknowledged" },
  };
  response.current = { available: true, workers: [worker({ execution })] };
  show();
  expect(await screen.findByText(/วันนี้ส่ง 4/)).toBeTruthy();
  expect(screen.getByText(/tak-abc RECONCILING มา 42 วินาที/)).toBeTruthy();
  expect(screen.getByText(/20261017T0800Z · limit-near: acknowledged · ครบ 1 รอบ/)).toBeTruthy();
});

test("the card lists recent Testnet trades and orders", async () => {
  activity.current = {
    available: true,
    pnl: {
      epoch: 1, opened_at: now, mark: "81713.67", mark_source: "quote", equity_change_usdt: "-0.0257",
      balances: [{ asset: "BTC", opening: "1", now: "1.00001984", change: "0.00001984" },
                 { asset: "USDT", opening: "10000", now: "9998.35318072", change: "-1.64681928" }],
      books: [{ book: "tn-conformance-v1", buys: 2, sells: 2, btc_net: "0.00001984", usdt_net: "-1.64681928", bnb_fees: "0",
                volume: "24.52", pnl_usdt: "-0.0257" }],
    },
    trades: [{ traded_at: now, epoch: 1, trade_id: 77, side: "SELL", qty: "0.00007", price: "106512.3", quote_qty: "7.45586",
               commission: "0.00745586", commission_asset: "USDT", client_order_id: "tak-s", book: "tn-conformance-v1", leg: "mkt-sell" }],
    orders: [{ client_order_id: "tak-f", book: "tn-conformance-v1", leg: "limit-far", side: "BUY", type: "LIMIT", qty: "0.00008",
               limit_price: "101000", created_at: now, state: "CANCELED", filled_qty: "0", updated_at: now, reason: "cancel", avg_price: null }],
  };
  response.current = { available: true, workers: [worker()] };
  show();
  const trades = await screen.findByTestId("worker-trades");
  const pnl = screen.getByTestId("worker-pnl").textContent ?? "";
  expect(pnl).toMatch(/รอบบัญชี #1/);
  expect(pnl).toMatch(/-0\.0257 USDT/);
  expect(pnl).toMatch(/USDT.*10000.*9998\.35318072.*-1\.64681928/);
  expect(pnl).toMatch(/tn-conformance-v1.*2\/2.*24\.52.*\+0\.00001984/);
  expect(trades.textContent).toMatch(/ขาย.*0\.00007.*106512\.3.*7\.45586.*0\.00745586 USDT.*tn-conformance-v1 · mkt-sell/);
  expect(screen.getByTestId("worker-orders").textContent).toMatch(/limit-far.*ซื้อ.*LIMIT.*0\.00008.*101000.*ยกเลิก/);
});
