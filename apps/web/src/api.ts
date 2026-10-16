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
