"use client";

import { useMemo, useState } from "react";
import { useTradingStream } from "@/hooks/useTradingStream";
import { useTodayLedger } from "@/hooks/useTodayLedger";
import { useHealthLimits } from "@/hooks/useHealthLimits";
import Header, { useProWordsToggle } from "@/components/Header";
import SegmentedModeToggle, { TradingMode } from "@/components/SegmentedModeToggle";
import BalanceCard from "@/components/BalanceCard";
import RightNowCard from "@/components/RightNowCard";
import StrategyCarousel from "@/components/StrategyCarousel";
import HoldingNow from "@/components/HoldingNow";
import RecentTrades from "@/components/RecentTrades";
import SafetyCard from "@/components/SafetyCard";
import SwingTelemetryBar from "@/components/SwingTelemetryBar";
import SwingCandidateWatchlist from "@/components/SwingCandidateWatchlist";
import ActiveSwingPositionsTable from "@/components/ActiveSwingPositionsTable";
import TradeHistory from "@/components/TradeHistory";
import ExecutionLog from "@/components/ExecutionLog";
import { AlertTriangle, WifiOff } from "lucide-react";
import { countdownToClose, groupLedgerByStrategy, rightNowSentence } from "@/lib/plain";

export default function Home() {
  const {
    state,
    isConnected,
    hasReceivedData,
    connectionState,
    flattenPosition,
    flattenAll,
    tightenStop,
    swingExitNextOpen,
    swingExitImmediate,
    swingTightenStop,
  } = useTradingStream();

  const [showPro, setShowPro] = useProWordsToggle();
  const [mode, setMode] = useState<TradingMode>("intraday");
  const [historyOpen, setHistoryOpen] = useState(false);

  const todayLedger = useTodayLedger(state.ledger_revision, isConnected);
  const healthLimits = useHealthLimits();

  // F4: recovered aggregate sessions add their strategy aggregates to the per-strategy totals
  // once (their trades never draw chart steps - BalanceCard only ever reads todayLedger.items).
  const ledgerByStrategy = useMemo(() => {
    const base = groupLedgerByStrategy(todayLedger.items);
    for (const session of todayLedger.recoveredSessions) {
      for (const [sid, agg] of Object.entries(session.strategies || {})) {
        const existing = base[sid] || { realized_pnl: 0, trades_count: 0, wins: 0, losses: 0 };
        base[sid] = {
          ...existing,
          realized_pnl: Math.round((existing.realized_pnl + agg.realized_pnl) * 100) / 100,
          trades_count: existing.trades_count + agg.trades_count,
        };
      }
    }
    return base;
  }, [todayLedger.items, todayLedger.recoveredSessions]);

  // F1: split holdings by arm. Quick-trade holdings = all_positions whose symbol is NOT
  // currently a swing holding.
  const swingSymbols = useMemo(
    () => new Set((state.swing?.positions ?? []).map((p) => p.symbol)),
    [state.swing?.positions]
  );
  const intradayPositions = useMemo(
    () => state.all_positions.filter((p) => !swingSymbols.has(p.symbol)),
    [state.all_positions, swingSymbols]
  );

  const tradesToday = todayLedger.summary?.trades_count ?? 0;
  const wins = todayLedger.summary?.wins ?? 0;
  const losses = todayLedger.summary?.losses ?? 0;

  const firstTradingDayFlag = state.strategies.find((s) => s.window)?.window?.trading_day;
  const countdownValue = countdownToClose(new Date(), firstTradingDayFlag);

  const heroSentence = rightNowSentence({
    isCircuitBroken: state.account.is_circuit_broken,
    marketStatus: state.market_context.market_status,
    strategies: state.strategies,
    positionsCount: intradayPositions.length,
    maxDailyLossDollars: healthLimits.maxDailyLossDollars,
  });

  // F8: always-visible plain-language problem banners (not behind "Show pro words").
  const feedDown = Object.values(state.ingestion || {}).length > 0 &&
    Object.values(state.ingestion || {}).every((v) => v !== "connected");
  const savingProblem = state.persistence.status !== "durable" && state.persistence.status !== "disabled";

  // F7: the brand header is static copy, not account data, so it renders immediately - only
  // the financial content area waits for a real snapshot instead of showing synthetic zeros.
  if (!hasReceivedData) {
    return (
      <main className="min-h-screen bg-ground px-4 py-6 sm:px-10 sm:py-10">
        <div className="mx-auto flex max-w-6xl flex-col gap-6 sm:gap-7">
          <Header isConnected={isConnected} showPro={showPro} onTogglePro={setShowPro} />
          <div className="flex flex-col items-center justify-center gap-3 py-24 text-center">
            <div className="h-10 w-10 animate-spin rounded-full border-4 border-line border-t-darkcard" aria-hidden="true" />
            <p className="text-sm text-muted">Connecting to the robot…</p>
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="relative min-h-screen overflow-x-hidden bg-ground px-4 py-6 sm:px-10 sm:py-10">
      <div className="drift pointer-events-none absolute -left-40 -top-52 h-[420px] w-[420px] rounded-full opacity-70 blur-3xl" style={{ background: "#F3E1CF" }} />
      <div className="drift2 pointer-events-none absolute -right-40 -top-40 h-[420px] w-[420px] rounded-full opacity-80 blur-3xl" style={{ background: "#E2E1F1" }} />

      <div className="relative mx-auto flex max-w-6xl flex-col gap-6 sm:gap-7">
        <Header isConnected={isConnected} showPro={showPro} onTogglePro={setShowPro} />

        {showPro && (
          <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-line bg-white/70 px-4 py-2 text-xs text-muted">
            <span>VIX regime: {state.market_context.vix_regime} ({state.market_context.vix.toFixed(1)})</span>
            {Object.entries(state.ingestion || {}).map(([feed, status]) => (
              <span key={feed} className="flex items-center gap-1.5">
                <span
                  className="inline-block h-2 w-2 rounded-full"
                  style={{ background: status === "connected" ? "#5E9A7A" : "#A9553A" }}
                />
                {feed}
              </span>
            ))}
          </div>
        )}

        {connectionState !== "live" && (
          <div
            role="status"
            className="rounded-2xl border px-4 py-3 text-sm flex items-center gap-2"
            style={{ borderColor: "#EFD8C5", background: "#FAF0E6", color: "#7A3E1D" }}
          >
            <WifiOff className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
            {connectionState === "reconnecting"
              ? "Lost connection to the robot. Showing the last numbers it sent. Reconnecting..."
              : "Numbers may be old. Still trying to reach the robot."}
          </div>
        )}
        {feedDown && (
          <div role="status" className="rounded-2xl border px-4 py-3 text-sm" style={{ borderColor: "#EFD8C5", background: "#FAF0E6", color: "#7A3E1D" }}>
            <AlertTriangle className="mr-2 inline h-4 w-4" aria-hidden="true" />
            Price feed is down, it can't trade right now.
          </div>
        )}
        {savingProblem && (
          <div role="status" className="rounded-2xl border px-4 py-3 text-sm" style={{ borderColor: "#EFD8C5", background: "#FAF0E6", color: "#7A3E1D" }}>
            <AlertTriangle className="mr-2 inline h-4 w-4" aria-hidden="true" />
            Saving problems: new trades are paused until this is fixed.
          </div>
        )}
        {state.account.is_circuit_broken && (
          <div role="status" className="rounded-2xl border px-4 py-3 text-sm" style={{ borderColor: "#EFD8C5", background: "#FAF0E6", color: "#7A3E1D" }}>
            Stopped for today. It hit the daily loss limit.
          </div>
        )}
        {state.swing?.last_close_entries_withheld && (
          <div role="status" className="rounded-2xl border px-4 py-3 text-sm" style={{ borderColor: "#D9DCEB", background: "#EEEFF7", color: "#3E4478" }}>
            Slow trades: last close data was incomplete, so no new slow trades were bought overnight.
          </div>
        )}

        <section className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          <BalanceCard
            equity={state.account.equity}
            dailyPnl={state.account.daily_pnl}
            todayTrades={todayLedger.items}
            loading={todayLedger.loading && todayLedger.items.length === 0}
          />
          <RightNowCard
            sentence={heroSentence}
            tradesToday={tradesToday}
            wins={wins}
            losses={losses}
            countdownLabel="Quick trades close in"
            countdownValue={countdownValue}
          />
        </section>

        <SegmentedModeToggle mode={mode} onModeChange={setMode} />

        {mode === "intraday" ? (
          <div className="fadein flex flex-col gap-6 sm:gap-7">
            <StrategyCarousel strategies={state.strategies} ledgerByStrategy={ledgerByStrategy} showPro={showPro} />

            <HoldingNow positions={intradayPositions} onFlattenPosition={flattenPosition} onTightenStop={tightenStop} />

            <section className="grid grid-cols-1 lg:grid-cols-[1.5fr_1fr] gap-5">
              <RecentTrades
                items={todayLedger.items}
                loading={todayLedger.loading}
                error={todayLedger.error}
                onSeeAll={() => setHistoryOpen(true)}
              />
              <SafetyCard
                drawdownDollars={state.account.daily_drawdown}
                maxDailyLossDollars={healthLimits.maxDailyLossDollars}
                baseTradeRiskPct={healthLimits.baseTradeRiskPct}
                intradayPositionsCount={intradayPositions.length}
                onFlattenAll={flattenAll}
              />
            </section>
          </div>
        ) : (
          <div className="fadein flex flex-col gap-6 sm:gap-7">
            <SwingTelemetryBar swingState={state.swing} showPro={showPro} />
            <ActiveSwingPositionsTable
              positions={state.swing?.positions ?? []}
              onExitNextOpen={swingExitNextOpen}
              onExitImmediate={swingExitImmediate}
              onTightenStop={swingTightenStop}
            />
            <SwingCandidateWatchlist candidates={state.swing?.candidates ?? []} />
          </div>
        )}

        {showPro && <ExecutionLog records={state.recent_activity} maxItems={20} />}
      </div>

      <TradeHistory
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        ledgerRevision={state.ledger_revision}
        streamPersistence={state.persistence}
      />
    </main>
  );
}
