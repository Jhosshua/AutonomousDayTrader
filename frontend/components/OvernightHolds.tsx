// @steered SNARE-2 2026-09-30
"use client";

import { useEffect, useRef, useState } from "react";
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

const REPLY_MS = 8000;

/** PLAN_2026_09_30_overnight_holds.md section 5, minimal version (existing row and button styles, no redesign
 * before the mockups are approved). Holds never get "Sell now" or "Move safety exit": neither can work on them. */
export default function OvernightHolds({ holds, tonight, summary, noBuyActive, disabledReason, onSetNoBuy }: OvernightHoldsProps) {
  const [confirming, setConfirming] = useState(false);
  const [sending, setSending] = useState(false);
  const [reply, setReply] = useState<{ ok: boolean; text: string } | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const replyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
    if (replyTimer.current) clearTimeout(replyTimer.current);
  }, []);

  const disabled = sending || disabledReason != null;

  const click = async () => {
    if (disabled) return;
    const on = !noBuyActive;
    // stopping a buy asks for a second tap, like the other write buttons; turning it back on does not
    if (on && !confirming) {
      setConfirming(true);
      timer.current = setTimeout(() => setConfirming(false), 4000);
      return;
    }
    if (timer.current) clearTimeout(timer.current);
    setConfirming(false);
    setSending(true);
    const res = await onSetNoBuy(on);
    setSending(false);
    setReply({ ok: res.ok, text: res.message || (res.ok ? "Saved." : "Didn't go through, try again.") });
    // the reply answers one tap, so it goes away on its own instead of lingering into later states
    if (replyTimer.current) clearTimeout(replyTimer.current);
    replyTimer.current = setTimeout(() => setReply(null), REPLY_MS);
  };

  const label = sending ? "Saving…" : confirming ? "Tap again to confirm" : noBuyActive ? "Turn tonight's buy back on" : "No overnight buy tonight";

  return (
    <section className="flex flex-col gap-2" data-testid="overnight-holds">
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

      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-2xl border border-line bg-white px-3 py-2.5 sm:px-4" data-testid="overnight-control">
        <div className="min-w-0 flex-1 basis-[240px] text-[13px] leading-snug text-ink">
          {noBuyActive && <div data-testid="overnight-no-buy-on">Tonight&apos;s buy is off. Holds already bought still sell at the next open.</div>}
          {!noBuyActive && summary && <div data-testid="overnight-summary">{summary}</div>}
          {disabledReason && <div className="text-xs text-muted" data-testid="overnight-no-buy-reason">{disabledReason}</div>}
          {reply && (
            <div role="status" className="text-xs font-semibold" style={{ color: reply.ok ? "#0A7D53" : "#C2300F" }} data-testid="overnight-no-buy-reply">
              {reply.text}
            </div>
          )}
        </div>
        <button
          type="button"
          onClick={click}
          disabled={disabled}
          data-testid="btn-no-buy-tonight"
          className="min-h-[44px] flex-1 min-w-[180px] rounded-xl border px-4 text-sm font-semibold disabled:opacity-40 sm:flex-none"
          style={{ borderColor: "#BFE6D3", color: "#0B5A3C", background: "#E9F8F0" }}
        >
          {label}
        </button>
      </div>
    </section>
  );
}
