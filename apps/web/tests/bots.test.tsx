import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { BotsPanel } from "../src/bots/BotsPanel";
import type { Bot, BotConfig } from "../src/bots/types";
import { configDiff, stepOf } from "../src/bots/types";

const TEMPLATE = { template: "tn-r10a-code-v2", version: "tn-r10a-code-v2", name_th: "R10a v2", market: "SPOT", symbol: "BTCUSDT", timeframes: ["1d"],
  summary_th: "band", params: {}, allocation: { asset: "USDT", min: "100", max: "2000" } };
const ACCOUNTS = [
  { account: "BOT1", usdt_free: "10000", balance_as_of: "2026-11-15T06:00:00Z", reconcile: "MATCH", available: true, reason_th: null },
  { account: "DEFAULT", usdt_free: "8000", balance_as_of: "2026-11-15T06:00:00Z", reconcile: "MATCH", available: false, reason_th: "บัญชี DEFAULT รันกลยุทธ์ tn-r10a-code-v1 ที่เปิดด้วย env บน worker อยู่" },
];
function conf(over: Partial<BotConfig> = {}): BotConfig {
  return { schema_version: "bot-config-v1", name: "R10a BOT1", account: "BOT1", venue: "SPOT_TESTNET", instrument: { symbol: "BTCUSDT", market: "SPOT" },
    strategy: { template: "tn-r10a-code-v2", version: "tn-r10a-code-v2", timeframe: "1d", params: {} }, allocation: { asset: "USDT", amount: "2000.00" },
    risk_profile: "spot.tn.v1", futures: null, mode: "SHADOW", source_draft: null, notes: "", ...over };
}
function bot(over: Partial<Bot> = {}): Bot {
  return { bot_id: "b1", book: "bot-b1", account: "BOT1", name: "R10a BOT1", status: "submitted",
    latest: { bot_id: "b1", revision: 1, account: "BOT1", config: conf(), hash: "h", saved_by: "u", saved_at: "2026-11-15T06:00:00Z", submitted: true, verdict: null },
    approved: null, running: null, retired: null, ...over };
}
const mock = vi.hoisted(() => ({ post: vi.fn(), bots: [] as unknown[], canEdit: true, drafts: [] as unknown[] }));
vi.mock("../src/api", () => ({
  apiPost: (...args: unknown[]) => mock.post(...args),
  apiGet: async (path: string) => {
    if (path === "/api/v1/bots") return { bots: mock.bots, can_edit: mock.canEdit };
    if (path === "/api/v1/bot-templates") return { templates: [TEMPLATE] };
    if (path === "/api/v1/bot-accounts") return { accounts: ACCOUNTS };
    if (path === "/api/v1/bot-profile-drafts") return { items: mock.drafts, can_edit: true };
    throw new Error(path);
  },
}));
beforeEach(() => {
  mock.bots = []; mock.canEdit = true; mock.drafts = [];
  mock.post.mockReset().mockImplementation(async (path: string, body: { config?: BotConfig; expected_revision?: number }) =>
    path === "/api/v1/bots" ? { bot_id: "b1", revision: (body.expected_revision ?? 0) + 1, account: "BOT1", config: body.config, hash: "h", saved_by: "u", saved_at: "x" } : {});
});
afterEach(cleanup);
function show() { return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><BotsPanel /></QueryClientProvider>); }
const next = () => fireEvent.click(screen.getByRole("button", { name: "ถัดไป" }));

test("wizard walks four steps, refuses a busy account, then saves and submits", async () => {
  show();
  fireEvent.click(await screen.findByRole("button", { name: "สร้างบอท" }));
  fireEvent.change(screen.getByLabelText("ชื่อบอท"), { target: { value: "R10a BOT1" } });
  const account = screen.getByLabelText(/บัญชี Testnet/) as HTMLSelectElement;
  await waitFor(() => expect(account.options.length).toBe(3));
  const busy = [...account.options].find((o) => o.value === "DEFAULT")!;
  expect(busy.disabled).toBe(true);
  expect(busy.textContent).toContain("เปิดด้วย env");
  fireEvent.change(account, { target: { value: "BOT1" } });
  expect((screen.getByLabelText(/ตลาด/) as HTMLSelectElement).querySelector("option[value=PERP]")!.hasAttribute("disabled")).toBe(true);
  next(); expect(screen.getByText(/ไม่มีค่าให้ปรับ/)).toBeTruthy();
  next(); fireEvent.change(screen.getByLabelText(/เงินของบอท/), { target: { value: "1500" } });
  next(); expect(screen.getByText("1500 USDT")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "บันทึกและส่งอนุมัติ" }));
  await waitFor(() => expect(mock.post).toHaveBeenCalledTimes(2));
  const [path, body] = mock.post.mock.calls[0];
  expect(path).toBe("/api/v1/bots");
  expect(body.config).toMatchObject({ name: "R10a BOT1", account: "BOT1", mode: "SHADOW", allocation: { amount: "1500" }, venue: "SPOT_TESTNET" });
  expect(mock.post.mock.calls[1][0]).toBe("/api/v1/bots/b1/revisions/1/submit");
  expect(await screen.findByText(/ส่งอนุมัติแล้ว/)).toBeTruthy();
});

