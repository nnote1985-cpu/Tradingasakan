import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import type { WorkersResponse, WorkerStatus } from "../src/api";
import { WorkerTab } from "../src/WorkerTab";

const response: { current: WorkersResponse } = { current: { available: false, workers: [] } };
vi.mock("../src/api", () => ({ supabase: null, apiGet: async () => response.current }));

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
  expect(screen.getByText(/หลุด/)).toBeTruthy();
  expect(screen.getByText(/was not placed by the bot/)).toBeTruthy();
  expect(screen.getByText(/USDT 9000 \(ล็อก 1000\)/)).toBeTruthy();
  expect(screen.getByText(/รวม 412 สินทรัพย์/)).toBeTruthy();
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
