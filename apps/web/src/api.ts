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
