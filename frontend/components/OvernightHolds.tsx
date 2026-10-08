// @steered SNARE-2 2026-09-30
"use client";

import NoBuyTonightButton from "./gut/NoBuyTonightButton";
import { Moon } from "lucide-react";
import { holdLine, strategyTheme } from "@/lib/plain";

/** One hold as the page shows it (from the position, enriched by the overnight payload). */
export interface HoldView {
  symbol: string;
  strategyId: string;
  shares: number;
  buyPrice: number | null;
  saleDate: string | null;
  nights: string | null;
  needsLook: boolean;
  bookOnly?: boolean;
}

interface OvernightHoldsProps {
  holds: HoldView[];
  /** From 3:45 PM, one line per stock not bought yet tonight. */
  tonight: { symbol: string; name: string; text: string }[];
  /** "Tonight it buys ..." while the control can still be used, else null. */
  summary: string | null;
  noBuyActive: boolean;
  disabledReason: string | null;
  onSetNoBuy: (on: boolean) => Promise<{ ok: boolean; message: string }>;
}


/** PLAN_2026_09_30_overnight_holds.md section 5, minimal version (existing row and button styles, no redesign
 * before the mockups are approved). Holds never get "Sell now" or "Move safety exit": neither can work on them. */
export default function OvernightHolds({ holds, tonight, summary, noBuyActive, disabledReason, onSetNoBuy }: OvernightHoldsProps) {

  return (
    <section id="overnight-holds" className="flex scroll-mt-4 flex-col gap-2" data-testid="overnight-holds">
      <h2 className="font-display text-lg font-semibold text-ink">Overnight holds</h2>

      {holds.map((h) => {
        const theme = strategyTheme(h.strategyId, `${h.symbol} overnight`);
        return (
          <div key={h.symbol} className="rise flex flex-col overflow-hidden rounded-2xl border border-line bg-white" data-testid={`overnight-hold-${h.symbol}`}>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-3 py-2.5 sm:px-4">
              <div className="flex items-center gap-2 rounded-lg px-2.5 py-1.5" style={{ background: theme.band, color: theme.ink }} data-testid="overnight-strategy">
                <Moon className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
                <span className="font-display text-[15px] font-semibold leading-tight">{theme.name}</span>
              </div>
              <div className="min-w-0 flex-1 basis-[260px] text-[13px] leading-snug text-ink" data-testid="overnight-hold-line">
                {holdLine({ symbol: h.symbol, shares: h.shares, buyPrice: h.buyPrice, saleDate: h.saleDate, nights: h.nights, needsLook: h.needsLook, bookOnly: h.bookOnly })}
              </div>
            </div>
          </div>
        );
      })}

      {tonight.length > 0 && (
        <div className="flex flex-col gap-1 rounded-2xl border border-line bg-white px-3 py-2.5 text-[13px] leading-snug text-ink sm:px-4" data-testid="overnight-tonight">
          {tonight.map((t) => (
            <div key={t.symbol} data-testid={`overnight-tonight-${t.symbol}`}>
              <span className="font-semibold">{t.name}.</span> {t.text}
            </div>
          ))}
        </div>
      )}

      <NoBuyTonightButton {...{ noBuyActive, disabledReason, summary, onSetNoBuy }} />
    </section>
  );
}
