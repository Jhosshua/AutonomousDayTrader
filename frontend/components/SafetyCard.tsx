// @steered SNARE-2 2026-09-30
"use client";

import { ShieldCheck, Moon, Target, TriangleAlert } from "lucide-react";
import { useActionButton } from "@/hooks/useActionButton";
import { formatMoney, joinNames } from "@/lib/plain";

/** Overnight holds facts the card must say (PLAN_2026_09_30_overnight_holds.md 5.10, D5, D6, D9).
 * Absent (older backend, no holds) the card reads exactly as before. */
export interface SafetyOvernight {
  /** Overnight holds buy tonight (mode live and the controller running). */
  buysOn: boolean;
  /** Stocks switched on, e.g. ["NVDA", "IREN", "HUT"]. */
  symbols: string[];
  /** Share of the account per stock, 0.20 = 20%. */
  pct: number | null;
  /** Today's overnight result (booked on the day it sells), kept out of the loss limit. */
  resultToday: number | null;
  holdsCount: number;
}

interface SafetyCardProps {
  drawdownDollars: number;
  maxDailyLossDollars: number | null;
  baseTradeRiskPct: number | null;
  intradayPositionsCount: number;
  onFlattenAll: () => boolean;
  overnight?: SafetyOvernight | null;
}

export default function SafetyCard({
  drawdownDollars,
  maxDailyLossDollars,
  baseTradeRiskPct,
  intradayPositionsCount,
  onFlattenAll,
  overnight,
}: SafetyCardProps) {
  const { phase, trigger } = useActionButton({
    send: onFlattenAll,
    isDone: () => intradayPositionsCount === 0,
    requireConfirm: true,
  });

  // drawdownDollars is the loss stop's own drawdown (net of the overnight result) when the backend sends it
  const pctUsed = maxDailyLossDollars ? Math.min(100, (drawdownDollars / maxDailyLossDollars) * 100) : 0;
  const riskPctLabel =
    baseTradeRiskPct != null ? `about ${(baseTradeRiskPct * 100).toFixed(0)}% of the account at risk` : "a small slice of the account at risk";

  const buttonLabel =
    phase === "confirm" ? "Tap again to confirm" : phase === "sending" ? "Closing…" : phase === "done" ? "Closed" : phase === "failed" ? "Didn't go through, try again" : "Close all quick trades now";

  const ovn = overnight ?? null;
  const names = ovn ? joinNames(ovn.symbols).replace(/ and ([^ ]+)$/, " or $1") : "";
  const pctText = ovn?.pct != null ? `${Math.round(ovn.pct * 100)}%` : null;

  return (
    <div
      className="rise hover-card flex flex-col gap-2 rounded-[22px] border p-3 sm:p-3.5"
      style={{ background: "#E9F8F0", borderColor: "#BFE6D3", animationDelay: "380ms" }}
      data-testid="risk-telemetry"
    >
      <div className="flex items-center gap-2.5">
        <div className="flex h-8 w-8 items-center justify-center rounded-xl" style={{ background: "#0A7D53" }}>
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
          <div className="grow h-full rounded-full" style={{ width: `${pctUsed}%`, background: "#0A7D53" }} />
        </div>
        <div className="text-xs" style={{ color: "#0B5A3C" }}>
          {ovn
            ? `${maxDailyLossDollars != null ? `If day trades ever lose ${formatMoney(maxDailyLossDollars)} in a day` : "If day trades ever hit the daily loss limit"}, it stops day trading for the day on its own. The daily loss limit covers day trades only.${ovn.buysOn ? " The overnight buy still goes in at the close." : ""}`
            : maxDailyLossDollars != null
            ? `If it ever loses ${formatMoney(maxDailyLossDollars)} in a day, it stops for the day on its own.`
            : "If it ever hits its daily loss limit, it stops for the day on its own."}
        </div>
        {ovn && (
          <div className="text-xs" style={{ color: "#0B5A3C" }} data-testid="safety-overnight-result">
            {ovn.resultToday != null && Math.abs(ovn.resultToday) >= 0.005
              ? `Overnight holds ${ovn.resultToday > 0 ? "made" : "lost"} ${formatMoney(Math.abs(ovn.resultToday))} today. This is not counted in the limit above.`
              : "No overnight hold result today."}
          </div>
        )}
      </div>

      {/* The three rule paragraphs: one disclosure, closed by default. The meter, its sentence, the overnight
          result line, the button and the "not included" note stay visible. */}
      <details className="border-t pt-1" style={{ borderColor: "#BFE6D3" }} data-testid="safety-rules-details">
        <summary className="flex min-h-[44px] cursor-pointer items-center text-sm font-semibold text-ink">The safety rules</summary>
        <div className="flex flex-col gap-3 pb-1 pt-1">
      <div className="flex items-start gap-2.5">
        <Moon className="mt-0.5 h-4 w-4 flex-shrink-0" style={{ color: "#2B4BFF" }} aria-hidden="true" />
        <div className="text-[13px] leading-snug">
          {ovn && ovn.buysOn ? (
            <><b>{`Most quick trades close at 3:55 PM, but a quick trade in ${names} closes at 3:46 PM on a night the robot buys that stock.`}</b> Slow trades can stay open for days. Overnight holds buy at the 4:00 PM close and sell at the next 9:30 AM open.</>
          ) : (
            <><b>Quick trades start closing at 3:55 PM.</b> Slow trades can stay open for days.</>
          )}
          {ovn && !ovn.buysOn && ovn.holdsCount > 0 && " Overnight holds sell at the next 9:30 AM open."}
        </div>
      </div>
      <div className="flex items-start gap-2.5">
        <Target className="mt-0.5 h-4 w-4 flex-shrink-0" style={{ color: "#0A7D53" }} aria-hidden="true" />
        <div className="text-[13px] leading-snug">
          <b>Small bets.</b> Keeps each bet small ({riskPctLabel}). Exception: Opening Range Breakout follows ORBStraddle's sizing, 2% on its first trade of the day and 2.5% in total.
          {ovn && (ovn.buysOn || ovn.holdsCount > 0) && ` Overnight holds are not small bets. Each puts ${pctText ?? "a set share"} of the account in one stock with no stop.`}
        </div>
      </div>
      <div className="flex items-start gap-2.5">
        <TriangleAlert className="mt-0.5 h-4 w-4 flex-shrink-0" style={{ color: "#8A4B00" }} aria-hidden="true" />
        <div className="text-[13px] leading-snug">
          {ovn ? (
            <><b>Every day trade has an exit plan.</b> A price where it gives up, set before it buys. Overnight holds have no stop and sell at the next open.</>
          ) : (
            <><b>Every trade has an exit plan.</b> A price where it gives up, set before it buys.</>
          )}
        </div>
      </div>

        </div>
      </details>

      <button
        type="button"
        onClick={trigger}
        disabled={phase === "sending" || intradayPositionsCount === 0}
        data-testid="btn-flatten-all"
        className="mt-auto min-h-[44px] rounded-xl border-0 text-sm font-semibold text-white shadow transition-opacity disabled:opacity-50"
        style={{ background: phase === "confirm" ? "#C2300F" : "#0E1330" }}
      >
        {buttonLabel}
      </button>
      {ovn && ovn.holdsCount > 0 && (
        <div className="text-xs" style={{ color: "#0B5A3C" }} data-testid="safety-holds-not-closed">
          Overnight holds are not included. They sell at the next 9:30 AM open.
        </div>
      )}
    </div>
  );
}
