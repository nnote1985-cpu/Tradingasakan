import { createClient } from "@supabase/supabase-js";
import { config } from "./config";

export const supabase = config.supabaseUrl ? createClient(config.supabaseUrl, config.supabaseKey) : null;

export type PortfolioRow = {
  label: string;
  mode: string;
  currency: string;
  initial_equity: string;
  as_of: string | null;
  trading_equity: string | null;
  economic_equity: string | null;
  high_water_mark: string | null;
  quality: string | null;
  stale: boolean;
  return: string | null;          // fraction of initial equity, from economic equity
  drawdown: string | null;        // fraction below the peak of economic equity
  exposure_share: string | null;  // BTC exposure / economic equity at the last mark
};
export type EquityPoint = {
  at: string;
  trading_equity: string;
  economic_equity: string;
  mark_price: string | null;
  exposure: string | null;
  quality: string;
  return: string;
  drawdown: string;
};
export type EquitySeries = {
  label: string;
  mode: string;
  currency: string;
  initial_equity: string;
  points: EquityPoint[];
  max_drawdown: string;
};
export type Overview = { mode: string; live_enabled: boolean; portfolios: PortfolioRow[] };
export type ApiError = { code: string; request_id: string | null; retryable: boolean; message: string };

export async function apiGet<T>(path: string): Promise<T> {
  return apiRequest<T>(path);
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  return apiRequest<T>(path, body);
}

