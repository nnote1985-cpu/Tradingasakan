// §22.3: the mode must be unmistakable on every screen. The banner stays on top while scrolling.
export function ShadowBanner({ mode, liveEnabled }: { mode: string; liveEnabled: boolean }) {
  const live = mode === "LIVE";
  return (
    <div data-testid="shadow-banner" role="status"
      className={`sticky top-0 z-20 px-4 py-2 text-center text-sm font-semibold ${live ? "bg-red-600 text-white" : "bg-[var(--shadow-bg)] text-[var(--shadow-fg)]"}`}>
      {live ? "โหมด API: LIVE · เงินจริง" : mode === "TESTNET" ? "โหมด API: TESTNET · ส่งคำสั่งไป Binance Testnet ด้วยเงินทดสอบ" : `โหมด API: ${mode} · เงินสมมติ จำลองภายในระบบ`}
      {!live && <span className="ml-2 font-normal opacity-80">{liveEnabled ? "(Live เปิดอยู่ในระบบ)" : "Live ปิดอยู่"}</span>}
      {!live && <span className="ml-2 font-normal opacity-80">· โหมดของบอตแสดงแยกตามบัญชี</span>}
    </div>
  );
}
