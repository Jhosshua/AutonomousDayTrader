"use client";

import { SwingEngineState } from "@/types/trading";
import { formatMoney } from "@/lib/plain";

interface SwingTelemetryBarProps {
  swingState?: SwingEngineState;
  showPro: boolean;
}

interface SlotTile {
  label: string;
  detail: string;
  filled: boolean;
}

export default function SwingTelemetryBar({ swingState, showPro }: SwingTelemetryBarProps) {
  const maxSlots = swingState?.max_slots ?? 2;
  const slotNotional = swingState?.slot_notional ?? 25000;
  const heldSymbols = (swingState?.positions ?? []).map((p) => p.symbol);
  // F12: held (positions), waiting to buy at open (staged candidates), free = rest.
  const stagedSymbols = (swingState?.candidates ?? []).filter((c) => c.is_staged).map((c) => c.symbol);

  const tiles: SlotTile[] = [];
  for (const sym of heldSymbols) tiles.push({ label: sym, detail: "Holding", filled: true });
  for (const sym of stagedSymbols) tiles.push({ label: sym, detail: "Buys at next open", filled: true });
  while (tiles.length < maxSlots) tiles.push({ label: "Empty", detail: `up to ${formatMoney(slotNotional)}`, filled: false });

  const nowText =
    heldSymbols.length + stagedSymbols.length === 0
      ? "Waiting. No company passes all 4 checks today."
      : [
          heldSymbols.length > 0 ? `Holding ${heldSymbols.length} ${heldSymbols.length === 1 ? "company" : "companies"}.` : "",
          stagedSymbols.length > 0 ? `Buying ${stagedSymbols.length} more at the next open.` : "",
        ].filter(Boolean).join(" ");

  return (
    <section className="grid grid-cols-1 lg:grid-cols-[1.4fr_1fr] gap-4 sm:gap-5" data-testid="swing-telemetry">
      <div className="rise hover-card flex flex-col gap-3 rounded-[28px] border p-6 sm:p-7" style={{ background: "#E7E8F2", borderColor: "#D9DCEB" }}>
        <div className="text-sm font-semibold" style={{ color: "#4A5190" }}>How slow trades work</div>
        <div className="font-display text-xl sm:text-2xl font-medium leading-snug" style={{ color: "#2E3244" }}>
          It buys strong companies right after a sudden 2-day drop, then sells when they bounce back. Usually within a week.
        </div>
        {showPro && (
          <div className="text-xs font-semibold" style={{ color: "#3E4478" }}>
            Pro name: {swingState?.strategy_name || "2-Day Panic Dip (Connors RSI-2)"}
          </div>
        )}
      </div>

      <div className="rise hover-card relative flex flex-col gap-3 overflow-hidden rounded-[28px] p-6 sm:p-7 text-white" style={{ background: "#2E3244", animationDelay: "80ms" }}>
        <div className="drift pointer-events-none absolute -right-20 -top-28 h-64 w-64 rounded-full opacity-50 blur-3xl" style={{ background: "#7E9CC8" }} />
        <div className="drift2 pointer-events-none absolute -left-16 -bottom-32 h-64 w-64 rounded-full opacity-40 blur-3xl" style={{ background: "#8189C4" }} />
        <div className="relative text-sm" style={{ color: "#C9CCD9" }}>Right now</div>
        <div className="relative font-display text-xl sm:text-2xl leading-snug">{nowText}</div>
        <div className="relative mt-auto flex gap-2 sm:gap-3">
          {tiles.map((t, i) => (
            <div
              key={i}
              className="flex-1 rounded-2xl border-[1.5px] p-3.5 flex flex-col gap-1"
              style={{ borderColor: t.filled ? "transparent" : "rgba(212,204,255,0.6)", borderStyle: t.filled ? "solid" : "dashed", background: t.filled ? "rgba(255,255,255,0.1)" : "transparent" }}
            >
              <span className="text-xs" style={{ color: "#C9CCD9" }}>Spot {i + 1}</span>
              <span className="text-base sm:text-lg font-semibold">{t.label}</span>
              <span className="text-xs" style={{ color: "#BCCBEA" }}>{t.detail}</span>
            </div>
          ))}
        </div>
      </div>

      <p className="lg:col-span-2 text-sm leading-relaxed text-muted" data-testid="swing-schedule">
        {swingState?.schedule_text ?? "Checks the 4:00 PM close for sharp dips; any buy or sell happens at the next 9:30 AM open."}
        {swingState?.last_close_entries_withheld ? (
          <span className="mt-1 block" style={{ color: "#A9553A" }}>
            Last close data was incomplete{swingState.last_close_data_note ? `: ${swingState.last_close_data_note}` : ""}. No new buys that night; sells still checked.
          </span>
        ) : swingState?.last_close_data_note ? (
          <span className="mt-1 block text-muted">{swingState.last_close_data_note}</span>
        ) : null}
      </p>
    </section>
  );
}