async function apiRequest<T>(path: string, payload?: unknown): Promise<T> {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout>;
  const timeout = new Promise<never>((_resolve, reject) => {
    timer = setTimeout(() => {
      controller.abort();
      reject(new Error("การเชื่อมต่อหมดเวลา กรุณาตรวจว่า Backend ทำงานอยู่แล้วลองใหม่"));
    }, 10_000);
  });
  try {
    return await Promise.race([timeout, (async () => {
      const session = supabase ? (await supabase.auth.getSession()).data.session : null;
      const res = await fetch(`${config.apiBase}${path}`, {
        method: payload === undefined ? "GET" : "POST",
        headers: { ...(session ? { Authorization: `Bearer ${session.access_token}` } : {}), ...(payload === undefined ? {} : { "Content-Type": "application/json" }) },
        body: payload === undefined ? undefined : JSON.stringify(payload),
        signal: controller.signal,
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        if (res.status === 422 && Array.isArray(body?.detail)) {
          throw new Error("ค่าตั้งไม่ถูกต้อง: " + body.detail.map((d: { loc?: string[]; msg?: string }) => `${d.loc?.slice(1).join(".") ?? ""}: ${d.msg ?? "invalid"}`).join(" · "));
        }
        throw body?.message ? body as ApiError : new Error(`API ตอบกลับผิดพลาด (${res.status})`);
      }
      return await res.json() as T;
    })()]);
  } catch (error) {
    if (error instanceof TypeError) {
      throw new Error("เชื่อมต่อ Backend ไม่ได้ กรุณารัน npm run dev จากโฟลเดอร์หลักของโปรเจกต์");
    }
    throw error;
  } finally {
    clearTimeout(timer!);
  }
}

export type DraftRule = { number: number; version: string; timeframe: string; params: Record<string, number> };
export type DraftRecipe = {
  schema_version: "bot-recipe-draft-v1"; status: "draft"; name: string; instrument: "BTCUSDT_SPOT"; timeframe: string;
  setup_mode: "custom" | "preset"; preset_profile: PresetSnapshot | null; preset_strategy_versions: Record<string, string>;
  trend_filter: DraftRule | null; entry: DraftRule | null;
  stop_loss: { kind: "atr" | "trailing_atr" | "fixed_percent"; atr_period: number | null; atr_multiple: number | null; percent: number | null };
  exit_kind: "entry_signal" | "strategy_signal"; exit_rule: DraftRule | null;
  risk_per_trade_percent: number; max_exposure_percent: number; notes: string;
};
export type ProfileDraft = { profile_id: string; revision: number; profile: DraftRecipe; saved_at: string; saved_by: string; hash: string; execution_supported: false };
export type ProfileDrafts = { items: ProfileDraft[]; can_edit: boolean };
export type PresetSnapshot = {
  profile_id: string; version: number; name: string; status: "draft"; instrument: "BTCUSDT_SPOT"; decision_clock: "1d";
  combine: "average" | "main_with_filters"; risk_profile: string; ai_mode: "off"; notes: string;
  strategies: { number: number; timeframe: string; params: Record<string, number> }[];
};
export type RiskStatus = { label: string; halted: boolean; halted_since: string | null; halt_reason: string | null };
export type RiskStatusResponse = { available: boolean; portfolios: RiskStatus[] };
export type AgentDecision = {
  proposal_id: string;
  kind: "filter" | "diagnostic";
  decided_on: string;
  current_weight: string | null;
  target_weight: string | null;
  decision: string | null;
  outcome: string;               // the decision on SUCCESS, else the attempt's failure (TIMEOUT, INVALID, ...)
  reason_code: string | null;
  reason: string | null;
  attempt_status: string | null;
  error_category: string | null;
  model_id: string | null;
  cost_usd: string | null;
  latency_ms: number | null;
  decided_at: string;
};
export type DecisionsResponse = {
  available: boolean;
  items: AgentDecision[];
  counts: Record<string, Record<string, number>>;
  cost_usd: string;
  limit: number;
};

export type AiSettings = {
  experiment_id: string;
  graph_version: string;
  locked: boolean;
  capability_stage: string;
  model_id: string;
  effort: string;
  prompt_version: string;
  prompt_hash: string;
  policy_version: string;
  context_profile: string;
  tools: string[];
  timeout_ms: number;
  max_attempts: number;
  max_run_cost_usd: string;
  daily_cost_cap_usd: string;
  monthly_cap_usd: string;
  timebox_days: number;
};
export type Verdict = "avoided_loss" | "missed_gain" | "allowed_gain" | "allowed_loss" | "flat" | "pending";
export type ScorecardItem = {
  proposal_id: string;
  kind: "filter" | "diagnostic";
  decided_on: string;
  outcome: string;
  forward_return: string | null;   // BTC close change over horizon_days, decimal string
  verdict: Verdict;
};
export type ScorecardResponse = {
  available: boolean;
  horizon_days: number;
  items: ScorecardItem[];
  counts: Record<string, Partial<Record<Verdict, number>>>;
  diagnostic_only: boolean;
};

export type BacktestPoint = { day: string; strategy: string; buy_and_hold: string; strategy_drawdown: string; buy_and_hold_drawdown: string };
export type BacktestTrade = {
  n: number;
  side: "long" | "short";
  entry_day: string;
  entry_price: string;
  qty: string;
  exit_day: string | null;        // null while still held at the end of the run
  exit_price: string | null;
  open: boolean;
  days: number;
  pnl: string;                    // after fees, slippage and funding while held
  return: string;
};
export type Backtest = {
  id: string;
  name: string;
  market: string;
  instrument: string;
  status: string;                 // dev_passed_unconfirmed, ...
  plan: string;
  commit: string;
  set: string;
  range: [string, string];
  initial_equity: string;
  summary: {
    net: string; sharpe: string; max_dd: string; bh_net: string; bh_sharpe: string; bh_max_dd: string;
    flips: number; days_long: number; days_short: number; fees: string; funding_paid: string;
    log_return_long_days: string; log_return_short_days: string; passes: boolean;
  };
  note: string;
  points: BacktestPoint[];
  trades?: BacktestTrade[];
  trade_stats?: {
    count: number; wins: number; long_pnl: string; short_pnl: string;
    closed: number; closed_wins: number; win_rate: string | null;   // closed trades only
    closed_pnl: string; open_pnl: string; net_pnl: string;
  };
};

export type ParamBound = { default: number; min: number; max: number; integer?: boolean };
export type LibraryStrategy = {
  number: number;
  template_id: string;
  version: string;
  name: string;
  family: string;                 // trend | momentum | mean_reversion
  timeframes: string[];
  default_timeframe: string;
  summary: string;
  params: Record<string, ParamBound>;
  evidence: string;               // untested until a pre-declared backtest says otherwise
};
export type ProfileStrategy = { number: number; template_id: string; version: string; name: string; timeframe: string; params: Record<string, number> };
export type BotProfile = {
  profile_id: string;
  version: number;
  name: string;
  status: "draft" | "active" | "retired";
  instrument: string;
  decision_clock: string;
  combine: "average" | "main_with_filters";
  risk_profile: string;
  ai_mode: string;
  notes: string;
  strategies: ProfileStrategy[];
  hash: string;
};

export type AssetInfo = { symbol: string; base: string; name: string; live_stream: string };
export type TrendState = "up" | "down" | "mixed";
export type Timeframe = { timeframe: "1h" | "4h" | "1d"; bar_open: string | null; close: string | null; ema20: string | null; ema50: string | null; state: TrendState | null };
export type AssetStatus = AssetInfo & {
  available: boolean;
  loading?: boolean;            // first download still running
  failed?: boolean;             // last download failed and nothing is cached yet
  refreshing?: boolean;
  fetched_at?: string;
  data_through?: string;        // last closed UTC day in the figures
  stale?: boolean;
  close?: string | null;
  change?: { d1: string | null; d7: string | null; d30: string | null };
  range_30d?: { high: string | null; low: string | null };
  regime?: { sma100: string | null; dist_to_sma: string | null; trend_up: boolean | null };
  timeframes?: Timeframe[];
  funding?: { per_8h_7d: string | null; hot: boolean | null; estimated: boolean };
  vol_30d?: string | null;
  drawdown_365d?: string | null;
  open_interest?: { usdt: string | null; change_7d: string | null } | null;
  events?: string[];
  missing_inputs?: string[];
};
export type StreamStatus = { connected: boolean; connects: number; silent_ms: number | null; error: string | null };
export type WorkerExecution = {
  safety: string;
  open_orders: number;
  unresolved: { client_order_id: string; state: string; for_s: number }[];
  waiting: number;
  orders_today: number;
  cancels_today: number;
  counts: Record<string, number>;
  last_error: string | null;
  conformance: { book: string; cycle: string | null; cycles_done: number; note: string } | null;
};

export type WorkerStatus = {
  account: string;
  owner: string;
  host: string;
  started_at: string | null;
  last_tick_at: string | null;
  updated_at: string | null;
  silent: boolean;
  mode: string | null;
  execution_enabled: boolean | null;
  safety: string | null;
  lease_token: number | null;
  clock: { offset_ms: number | null; status: string | null; signing_paused: boolean | null };
  account_ok: boolean | null;
  deadman_ok: boolean | null;
  streams: Record<string, StreamStatus>;
  quote_age_ms: number | null;
  bars: { last_1m: number | null; last_1d: number | null; gaps: number | null; revisions: number | null };
  persist: { ok: boolean | null; pending: number; epoch: number | null } | null;
  execution: WorkerExecution | null;
  epoch: { epoch: number; opened_at: string | null } | null;
  reconcile: { verdict: string; checked_at: string | null; differences: string[]; open_orders: number } | null;
  balances: { asset: string; free: string; locked: string }[];
  asset_count: number;
  trades: number;
};
export type WorkerOrder = {
  client_order_id: string; book: string; leg: string; side: "BUY" | "SELL"; type: "MARKET" | "LIMIT"; qty: string;
  limit_price: string | null; created_at: string; state: string; filled_qty: string; updated_at: string; reason: string;
  avg_price: string | null;
};

export type WorkerTrade = {
  traded_at: string; epoch: number; trade_id: number; side: "BUY" | "SELL"; qty: string; price: string; quote_qty: string;
  commission: string; commission_asset: string | null; client_order_id: string | null; book: string | null; leg: string | null;
};

export type WorkerPnl = {
  epoch: number | null; opened_at: string | null; mark: string | null; mark_source: "quote" | "last_trade" | null;
  balances: { asset: string; opening: string; now: string | null; change: string | null }[];
  equity_change_usdt: string | null;
  books: { book: string | null; buys: number; sells: number; btc_net: string; usdt_net: string; bnb_fees: string; volume: string;
           pnl_usdt: string | null }[];
};

export type WorkerActivity = { available: boolean; orders: WorkerOrder[]; trades: WorkerTrade[]; pnl: WorkerPnl | null };

export type WorkersResponse = { available: boolean; workers: WorkerStatus[] };
