import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { FuturesDesk, WorkerStatus } from "../src/api";
import { botName, deskAlerts, HomeSummary, workerAlerts } from "../src/HomeSummary";

const now = new Date().toISOString();
function worker(account: string, extra: Partial<WorkerStatus> = {}): WorkerStatus {
  return { account, mode: "TESTNET", updated_at: now, silent: false, safety: "RUNNING", execution_enabled: true, account_ok: true,
    clock: { signing_paused: false }, quote_age_ms: 100, reconcile: { verdict: "MATCH", checked_at: now, differences: [], open_orders: 0 },
    persist: { ok: true }, streams: {}, execution: { r10a: { book: "tn-r10a-v1.1", allocation: "2000", note: "" }, unresolved: [], last_error: null }, ...extra } as unknown as WorkerStatus;
}
const response = vi.hoisted(() => ({ workers: [] as WorkerStatus[], desk: { available: false, reason: "no_worker", accounts: [] } as FuturesDesk }));
vi.mock("../src/api", () => ({ apiGet: async (path: string) => {
  if (path === "/api/v1/workers") return { available: true, workers: response.workers };
  if (path === "/api/v1/futures/desk") return response.desk;
  if (path === "/api/v1/bots") return { bots: [{ book: "bot-efe97e9e", name: "BOTR10a v2" }], can_edit: false };
  if (path.endsWith("/activity")) return { available: true, orders: [], trades: [], pnl: { books: [{ book: "tn-r10a-v1.1", pnl_usdt: "12.5" }], balances: [] } };
  if (path.endsWith("/control")) return { available: true, decisions: [{ day: "2026-11-24", book: "tn-r10a-v1.1", action: "HOLD", weight: "1" }], risk_events: [], requests: [] };
  if (path === "/api/v1/paper-compare") return { from: "2026-11-17", books: [{ label: "pathA-r10a-hedge/paper", return: "0.01", as_of: "2026-11-24T23:59:59Z", benchmark: false }, { label: "pathA-r10a/paper", return: "-0.02", as_of: "2026-11-24T23:59:59Z", benchmark: false }, { label: "pathA-v1.1/buy_and_hold", return: "-0.05", as_of: "2026-11-24T23:59:59Z", benchmark: true }] };
  throw new Error(`unexpected ${path}`);
} }));
afterEach(cleanup);
function show() { return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><HomeSummary /></QueryClientProvider>); }

test("silent or halted workers are critical, other issues are warnings", () => {
  expect(workerAlerts(worker("A"))).toEqual([]);
  expect(workerAlerts(worker("A", { silent: true }))[0].level).toBe("critical");
  expect(workerAlerts(worker("A", { safety: "HALTED" }))[0].level).toBe("critical");
  expect(workerAlerts(worker("A", { quote_age_ms: 9000 })).map((a) => a.level)).toEqual(["warn"]);
});
test("a futures position without a stop is critical", () => {
  const desk = { available: true, unprotected: ["BTCUSDT"], caps: null, stale: false, worker: { last_error: null } } as unknown as FuturesDesk;
  expect(deskAlerts(desk)).toEqual([expect.objectContaining({ level: "critical", where: "Futures" })]);
  expect(deskAlerts(undefined)).toEqual([]);
});
test("bot names prefer the web bot name, then a short known name", () => {
  const bots = { bots: [{ book: "bot-efe97e9e", name: "BOTR10a v2" }], can_edit: false } as never;
  expect(botName("bot-efe97e9e", bots)).toBe("BOTR10a v2");
  expect(botName("tn-r10a-v1.1", undefined)).toBe("R10a v1.1");
  expect(botName(null, undefined)).toBe("ไม่มีบอทกลยุทธ์");
});
test("home shows one row per bot with its own P&L and position, and an all-clear strip", async () => {
  response.workers = [worker("DEFAULT"), worker("DEFAULT", { updated_at: "2020-01-01" })];
  show();
  expect(await screen.findByText("+12.50")).toBeTruthy();
  expect(screen.getAllByText("R10a v1.1")).toHaveLength(1);
  expect(await screen.findByText("ถือ BTC 100%")).toBeTruthy();
  expect(screen.getByTestId("home-alert").textContent).toContain("ทุกอย่างปกติ");
  expect(await screen.findByText("-2.00%".replace("-", "−"))).toBeTruthy();
});
test("problems show at the top with a link to where to fix them", async () => {
  response.workers = [worker("AI", { silent: true })];
  show();
  const strip = await screen.findByRole("alert");
  expect(strip.textContent).toContain("ต้องดูด่วน");
  expect(strip.querySelector("a")?.getAttribute("href")).toBe("#system");
});
