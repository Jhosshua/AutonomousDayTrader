"use client";

import { useEffect, useState } from "react";
import { RecoveredSessionSummary, TradeHistoryResponse, TradeRecord } from "@/types/trading";
import { companyName, etTimeLabel, formatMoney, formatSignedMoney, historyDateLabel, strategyTheme } from "@/lib/plain";
import { apiBase } from "@/lib/apiBase";

interface RecentTradesProps {
  items: TradeRecord[];
  loading: boolean;
  error: string | null;
  onSeeAll: () => void;
}

export default function RecentTrades({ items, loading, error, onSeeAll }: RecentTradesProps) {
  const [recentDays, setRecentDays] = useState<RecoveredSessionSummary[]>([]);
  useEffect(() => {
    if (loading || items.length) return;
    const controller = new AbortController();
    void fetch(`${apiBase()}/api/trades?range=7d&limit=1`, { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("History unavailable");
        const history = await response.json() as TradeHistoryResponse;
        setRecentDays((history.sessions ?? []).slice(0, 3));
      }).catch(() => { /* The history button still offers a retryable full view. */ });
    return () => controller.abort();
  }, [loading, items.length]);
  const sorted = [...items].sort((a, b) => new Date(b.closed_at).getTime() - new Date(a.closed_at).getTime());
  const top6 = sorted.slice(0, 6);

  return (
    <div className="rise hover-card flex flex-col gap-1 rounded-[28px] border border-line bg-white p-6 sm:p-7" style={{ animationDelay: "300ms" }}>
      <div className="mb-2 flex items-baseline justify-between">
        <h2 className="font-display text-xl sm:text-2xl font-semibold text-ink">What it did today</h2>
        <button
          type="button"
          onClick={onSeeAll}
          className="min-h-[44px] px-1 text-sm font-semibold underline-offset-2 hover:underline"
          style={{ color: "#4A5190" }}
        >
          Trade history
        </button>
      </div>

      {loading && items.length === 0 ? (
        <div className="py-8 text-center text-sm text-muted">Loading today's trades…</div>
      ) : error ? (
        <div className="py-6 text-center text-sm" style={{ color: "#8F4424" }}>
          Couldn't load today's trades
        </div>
      ) : top6.length === 0 ? (
        <div className="py-3 text-sm text-muted">
          <p className="mb-3">No finished trades today. Your previous days are saved in Trade history.</p>
          {recentDays.map((day) => (
            <button key={day.session_date} type="button" onClick={onSeeAll}
              className="mt-2 flex min-h-[52px] w-full items-center justify-between gap-3 rounded-xl border border-line p-3 text-left hover:bg-[#FAF8F2]">
              <span><span className="block font-semibold text-ink">{historyDateLabel(day.session_date)}</span>
                <span className="text-xs">{day.trades_count} finished {day.trades_count === 1 ? "trade" : "trades"}</span></span>
              <span className="font-bold tabular-nums" style={{ color: day.realized_pnl < 0 ? "#8F4424" : "#2F6B4C" }}>{formatSignedMoney(day.realized_pnl)}</span>
            </button>
          ))}
        </div>
      ) : (
        top6.map((t) => {
          const theme = strategyTheme(t.strategy_id, t.strategy_id);
          const won = t.realized_pnl >= 0;
          const isLong = t.side === "LONG";
          return (
            <div
              key={t.trade_id}
              className="grid grid-cols-[40px_minmax(0,1fr)_auto] sm:grid-cols-[76px_44px_minmax(0,1fr)_auto] items-center gap-3 border-t py-3 first:border-t-0"
              style={{ borderColor: "#F5EEE2" }}
            >
              <div className="hidden tabular-nums text-sm text-muted sm:block">{etTimeLabel(t.closed_at)}</div>
              <div
                className="flex h-9 w-9 sm:h-10 sm:w-10 items-center justify-center rounded-2xl text-lg font-bold"
                style={{ background: theme.track, color: theme.ink }}
              >
                {isLong ? "↑" : "↓"}
              </div>
              <div className="flex min-w-0 flex-col gap-0.5">
                <div className="text-sm sm:text-base font-semibold text-ink">
                  {companyName(t.symbol)} <span className="text-xs font-medium text-muted">{t.symbol}</span>
                </div>
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs sm:text-sm text-muted">
                  <span className="whitespace-nowrap rounded-md px-2 py-0.5 text-xs font-semibold" style={{ background: theme.tint, color: theme.ink }}>
                    {theme.name}
                  </span>
                  <span className="whitespace-nowrap">{isLong ? "bet it goes up" : "bet it goes down"}<span className="tabular-nums sm:hidden"> · {etTimeLabel(t.closed_at)}</span></span>
                </div>
              </div>
              <div
                className="whitespace-nowrap rounded-full px-2.5 py-1 text-sm font-bold tabular-nums sm:px-3 sm:py-1.5"
                style={won ? { background: "#E4EFE7", color: "#2F6B4C" } : { background: "#F6E3DA", color: "#8F4424" }}
              >
                {won ? "+" : "-"}
                {formatMoney(Math.abs(t.realized_pnl))}
              </div>
            </div>
          );
        })
      )}
    </div>
  );
}
