"use client";

import { ShieldCheck, Moon, Target, TriangleAlert } from "lucide-react";
import { useActionButton } from "@/hooks/useActionButton";
import { formatMoney } from "@/lib/plain";

interface SafetyCardProps {
  drawdownDollars: number;
  maxDailyLossDollars: number | null;
  baseTradeRiskPct: number | null;
  intradayPositionsCount: number;
  onFlattenAll: () => boolean;
}

export default function SafetyCard({
  drawdownDollars,
  maxDailyLossDollars,
  baseTradeRiskPct,
  intradayPositionsCount,
  onFlattenAll,
}: SafetyCardProps) {
  const { phase, trigger } = useActionButton({
    send: onFlattenAll,
    isDone: () => intradayPositionsCount === 0,
    requireConfirm: true,
  });

  const pctUsed = maxDailyLossDollars ? Math.min(100, (drawdownDollars / maxDailyLossDollars) * 100) : 0;
  const riskPctLabel =
    baseTradeRiskPct != null ? `about ${(baseTradeRiskPct * 100).toFixed(0)}% of the account at risk` : "a small slice of the account at risk";

  const buttonLabel =
    phase === "confirm" ? "Tap again to confirm" : phase === "sending" ? "Closing…" : phase === "done" ? "Closed" : phase === "failed" ? "Didn't go through, try again" : "Close all quick trades now";

  return (
    <div
      className="rise hover-card flex flex-col gap-3 rounded-[22px] border p-4 sm:p-5"
      style={{ background: "#E9EFE8", borderColor: "#D5E2D6", animationDelay: "380ms" }}
      data-testid="risk-telemetry"
    >
      <div className="flex items-center gap-2.5">
        <div className="flex h-8 w-8 items-center justify-center rounded-xl" style={{ background: "#3F7D5C" }}>
          <ShieldCheck className="h-4 w-4 text-white" strokeWidth={2} aria-hidden="true" />
        </div>
        <h2 className="font-display text-lg font-semibold text-ink">Safety rules</h2>
      </div>

      <div className="flex flex-col gap-1">
        <div className="flex justify-between text-sm">
          <span>Daily loss limit used</span>
          <span className="tabular-nums font-semibold">
            {formatMoney(drawdownDollars)}{maxDailyLossDollars != null ? ` of ${formatMoney(maxDailyLossDollars)}` : ""}
          </span>
        </div>
        <div className="h-2 overflow-hidden rounded-full" style={{ background: "rgba(255,255,255,0.8)" }}>
          <div className="grow h-full rounded-full" style={{ width: `${pctUsed}%`, background: "linear-gradient(90deg, #5E9A7A, #E3B77F)" }} />
        </div>
        <div className="text-xs" style={{ color: "#2F5A4B" }}>
          {maxDailyLossDollars != null
            ? `If it ever loses ${formatMoney(maxDailyLossDollars)} in a day, it stops for the day on its own.`
            : "If it ever hits its daily loss limit, it stops for the day on its own."}
        </div>
      </div>

      <div className="flex items-start gap-2.5 border-t pt-3" style={{ borderColor: "rgba(14,138,98,0.2)" }}>
        <Moon className="mt-0.5 h-4 w-4 flex-shrink-0" style={{ color: "#4A5190" }} aria-hidden="true" />
        <div className="text-[13px] leading-snug">
          <b>Quick trades start closing at 3:55 PM.</b> Slow trades can stay open for days.
        </div>
      </div>
      <div className="flex items-start gap-2.5">
        <Target className="mt-0.5 h-4 w-4 flex-shrink-0" style={{ color: "#3F7D5C" }} aria-hidden="true" />
        <div className="text-[13px] leading-snug">
          <b>Small bets.</b> Keeps each bet small ({riskPctLabel}). Exception: Opening Range Breakout follows ORBStraddle's sizing, 2% on its first trade of the day and 2.5% in total.
        </div>
      </div>
      <div className="flex items-start gap-2.5">
        <TriangleAlert className="mt-0.5 h-4 w-4 flex-shrink-0" style={{ color: "#A9553A" }} aria-hidden="true" />
        <div className="text-[13px] leading-snug">
          <b>Every trade has an exit plan.</b> A price where it gives up, set before it buys.
        </div>
      </div>

      <button
        type="button"
        onClick={trigger}
        disabled={phase === "sending" || intradayPositionsCount === 0}
        data-testid="btn-flatten-all"
        className="mt-auto min-h-[44px] rounded-xl border-0 text-sm font-semibold text-white shadow transition-opacity disabled:opacity-50"
        style={{ background: "#A9553A" }}
      >
        {buttonLabel}
      </button>
    </div>
  );
}
