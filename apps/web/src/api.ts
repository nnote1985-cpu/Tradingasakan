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
        headers: session ? { Authorization: `Bearer ${session.access_token}` } : {},
        signal: controller.signal,
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
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
