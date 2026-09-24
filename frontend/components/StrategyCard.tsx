"use client";

import { Sunrise, Waves, Zap, Undo2 } from "lucide-react";
import { StrategyState } from "@/types/trading";
import {
  StrategyLedgerAgg,
  etMinutesOfDay,
  formatSignedMoney,
  isWithinSession,
  rangesToSegments,
  sessionPct,
  strategyNoteLine,
  strategyTheme,
  windowToChip,
} from "@/lib/plain";

const ICONS: Record<string, typeof Sunrise> = {
  orb: Sunrise,
  vwap_pullback: Waves,
  news_momentum: Zap,
  mean_reversion: Undo2,
};

interface StrategyCardProps {
  strategy: StrategyState;
  ledgerAgg?: StrategyLedgerAgg;
  showPro: boolean;
  delayMs?: number;
}

const CHIP_STYLES: Record<string, { bg: string; fg: string }> = {
  sage: { bg: "#FFFFFF", fg: "#2F5A45" },
  lavender: { bg: "#FFFFFF", fg: "#3E4478" },
  grey: { bg: "#FFFFFF", fg: "#5D5A73" },
  terracotta: { bg: "#FFFFFF", fg: "#8F4424" },
};

export default function StrategyCard({ strategy, ledgerAgg, showPro, delayMs = 0 }: StrategyCardProps) {
  const theme = strategyTheme(strategy.id, strategy.name);
  const Icon = ICONS[strategy.id] || Waves;
  const win = strategy.window;
  const chip = windowToChip(win);
  const chipStyle = CHIP_STYLES[chip.tone] || CHIP_STYLES.grey;
  const resting = win?.state === "DONE_FOR_DAY" || win?.state === "PAUSED" || win?.state === "MARKET_CLOSED";

  const segments = rangesToSegments(win?.ranges);
  const nowMin = etMinutesOfDay();
  const showNow = (win?.trading_day ?? true) && isWithinSession(nowMin);
  const nowLeft = sessionPct(nowMin);

  const pnl = ledgerAgg?.realized_pnl ?? strategy.daily_pnl ?? 0;
  const tradesCount = ledgerAgg?.trades_count ?? strategy.trades_count ?? 0;
  const pnlColor = pnl > 0 ? "#2F6B4C" : pnl < 0 ? "#8F4424" : "#5D5A73";

  // F9: an early-close day note must surface even when there's already a signals/orders lead.
  const earlyCloseNote = win?.notes?.find((n) => n.toLowerCase().includes("early"));
  const note = strategyNoteLine(
    strategy.decisions,
    earlyCloseNote ? `${win?.market_text ?? ""} ${earlyCloseNote}`.trim() : win?.market_text,
    win?.notes?.[0]
  );

  return (
    <article
      className="rise hover-card flex min-h-[380px] sm:min-h-[420px] flex-col overflow-hidden rounded-[26px] border border-line bg-white"
      style={{ animationDelay: `${delayMs}ms` }}
    >
      <div
        className="flex flex-col gap-4 px-5 py-5"
        style={{ background: theme.band, filter: resting ? "saturate(0.55) brightness(1.03)" : undefined }}
      >
        <div className="flex items-start justify-between">
          <div className="bob flex h-11 w-11 sm:h-12 sm:w-12 items-center justify-center rounded-2xl" style={{ background: theme.bar }}>
            <Icon className="h-5 w-5 sm:h-6 sm:w-6 text-white" strokeWidth={1.9} aria-hidden="true" />
          </div>
          <span
            className="inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-semibold"
            style={{ background: chipStyle.bg, color: chipStyle.fg }}
            data-testid="window-badge"
          >
            <span
              className={chip.breathing ? "breathe inline-block h-2 w-2 rounded-full" : "inline-block h-2 w-2 rounded-full"}
              style={{ background: resting ? "#A7A2B8" : theme.bar }}
            />
            {chip.label}
          </span>
        </div>
        <h3 className="font-display text-xl sm:text-2xl font-semibold" style={{ color: theme.ink }}>
          {theme.name}
        </h3>
      </div>

      <div className="flex flex-grow flex-col gap-4 px-5 py-5">
        {showPro && (
          <div className="flex flex-col gap-1 self-start rounded-lg px-2.5 py-1.5 text-xs font-semibold" style={{ background: theme.tint, color: theme.ink }}>
            <span>Pro name: {strategy.name}</span>
            <span className="font-normal">
              Win rate: {ledgerAgg ? `${Math.round((ledgerAgg.wins / Math.max(1, ledgerAgg.trades_count)) * 100)}%` : `${Math.round((strategy.win_rate ?? 0) * 100)}%`}
            </span>
            {win?.blockers && win.blockers.length > 0 && <span className="font-normal">Blocked by: {win.blockers.join(", ")}</span>}
          </div>
        )}
        <p className="text-sm leading-relaxed text-[#3E3A57]">{theme.what}</p>

        <div className="mt-auto flex flex-col gap-1.5" data-testid="strategy-window">
          <div className="relative h-3 rounded-full" style={{ background: theme.track }}>
            {segments.map((seg, i) => (
              <div
                key={i}
                className="grow absolute inset-y-0 rounded-md"
                style={{ left: `${seg.left}%`, width: `${seg.width}%`, background: theme.bar, opacity: resting ? 0.5 : 1 }}
              />
            ))}
            {showNow && (
              <div
                className="breathe absolute -top-1 h-5 w-[3px] rounded-sm"
                style={{ left: `${nowLeft}%`, background: "#1D1A33" }}
              />
            )}
          </div>
          <div className="flex justify-between text-xs text-muted">
            <span>9:30</span>
            <span>noon</span>
            <span>4 PM</span>
          </div>
        </div>

        <div
          className="flex items-start justify-between gap-3 rounded-2xl px-3.5 py-3 text-sm leading-snug text-[#3E3A57]"
          style={{ background: theme.tint }}
          data-testid="strategy-decisions"
        >
          <span>{note}</span>
          <span className="flex-shrink-0 tabular-nums text-base font-bold" style={{ color: pnlColor }}>
            {tradesCount > 0 ? formatSignedMoney(pnl) : "$0"}
          </span>
        </div>
      </div>
    </article>
  );
}
