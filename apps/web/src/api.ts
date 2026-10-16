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
  const session = supabase ? (await supabase.auth.getSession()).data.session : null;
  const res = await fetch(`${config.apiBase}${path}`, {
    headers: session ? { Authorization: `Bearer ${session.access_token}` } : {},
  });
  const body = await res.json();
  if (!res.ok) throw body as ApiError;
  return body as T;
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
