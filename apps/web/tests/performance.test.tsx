import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { Overview, WorkerActivity, WorkersResponse, WorkerStatus } from "../src/api";
import { accountEquity, difference, pnlTotals, sumAmounts, uniqueAccounts } from "../src/dashboardMetrics";
import { PerformanceOverview } from "../src/PerformanceOverview";

const response = vi.hoisted(() => ({ workers: { available: true, workers: [] } as WorkersResponse, activities: {} as Record<string, WorkerActivity>, overview: { portfolios: [] } as unknown as Overview }));
vi.mock("../src/api", () => ({ apiGet: async (path: string) => {
  if (path === "/api/v1/workers") return response.workers;
  if (path === "/api/v1/overview") return response.overview;
  const result = response.activities[path];
  if (!result) throw new Error("Missing account");
  return result;
} }));
afterEach(cleanup);

const now = new Date().toISOString();
function worker(account: string, mode = "TESTNET"): WorkerStatus {
  return { account, mode, updated_at: now, silent: false, safety: "RUNNING", execution_enabled: true, account_ok: true,
    clock: { signing_paused: false }, quote_age_ms: 100, reconcile: { verdict: "MATCH", checked_at: now, differences: [], open_orders: 0 },
    persist: { ok: true }, streams: {}, execution: null } as unknown as WorkerStatus;
}
function activity(profit = "12.5"): WorkerActivity {
  return { available: true, trades: [], orders: [], pnl: {
    epoch: 1, opened_at: now, mark: "80000", mark_source: "quote", equity_change_usdt: "999",
    balances: [{ asset: "USDT", opening: "1000", now: "1000", change: "0" }, { asset: "BTC", opening: "0.1", now: "0.1", change: "0" }],
    books: [{ book: "trend", buys: 2, sells: 1, btc_net: "0", usdt_net: profit, bnb_fees: "0", volume: "100", pnl_usdt: profit },
      { book: "tn-conformance-v1", buys: 1, sells: 1, btc_net: "0", usdt_net: "500", bnb_fees: "0", volume: "10", pnl_usdt: "500" },
      { book: null, buys: 0, sells: 1, btc_net: "0", usdt_net: "700", bnb_fees: "0", volume: "10", pnl_usdt: "700" }],
  } };
}
function show() { return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><PerformanceOverview /></QueryClientProvider>); }

