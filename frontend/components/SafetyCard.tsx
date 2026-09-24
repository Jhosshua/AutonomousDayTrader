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
      className="rise hover-card flex flex-col gap-4 rounded-[28px] border p-6 sm:p-7"
      style={{ background: "#E9EFE8", borderColor: "#D5E2D6", animationDelay: "380ms" }}
      data-testid="risk-telemetry"
    >
      <div className="flex items-center gap-2.5">
        <div className="flex h-10 w-10 items-center justify-center rounded-2xl" style={{ background: "#3F7D5C" }}>
          <ShieldCheck className="h-5 w-5 text-white" strokeWidth={2} aria-hidden="true" />
        </div>
        <h2 className="font-display text-xl sm:text-2xl font-semibold text-ink">Safety rules</h2>
      </div>

      <div className="flex flex-col gap-1.5">
        <div className="flex justify-between text-sm">
          <span>Daily loss limit used</span>
          <span className="tabular-nums font-semibold">
            {formatMoney(drawdownDollars)}{maxDailyLossDollars != null ? ` of ${formatMoney(maxDailyLossDollars)}` : ""}
          </span>
        </div>
        <div className="h-3 overflow-hidden rounded-full" style={{ background: "rgba(255,255,255,0.8)" }}>
          <div className="grow h-full rounded-full" style={{ width: `${pctUsed}%`, background: "linear-gradient(90deg, #5E9A7A, #E3B77F)" }} />
        </div>
        <div className="text-sm" style={{ color: "#2F5A4B" }}>
          {maxDailyLossDollars != null
            ? `If it ever loses ${formatMoney(maxDailyLossDollars)} in a day, it stops for the day on its own.`
            : "If it ever hits its daily loss limit, it stops for the day on its own."}
        </div>
      </div>

      <div className="flex items-start gap-3 border-t pt-3.5" style={{ borderColor: "rgba(14,138,98,0.2)" }}>
        <Moon className="h-5 w-5 flex-shrink-0" style={{ color: "#4A5190" }} aria-hidden="true" />
        <div className="text-sm leading-relaxed">
          <b>Quick trades start closing at 3:55 PM.</b> Slow trades can stay open for days.
        </div>
      </div>
      <div className="flex items-start gap-3">
        <Target className="h-5 w-5 flex-shrink-0" style={{ color: "#3F7D5C" }} aria-hidden="true" />
        <div className="text-sm leading-relaxed">
          <b>Small bets.</b> Keeps each bet small ({riskPctLabel}).
        </div>
      </div>
      <div className="flex items-start gap-3">
        <TriangleAlert className="h-5 w-5 flex-shrink-0" style={{ color: "#A9553A" }} aria-hidden="true" />
        <div className="text-sm leading-relaxed">
          <b>Every trade has an exit plan.</b> A price where it gives up, set before it buys.
        </div>
      </div>

      <button
        type="button"
        onClick={trigger}
        disabled={phase === "sending" || intradayPositionsCount === 0}
        data-testid="btn-flatten-all"
        className="mt-auto min-h-[52px] rounded-2xl border-0 text-base font-semibold text-white shadow-lg transition-opacity disabled:opacity-50"
        style={{ background: "#A9553A" }}
      >
        {buttonLabel}
      </button>
    </div>
  );
}
