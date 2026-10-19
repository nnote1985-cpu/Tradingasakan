import { type ReactNode, useSyncExternalStore } from "react";

export const TABS = [
  { key: "overview", label: "ภาพรวม" },
  { key: "bots", label: "บอตและพอร์ต" },
  { key: "research", label: "งานวิจัย" },
  { key: "system", label: "ระบบ" },
  { key: "settings", label: "ตั้งค่า" },
] as const;
export type TabKey = (typeof TABS)[number]["key"];
export type ViewKey = TabKey | "spot" | "asset" | "futures" | "ai" | "strategies" | "options" | "worker";
export function sectionOf(view: ViewKey): TabKey {
  if (["spot", "futures", "ai", "options"].includes(view)) return "research";
  if (view === "strategies") return "settings";
  if (view === "asset") return "bots";
  if (view === "worker") return "system";
  return view as TabKey;
}

function fromHash(): ViewKey {
  const h = window.location.hash.slice(1);
  return [...TABS.map((t) => t.key), "spot", "asset", "futures", "ai", "strategies", "options", "worker"].includes(h) ? (h as ViewKey) : "overview";
}

const NAVIGATE = "tradingasakan:navigate";

function subscribe(onChange: () => void) {
  window.addEventListener(NAVIGATE, onChange);
  window.addEventListener("hashchange", onChange);
  window.addEventListener("popstate", onChange);
  return () => {
    window.removeEventListener(NAVIGATE, onChange);
    window.removeEventListener("hashchange", onChange);
    window.removeEventListener("popstate", onChange);
  };
}

function navigate(view: ViewKey) {
  if (window.location.hash !== `#${view}`) {
    window.history.pushState(window.history.state, "", `#${view}`);
    window.dispatchEvent(new Event(NAVIGATE));
  }
}

/** URL is the single source for both panel and highlight. Internal navigation
 * notifies React synchronously; browser history and ordinary hash links also work. */
export function useHashTab(): [ViewKey, (t: ViewKey) => void] {
  return [useSyncExternalStore(subscribe, fromHash, () => "overview"), navigate];
}

export function TabBar({ tab, onSelect }: { tab: TabKey; onSelect: (t: TabKey) => void }) {
  return (
    <div role="tablist" aria-label="เมนูหลัก" aria-orientation="vertical" className="main-nav">
      {TABS.map((t) => {
        const on = t.key === tab;
        return (
          <button
            type="button"
            key={t.key}
            role="tab"
            id={`tab-${t.key}`}
            aria-selected={on}
            aria-controls={`panel-${t.key}`}
            onKeyDown={(e) => {
              const index = TABS.findIndex((x) => x.key === t.key);
              const next = ["ArrowDown", "ArrowRight"].includes(e.key) ? (index + 1) % TABS.length : ["ArrowUp", "ArrowLeft"].includes(e.key) ? (index + TABS.length - 1) % TABS.length : e.key === "Home" ? 0 : e.key === "End" ? TABS.length - 1 : -1;
              if (next >= 0) { e.preventDefault(); onSelect(TABS[next].key); document.getElementById(`tab-${TABS[next].key}`)?.focus(); }
            }}
            tabIndex={on ? 0 : -1}
            onClick={() => onSelect(t.key)}
            className={`min-h-11 rounded-xl px-4 py-3 text-left text-sm font-medium ${on ? "bg-[var(--text-primary)] text-[var(--surface-1)]" : "text-[var(--text-secondary)] hover:bg-[var(--surface-0)]"}`}
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}

export function NotOpen({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div data-testid="not-open" className="rounded-xl border border-dashed border-[var(--border)] p-6 text-center">
      <p className="font-medium">{title}</p>
      <p className="mt-1 text-sm text-[var(--text-secondary)]">{children}</p>
    </div>
  );
}
