// Public values only (§21.6): API base URL and the Supabase publishable key.
export const config = {
  apiBase: import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000",
  supabaseUrl: import.meta.env.VITE_SUPABASE_URL ?? "",
  supabaseKey: import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY ?? "",
};