test("a server field error opens its step, shows under the field and takes focus", async () => {
  mock.post.mockRejectedValueOnce({ code: "over_free_balance", message: "x", retryable: false, request_id: null,
    errors: [{ field: "allocation.amount", code: "over_free_balance", message_th: "เงินของบอทมากกว่า USDT ที่ว่าง" }] });
  show();
  fireEvent.click(await screen.findByRole("button", { name: "สร้างบอท" }));
  fireEvent.change(screen.getByLabelText("ชื่อบอท"), { target: { value: "x" } });
  await waitFor(() => expect((screen.getByLabelText(/บัญชี Testnet/) as HTMLSelectElement).options.length).toBe(3));
  fireEvent.change(screen.getByLabelText(/บัญชี Testnet/), { target: { value: "BOT1" } });
  fireEvent.click(screen.getByRole("button", { name: "บันทึกร่าง" }));
  const err = await screen.findByText("เงินของบอทมากกว่า USDT ที่ว่าง");
  await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText(/เงินของบอท/)));
  expect(err.closest("[data-field]")!.getAttribute("data-field")).toBe("allocation.amount");
});

test("local check stops an empty name before calling the API", async () => {
  show();
  fireEvent.click(await screen.findByRole("button", { name: "สร้างบอท" }));
  fireEvent.click(screen.getByRole("button", { name: "บันทึกร่าง" }));
  expect(await screen.findByText("ต้องตั้งชื่อบอท")).toBeTruthy();
  expect(document.activeElement).toBe(screen.getByLabelText("ชื่อบอท"));
  expect(mock.post).not.toHaveBeenCalled();
});

test("approve asks in a dialog that says what will happen; Escape cancels", async () => {
  mock.bots = [bot()];
  show();
  fireEvent.click(await screen.findByRole("button", { name: "อนุมัติ…" }));
  const dialog = screen.getByRole("dialog");
  expect(dialog.textContent).toContain("worker ของบัญชี BOT1");
  expect(dialog.textContent).toContain("โดยไม่ส่งคำสั่ง");
  expect(document.activeElement?.textContent).toBe("ยกเลิก");
  fireEvent.keyDown(window, { key: "Escape" });
  expect(screen.queryByRole("dialog")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "อนุมัติ…" }));
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "อนุมัติ" }));
  await waitFor(() => expect(mock.post).toHaveBeenCalledWith("/api/v1/bots/b1/revisions/1/approve", { note: "" }));
});

test("the running revision comes from the worker's heartbeat", async () => {
  mock.bots = [bot({ status: "running", approved: { revision: 1, hash: "h", actor: "u", at: "2026-11-15T06:00:00Z" },
    running: { revision: 1, hash: "h", mode: "SHADOW", pending_revision: 2, note: "ยังมีสถานะเปิด", safety: "RUNNING", reported_at: "x" } })];
  show();
  expect((await screen.findByTestId("bot")).textContent).toContain("worker รายงาน rev 1 (SHADOW, RUNNING)");
  expect(screen.getByText(/revision 2 รอ: ยังมีสถานะเปิด/)).toBeTruthy();
  expect(screen.getByText("กำลังรัน")).toBeTruthy();
});

test("editing shows a diff with the previous revision", async () => {
  mock.bots = [bot()];
  show();
  fireEvent.click(await screen.findByRole("button", { name: "แก้ไข" }));
  expect((screen.getByLabelText(/บัญชี Testnet/) as HTMLSelectElement).disabled).toBe(true);
  next(); next();
  fireEvent.change(screen.getByLabelText(/โหมด/), { target: { value: "TESTNET" } });
  next();
  const rows = screen.getAllByTestId("diff-row");
  expect(rows.map((r) => r.textContent)).toEqual(["modeSHADOWTESTNET"]);
  fireEvent.click(screen.getByRole("button", { name: "บันทึกร่าง" }));
  await waitFor(() => expect(mock.post.mock.calls[0][1]).toMatchObject({ bot_id: "b1", expected_revision: 1 }));
});

test("an old draft profile converts into a bot form that links back", async () => {
  mock.drafts = [{ profile_id: "p1", revision: 3, profile: { name: "MACD recipe", notes: "from research" }, saved_at: "x", saved_by: "u", hash: "h", execution_supported: false }];
  show();
  const pick = await screen.findByLabelText("เริ่มจากร่าง Bot profile");
  await waitFor(() => expect((pick as HTMLSelectElement).options.length).toBe(2));
  fireEvent.change(pick, { target: { value: "p1" } });
  expect((screen.getByLabelText("ชื่อบอท") as HTMLInputElement).value).toBe("MACD recipe");
  expect(screen.getByText(/คัดมาเฉพาะชื่อและหมายเหตุ/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText(/บัญชี Testnet/), { target: { value: "BOT1" } });
  fireEvent.click(screen.getByRole("button", { name: "บันทึกร่าง" }));
  await waitFor(() => expect(mock.post.mock.calls[0][1].config.source_draft).toEqual({ profile_id: "p1", revision: 3 }));
});

test("viewers see bots without write buttons", async () => {
  mock.bots = [bot()]; mock.canEdit = false;
  show();
  expect(await screen.findByText(/ดูได้อย่างเดียว/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "อนุมัติ…" })).toBeNull();
  expect(screen.queryByRole("button", { name: "สร้างบอท" })).toBeNull();
});

test("diff and step helpers", () => {
  expect(configDiff(conf(), conf({ mode: "TESTNET" }))).toEqual([{ field: "mode", before: "SHADOW", after: "TESTNET" }]);
  expect([stepOf("account"), stepOf("strategy.params.x"), stepOf("allocation.amount"), stepOf("futures.leverage"), stepOf("hash")]).toEqual([0, 1, 2, 2, 3]);
});
