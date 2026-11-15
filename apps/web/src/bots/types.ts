// Shapes of /api/v1/bots* (apps/api/api/bots.py, docs/m4-bot-config-plan.md §3.4).

export type BotConfig = {
  schema_version: "bot-config-v1";
  name: string;
  account: string;
  venue: "SPOT_TESTNET" | "USDM_TESTNET";
  instrument: { symbol: string; market: "SPOT" | "PERP" };
  strategy: { template: string; version: string; timeframe: string; params: Record<string, number> };
  allocation: { asset: "USDT"; amount: string };
  risk_profile: "spot.tn.v1";
  futures: { leverage: number; margin_mode: string; stop_percent: string } | null;
  mode: "SHADOW" | "TESTNET";
  source_draft: { profile_id: string; revision: number } | null;
  notes: string;
};
export type BotRevision = { bot_id: string; revision: number; account: string; config: BotConfig; hash: string; saved_by: string; saved_at: string };
export type BotStatus = "draft" | "submitted" | "approved" | "running" | "halted" | "retired";
export type Bot = {
  bot_id: string; book: string; account: string; name: string; status: BotStatus;
  latest: BotRevision & { submitted: boolean; verdict: { action: "approve" | "reject"; actor: string; at: string; note: string } | null };
  approved: { revision: number; hash: string; actor: string; at: string } | null;
  running: { revision: number | null; hash: string | null; mode: string | null; pending_revision: number | null; note: string | null; safety: string | null; reported_at: string | null } | null;
  retired: { actor: string; at: string; note: string } | null;
};
export type BotList = { bots: Bot[]; can_edit: boolean };
export type TemplateParam = { default: number; min: number; max: number; label_th: string };
export type BotTemplate = {
  template: string; version: string; name_th: string; market: "SPOT" | "PERP"; symbol: string; timeframes: string[]; summary_th: string;
  params: Record<string, TemplateParam>; allocation: { asset: "USDT"; min: string; max: string };
};
export type BotAccount = { account: string; usdt_free: string | null; balance_as_of: string | null; reconcile: string | null; available: boolean; reason_th: string | null };
export type FieldError = { field: string; code: string; message_th: string };

export const STATUS_TH: Record<BotStatus, string> = {
  draft: "ร่าง", submitted: "รออนุมัติ", approved: "อนุมัติแล้ว รอ worker", running: "กำลังรัน", halted: "HALTED", retired: "ปลดแล้ว",
};

export function blankConfig(t: BotTemplate | undefined, account = ""): BotConfig {
  return {
    schema_version: "bot-config-v1", name: "", account, venue: "SPOT_TESTNET",
    instrument: { symbol: t?.symbol ?? "BTCUSDT", market: "SPOT" },
    strategy: { template: t?.template ?? "", version: t?.version ?? "", timeframe: t?.timeframes[0] ?? "1d",
      params: Object.fromEntries(Object.entries(t?.params ?? {}).map(([k, p]) => [k, p.default])) },
    allocation: { asset: "USDT", amount: t?.allocation.max ?? "2000" }, risk_profile: "spot.tn.v1", futures: null, mode: "SHADOW",
    source_draft: null, notes: "",
  };
}

function flat(v: unknown, prefix = ""): Record<string, unknown> {
  if (v && typeof v === "object" && !Array.isArray(v)) {
    const entries = Object.entries(v as Record<string, unknown>);
    if (!entries.length) return prefix ? { [prefix]: "{}" } : {};
    return Object.assign({}, ...entries.map(([k, x]) => flat(x, prefix ? `${prefix}.${k}` : k)));
  }
  return { [prefix]: v };
}

/** Changed fields between two configs, as dotted paths (same rule as the API's diff). */
export function configDiff(before: BotConfig | null, after: BotConfig): { field: string; before: unknown; after: unknown }[] {
  const a = flat(before ?? {}), b = flat(after);
  return [...new Set([...Object.keys(a), ...Object.keys(b)])].sort()
    .filter((k) => JSON.stringify(a[k]) !== JSON.stringify(b[k])).map((k) => ({ field: k, before: a[k], after: b[k] }));
}

/** Which wizard step holds a field, so an error from the server opens the right step. */
export function stepOf(field: string): number {
  if (["account", "venue", "instrument", "futures"].some((f) => field === f || field.startsWith(`${f}.`))) return field.startsWith("futures") ? 2 : 0;
  if (field.startsWith("strategy")) return 1;
  if (field.startsWith("allocation") || field === "mode" || field === "risk_profile") return 2;
  return field === "name" || field === "notes" ? 0 : 3;
}