test("display totals preserve decimal precision and never turn missing values into zero", () => {
  expect(sumAmounts(["0.1", "0.2", "-0.00000001"])).toBe("0.29999999");
  expect(sumAmounts(["9999999999999999.99", "0.01"])).toBe("10000000000000000.00");
  expect(sumAmounts(["100", null])).toBeNull();
  expect(sumAmounts([])).toBeNull();
  expect(difference("9999.99", "10000")).toBe("-0.01");
  expect(pnlTotals(["10", "-4", "2"])).toEqual({ net: "8", gains: "12", losses: "-4" });
  expect(pnlTotals(["10", null])).toEqual({ net: null, gains: null, losses: null });
});
test("account equity values BTC at the supplied mark and requires all balances", () => {
  const p = activity().pnl!;
  expect(Number(accountEquity(p))).toBe(9000);
  expect(accountEquity({ ...p, mark: null })).toBeNull();
  expect(accountEquity({ ...p, balances: [p.balances[0]] })).toBeNull();
  expect(accountEquity({ ...p, mark: null, balances: [p.balances[0], { ...p.balances[1], now: "0" }] })).toBe("1000");
  expect(accountEquity(null)).toBeNull();
});
test("duplicate account reports keep only the latest snapshot", () => {
  const latest = worker("A");
  const old = { ...latest, updated_at: "2020-01-01" };
  expect(uniqueAccounts([old, latest])).toEqual([latest]);
});
test("account totals exclude conformance, outside trades, and other money modes", async () => {
  response.workers = { available: true, workers: [worker("A"), worker("A")] };
  response.activities = { "/api/v1/workers/A/activity": activity() };
  const { container } = show();
  await screen.findByText("trend");
  const metrics = container.querySelector(".performance-metrics")!;
  expect(metrics.textContent).toContain("9,000.00");
  expect(metrics.textContent).toContain("+12.50");
  expect(metrics.textContent).not.toContain("999.00");
  expect(metrics.textContent).not.toContain("500.00");
  expect(screen.getAllByText("trend")).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "เงินจริง" }));
  expect(screen.queryByText("trend")).toBeNull();
  expect(screen.getByText("ยังไม่มีบอตรายงานสถานะ")).toBeTruthy();
});
test("one failed account makes aggregate metrics unavailable and exposes a retry", async () => {
  response.workers = { available: true, workers: [worker("A"), worker("B")] };
  response.activities = { "/api/v1/workers/A/activity": activity() };
  const { container } = show();
  await screen.findByRole("alert");
  expect([...container.querySelectorAll(".performance-metrics dd")].map((el) => el.textContent)).toEqual(["—", "—", "—", "—"]);
  expect(screen.getByRole("button", { name: "ลองใหม่" })).toBeTruthy();
});
test("an account with only conformance results shows balances rather than empty strategy profits", async () => {
  response.workers = { available: true, workers: [worker("A")] };
  const data = activity();
  data.pnl!.books = data.pnl!.books.filter((b) => b.book === "tn-conformance-v1");
  response.activities = { "/api/v1/workers/A/activity": data };
  const { container } = show();
  await screen.findByText("เงินสด USDT");
  expect([...container.querySelectorAll(".performance-metrics dd")].map((el) => el.textContent)).toEqual(["9,000.00", "1,000.00", "8,000.00", "+999.00"]);
  expect(screen.queryByText("กำไรรวม · USDT")).toBeNull();
  expect(screen.getByText(/ยอดเปลี่ยนยังไม่ใช่กำไรของกลยุทธ์/)).toBeTruthy();
});
test("simulated totals exclude benchmarks and show net profit after costs", async () => {
  response.workers = { available: true, workers: [] };
  const row = { label: "exp/code", mode: "SHADOW", currency: "USDT", initial_equity: "10000", economic_equity: "11000", trading_equity: "11100", as_of: "2026-10-18", return: "0.1", drawdown: "0.02", stale: false };
  response.overview = { mode: "SHADOW", live_enabled: false, portfolios: [row, { ...row, label: "exp/buy_and_hold", economic_equity: "20000" }] } as Overview;
  const { container } = show();
  fireEvent.click(screen.getByRole("button", { name: "พอร์ตจำลอง" }));
  await screen.findByText("exp/code");
  const metrics = container.querySelector(".performance-metrics")!;
  expect(metrics.textContent).toContain("11,000.00");
  expect(metrics.textContent).toContain("+1,000.00");
  expect(screen.queryByText("exp/buy_and_hold")).toBeNull();
  fireEvent.click(screen.getByRole("checkbox", { name: "แสดงตัวเปรียบเทียบ" }));
  expect(screen.getByText("exp/buy_and_hold")).toBeTruthy();
  expect(metrics.textContent).not.toContain("31,000.00");
});

test("Spot and Futures simulations have separate tables, totals and benchmarks", async () => {
  response.workers = { available: true, workers: [] };
  const row = { mode: "SHADOW", currency: "USDT", initial_equity: "10000", economic_equity: "11000", trading_equity: "11000", as_of: "2026-10-18", return: "0.1", drawdown: "0.02", stale: false };
  response.overview = { mode: "SHADOW", live_enabled: false, portfolios: [
    { ...row, label: "exp/code" },
    { ...row, label: "exp/buy_and_hold", economic_equity: "30000" },
    { ...row, label: "futures-r7/code", economic_equity: "9500", return: "-0.05" },
    { ...row, label: "futures-r7/buy_and_hold", economic_equity: "20000" },
  ] } as Overview;
  const { container } = show();
  fireEvent.click(screen.getByRole("button", { name: "พอร์ตจำลอง" }));
  await screen.findByText("exp/code");
  expect(screen.queryByText("futures-r7/code")).toBeNull();
  expect(container.querySelector(".performance-metrics")!.textContent).toContain("11,000.00");
  fireEvent.click(screen.getByRole("button", { name: "Futures 1 พอร์ต" }));
  expect(screen.queryByText("exp/code")).toBeNull();
  expect(screen.getByText("futures-r7/code")).toBeTruthy();
  const metrics = container.querySelector(".performance-metrics")!;
  expect(metrics.textContent).toContain("9,500.00");
  expect(metrics.textContent).toContain("-500.00");
  expect(metrics.textContent).not.toContain("11,000.00");
  fireEvent.click(screen.getByRole("checkbox", { name: "แสดงตัวเปรียบเทียบ" }));
  expect(screen.getByText("futures-r7/buy_and_hold")).toBeTruthy();
  expect(screen.queryByText("exp/buy_and_hold")).toBeNull();
  expect(metrics.textContent).not.toContain("29,500.00");
  fireEvent.click(screen.getByRole("button", { name: "Spot 1 พอร์ต" }));
  expect(screen.getByText("exp/code")).toBeTruthy();
  expect(screen.queryByText("futures-r7/code")).toBeNull();
});
