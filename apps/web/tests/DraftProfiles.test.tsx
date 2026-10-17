import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { DraftRecipe, ProfileDraft } from "../src/api";
import { DraftProfiles } from "../src/DraftProfiles";

const mock = vi.hoisted(() => ({ canEdit: true, post: vi.fn() }));
vi.mock("../src/api", () => ({
  apiPost: (...args: unknown[]) => mock.post(...args),
  apiGet: async (path: string) => {
    if (path.endsWith("bot-profile-drafts")) return { items: [], can_edit: mock.canEdit };
    if (path.endsWith("strategies")) return { items: [
      { number: 1, name: "MACD", version: "1.0", family: "trend", timeframes: ["1h", "4h", "1d"], default_timeframe: "4h", summary: "MACD signal", params: { fast: { default: 12, min: 5, max: 20, integer: true } } },
      { number: 7, name: "SMA", version: "1.0", family: "trend", timeframes: ["1h", "4h", "1d"], default_timeframe: "1d", summary: "SMA signal", params: { sma: { default: 100, min: 10, max: 300, integer: true } } },
    ] };
    if (path.endsWith("bot-profiles")) return { items: [{ profile_id: "preset-mix", name: "Preset mix", version: 1, combine: "average", risk_profile: "spot.v1", notes: "original", strategies: [
      { number: 1, version: "1.0", timeframe: "4h", params: { fast: 12 } }, { number: 7, version: "1.0", timeframe: "1d", params: { sma: 100 } },
    ] }] };
    throw new Error(path);
  },
}));
beforeEach(() => {
  mock.canEdit = true;
  mock.post.mockReset().mockImplementation(async (_path: string, body: { profile_id: string | null; expected_revision: number; profile: DraftRecipe }): Promise<ProfileDraft> => ({
    profile_id: body.profile_id ?? "saved-id", revision: body.expected_revision + 1, profile: body.profile, hash: "hash", saved_at: new Date().toISOString(), saved_by: "operator", execution_supported: false,
  }));
});
afterEach(cleanup);
function show() { return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><DraftProfiles /></QueryClientProvider>); }
async function start(name: string) {
  fireEvent.click(await screen.findByRole("button", { name: "สร้าง profile" }));
  await screen.findByRole("form", { name: "ตั้งค่า Draft Bot Profile" });
  fireEvent.change(screen.getByLabelText("ชื่อ profile"), { target: { value: name } });
}

test("creates a custom draft with independent filter, exit, stop and risk settings", async () => {
  show(); await start("Custom 1");
  fireEvent.change(screen.getByLabelText("Trend Filter", { exact: true }), { target: { value: "on" } });
  fireEvent.change(screen.getByLabelText("Trend Filter · กลยุทธ์"), { target: { value: "7" } });
  fireEvent.change(screen.getByLabelText("Trend Filter · Timeframe"), { target: { value: "1d" } });
  fireEvent.change(screen.getByLabelText("Trend Filter · sma"), { target: { value: "150" } });
  fireEvent.change(screen.getByLabelText("Exit", { exact: true }), { target: { value: "strategy_signal" } });
  fireEvent.change(screen.getByLabelText("Exit · กลยุทธ์"), { target: { value: "7" } });
  fireEvent.change(screen.getByLabelText("Stop-loss", { exact: true }), { target: { value: "fixed_percent" } });
  fireEvent.change(screen.getByLabelText("ระยะ Stop-loss (%)"), { target: { value: "3" } });
  fireEvent.change(screen.getByLabelText(/Risk \/ Trade/), { target: { value: "0.75" } });
  fireEvent.change(screen.getByLabelText(/Max Exposure/), { target: { value: "40" } });
  fireEvent.click(screen.getByRole("button", { name: "บันทึก Draft Profile" }));
  await screen.findByTestId("saved-draft");
  const body = mock.post.mock.calls[0][1];
  expect(body.profile).toMatchObject({ setup_mode: "custom", status: "draft", preset_profile: null, timeframe: "4h", risk_per_trade_percent: .75, max_exposure_percent: 40,
    entry: { number: 1 }, trend_filter: { number: 7, timeframe: "1d", params: { sma: 150 } }, exit_kind: "strategy_signal", exit_rule: { number: 7 },
    stop_loss: { kind: "fixed_percent", percent: 3, atr_period: null, atr_multiple: null } });
  expect(body.request_id).toBeTruthy();
  expect(screen.queryByRole("form")).toBeNull();
});

test("preset snapshots keep combination and all signals; edits append a revision", async () => {
  show(); await start("Preset 1");
  fireEvent.change(screen.getByLabelText("วิธีตั้งค่า"), { target: { value: "preset" } });
  expect(screen.queryByLabelText("Entry · กลยุทธ์")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "บันทึก Draft Profile" }));
  await screen.findByTestId("saved-draft");
  const preset = mock.post.mock.calls[0][1].profile;
  expect(preset).toMatchObject({ setup_mode: "preset", timeframe: "1d", entry: null, preset_strategy_versions: { "1": "1.0", "7": "1.0" }, preset_profile: { combine: "average" } });
  expect(preset.preset_profile.strategies).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: "แก้ไข Preset 1" }));
  fireEvent.change(screen.getByLabelText("หมายเหตุ"), { target: { value: "revision two" } });
  fireEvent.click(screen.getByRole("button", { name: "บันทึก Draft Profile" }));
  await waitFor(() => expect(screen.getByTestId("saved-draft").textContent).toContain("เวอร์ชัน 2"));
  expect(mock.post.mock.calls[1][1]).toMatchObject({ profile_id: "saved-id", expected_revision: 1 });
});

test("failed saves keep the form and retry key; impossible risk settings do not submit", async () => {
  show(); await start("Keep me");
  fireEvent.change(screen.getByLabelText(/Risk \/ Trade/), { target: { value: "50" } });
  fireEvent.click(screen.getByRole("button", { name: "บันทึก Draft Profile" }));
  expect(mock.post).not.toHaveBeenCalled();
  expect(screen.getByRole("alert").textContent).toContain("ต้องไม่เกิน Max Exposure");
  fireEvent.change(screen.getByLabelText(/Risk \/ Trade/), { target: { value: "1" } });
  mock.post.mockRejectedValueOnce(new Error("network failed"));
  fireEvent.click(screen.getByRole("button", { name: "บันทึก Draft Profile" }));
  await screen.findByText("network failed");
  expect((screen.getByLabelText("ชื่อ profile") as HTMLInputElement).value).toBe("Keep me");
  const key = mock.post.mock.calls[0][1].request_id;
  fireEvent.click(screen.getByRole("button", { name: "บันทึก Draft Profile" }));
  await screen.findByTestId("saved-draft");
  expect(mock.post.mock.calls[1][1].request_id).toBe(key);
});

test("viewers can inspect drafts without write controls", async () => {
  mock.canEdit = false; show();
  await screen.findByText(/การสร้างและบันทึกต้องใช้สิทธิ์ Operator/);
  expect(screen.queryByRole("button", { name: "สร้าง profile" })).toBeNull();
});
