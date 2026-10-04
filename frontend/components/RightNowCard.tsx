"use client";

import { AlertTriangle, Check } from "lucide-react";
import { AttentionItem, attentionPillText } from "@/lib/plain";

interface RightNowCardProps {
  sentence: string;
  tradesToday: number;
  wins: number;
  losses: number;
  countdownLabel: string;
  countdownValue: string;
  /** Everything that needs a look (collectAttention). Empty = no alarm is on. */
  attention: AttentionItem[];
}

/** The status strip: one thin accent row under the header. Left: the "No alarms" / "N need a look" pill. Middle: the
 * existing right-now sentence (with the list of what needs a look in front of it when something does). Right: the
 * three stats. Every colour on it is white, or lime for the calm pill and the wins number (contrast checked). */
export default function RightNowCard({
  sentence,
  tradesToday,
  wins,
  losses,
  countdownLabel,
  countdownValue,
  attention,
}: RightNowCardProps) {
  const total = Math.max(1, wins + losses);
  const winPct = (wins / total) * 100;
  const calm = attention.length === 0;

  return (
    <div
      className="rise relative flex flex-col gap-2 rounded-[18px] bg-darkcard px-3 py-2 text-white sm:px-4 lg:flex-row lg:items-center lg:gap-4"
      style={{ animationDelay: "120ms" }}
      data-testid="status-strip"
    >
      <div className="flex min-w-0 flex-1 flex-col gap-2 sm:flex-row sm:items-center sm:gap-3">
        <span
          data-testid="attention-pill"
          data-count={attention.length}
          className={`inline-flex min-h-[32px] flex-shrink-0 items-center gap-1.5 self-start rounded-full px-3 text-sm font-bold ${calm ? "bg-lime text-ink" : "bg-warnbg text-warn"}`}
        >
          {calm ? <Check className="h-4 w-4" strokeWidth={3} aria-hidden="true" /> : <AlertTriangle className="h-4 w-4" aria-hidden="true" />}
          {attentionPillText(attention.length)}
        </span>
        <div className="min-w-0 text-sm leading-snug sm:text-[15px]">
          {!calm && (
            <span className="mr-1.5 font-bold" data-testid="attention-list">
              Needs a look: {attention.map((a) => a.label).join(", ")}.
            </span>
          )}
          <span className="font-display font-semibold" data-testid="right-now-sentence">
            {sentence}
          </span>
        </div>
      </div>

      <div className="flex flex-shrink-0 items-center gap-4 border-t border-white/30 pt-2 lg:border-l lg:border-t-0 lg:pl-4 lg:pt-0">
        <div className="flex flex-col leading-tight">
          <span className="text-[11px]">Trades today</span>
          <span className="tabular-nums text-lg font-bold">{tradesToday}</span>
        </div>
        <div className="flex flex-col leading-tight">
          <span className="text-[11px]">Won vs lost</span>
          <span className="tabular-nums text-lg font-bold">
            <span className="text-lime">{wins}</span> <span className="text-xs font-medium">vs</span> <span>{losses}</span>
          </span>
          <span className="mt-0.5 h-1 w-16 overflow-hidden rounded-full" style={{ background: "rgba(255,255,255,0.3)" }}>
            {wins + losses > 0 && <span className="grow block h-full rounded-full bg-lime" style={{ width: `${winPct}%` }} />}
          </span>
        </div>
        <div className="flex flex-col leading-tight">
          <span className="text-[11px]">{countdownLabel}</span>
          <span className="tabular-nums text-lg font-bold">{countdownValue}</span>
        </div>
      </div>
    </div>
  );
}
