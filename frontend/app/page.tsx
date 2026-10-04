// @steered SNARE-2 2026-09-30
"use client";

import { useMemo, useState } from "react";
import { useTradingStream } from "@/hooks/useTradingStream";
import { useLedger, useTodayLedger } from "@/hooks/useTodayLedger";
import { useHealthLimits } from "@/hooks/useHealthLimits";
import Header, { useProWordsToggle } from "@/components/Header";
import SegmentedModeToggle, { TradingMode } from "@/components/SegmentedModeToggle";
import BalanceCard from "@/components/BalanceCard";
import RightNowCard from "@/components/RightNowCard";
import StrategyTable from "@/components/StrategyTable";
import HoldingNow from "@/components/HoldingNow";
import MarketMoodCard from "@/components/MarketMoodCard";
import ResultsPanel from "@/components/ResultsPanel";
import SafetyCard from "@/components/SafetyCard";
import SwingTelemetryBar from "@/components/SwingTelemetryBar";
import SwingCandidateWatchlist from "@/components/SwingCandidateWatchlist";
import ActiveSwingPositionsTable from "@/components/ActiveSwingPositionsTable";
import ExecutionLog from "@/components/ExecutionLog";
import OvernightHolds, { HoldView } from "@/components/OvernightHolds";
import { AlertTriangle, WifiOff } from "lucide-react";
import {
  countdownToClose,
  etDateKey,
  etDateOfIso,
  etMinutesOfDay,
  etParts,
  groupLedgerByStrategy,
  ledgerHistoryLine,
  isOvernightPosition,
  joinNames,
  noBuyDisabledReason,
  overnightBalanceNote,
  overnightHoldingSentence,
  rightNowSentence,
  tonightStatusLine,
  unsoldBannerText,
} from "@/lib/plain";

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
    setNoBuyTonight,
  } = useTradingStream();

  const [showPro, setShowPro] = useProWordsToggle();
  const [mode, setMode] = useState<TradingMode>("intraday");

  const todayLedger = useTodayLedger(state.ledger_revision, isConnected);
  // every day and every trade since the start: the Results panel and the Balance card's "Since start" line
  const allLedger = useLedger("all", state.ledger_revision, isConnected);
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
  // currently a swing holding, and not an overnight hold (those have their own list).
  const swingSymbols = useMemo(
    () => new Set((state.swing?.positions ?? []).map((p) => p.symbol)),
    [state.swing?.positions]
  );
  const intradayPositions = useMemo(
    () => state.all_positions.filter((p) => !swingSymbols.has(p.symbol) && !isOvernightPosition(p)),
    [state.all_positions, swingSymbols]
  );

  const tradesToday = todayLedger.summary?.trades_count ?? 0;
  const wins = todayLedger.summary?.wins ?? 0;
  const losses = todayLedger.summary?.losses ?? 0;

  const firstTradingDayFlag = state.strategies.find((s) => s.window)?.window?.trading_day;

  // Overnight holds (PLAN_2026_09_30_overnight_holds.md section 5). Everything below reads the
  // websocket "overnight" payload and the positions. With neither (older backend) nothing changes.
  const now = new Date();
  const nowMs = now.getTime();
  const etMin = etMinutesOfDay(now);
  const todayEt = etDateKey(now);
  const ovn = state.overnight ?? null;
  const holdViews: HoldView[] = state.all_positions.filter(isOvernightPosition).map((p) => {
    const h = ovn?.holds?.find((x) => x.symbol === p.symbol);
    const row = ovn?.rows?.find((x) => x.symbol === p.symbol && (x.needs_look ?? []).includes("BOOK_MORE_THAN_ALPACA"));
    return {
      symbol: p.symbol,
      strategyId: p.strategy_id || h?.strategy_id || "",
      shares: p.shares,
      buyPrice: p.entry_price ?? h?.buy_avg ?? null,
      saleDate: h?.sale_date ?? etDateOfIso(p.exit_due),
      nights: h?.nights ?? null,
      needsLook: (h?.needs_look?.length ?? 0) > 0 || row != null,
      // Alpaca sold it but the robot's book still shows shares: not a normal hold
      bookOnly: !h && row != null,
    };
  });
  const overnightOn = ovn != null || holdViews.length > 0;
  const ovnRunning = ovn?.state?.running === true;
  const ovnBuysOn = ovnRunning && ovn?.state?.mode === "live";
  const tradingDayToday = firstTradingDayFlag ?? (etParts(now).weekday !== 0 && etParts(now).weekday !== 6);
  const noBuyActive = ovn?.no_buy_tonight === true;
  const ovnRows = (ovn?.rows ?? []).filter((r) => (ovn?.settings?.enabled ?? []).includes(r.symbol) || r.enabled);
  // a stock still counts as planned tonight until its night is skipped or bought
  const plannedTonight = ovnRows.filter((r) => {
    if (!r.enabled) return false;
    if ((r.needs_look ?? []).includes("BOOK_MORE_THAN_ALPACA")) return false; // the backend skips it
    if (r.buy_date === todayEt) return r.state !== "SKIPPED" && !["HELD", "SALE_QUEUED", "SOLD"].includes(r.state || "");
    return !["HELD", "SALE_QUEUED"].includes(r.state || "") && !noBuyActive;
  });
  const noBuyUntilMs = ovn?.no_buy_until ? Date.parse(ovn.no_buy_until) : NaN;
  const tooLate = Number.isFinite(noBuyUntilMs) && etDateOfIso(ovn?.no_buy_until) === todayEt ? nowMs >= noBuyUntilMs : etMin >= 15 * 60 + 49;
  const disabledReason = noBuyDisabledReason({
    running: ovnRunning,
    modeOn: ovnBuysOn,
    enabledCount: ovn?.settings?.enabled?.length ?? 0,
    tradingDay: tradingDayToday,
    active: noBuyActive,
    tooLate,
    alreadyStopped: ovnRows.some((r) => r.buy_date === todayEt && r.reason === "OPERATOR_NO_BUY_TONIGHT"),
    nothingPlanned: plannedTonight.length === 0,
  });
  const tonightLines =
    ovn && ovnRunning && tradingDayToday && etMin >= 15 * 60 + 45
      ? (ovn.rows ?? []).flatMap((r) => {
          const text = tonightStatusLine(r, { modeOn: ovnBuysOn, noBuyTonight: noBuyActive, today: todayEt, etMin });
          return text ? [{ symbol: r.symbol, name: r.name || `${r.symbol} overnight`, text }] : [];
        })
      : [];
  const overnightSummary =
    ovnBuysOn && tradingDayToday && !tooLate && etMin < 15 * 60 + 45 && plannedTonight.length > 0
      ? `Tonight it buys ${joinNames(plannedTonight.map((r) => r.symbol))} at the 4:00 PM close and sells at the next 9:30 AM open.`
      : null;
  // unsold after 9:31 AM: the backend list, plus any hold whose sale time is more than a minute past
  const unsold = Array.from(new Set([
    ...(ovn?.unsold_after_0931 ?? ovn?.state?.unsold_after_0931 ?? []),
    ...state.all_positions
      .filter((p) => isOvernightPosition(p) && p.exit_due && nowMs >= Date.parse(p.exit_due) + 60_000)
      .map((p) => p.symbol),
  ]));
  const firstSale = holdViews.filter((h) => !h.bookOnly).map((h) => h.saleDate).filter((d): d is string => !!d).sort()[0] ?? null;
  const pastSale = state.all_positions.some((p) => isOvernightPosition(p) && p.exit_due && nowMs >= Date.parse(p.exit_due));
  const holdsValue = holdViews.reduce((sum, h) => sum + h.shares * (h.buyPrice ?? 0), 0);

  // X6: a quick trade in a stock bought overnight tonight closes at 3:46 PM, not 3:55 PM
  const x6Symbol =
    ovnBuysOn && etMin < 15 * 60 + 46
      ? intradayPositions.find((p) => plannedTonight.some((r) => r.symbol === p.symbol))?.symbol ?? null
      : null;
  const countdownValue = countdownToClose(now, firstTradingDayFlag, x6Symbol ? 15 * 60 + 46 : undefined);
  const countdownLabel = x6Symbol ? `${x6Symbol} quick trade closes in` : "Quick trades close in";

  const heroSentence = rightNowSentence({
    isCircuitBroken: state.account.is_circuit_broken,
    marketStatus: state.market_context.market_status,
    strategies: state.strategies,
    positionsCount: intradayPositions.length,
    maxDailyLossDollars: healthLimits.maxDailyLossDollars,
    overnightOn,
    overnightLine: overnightHoldingSentence(holdViews.filter((h) => !h.bookOnly).length, firstSale, pastSale),
  });

  // F8: always-visible plain-language problem banners (not behind "Show pro words").
  const feedDown = Object.values(state.ingestion || {}).length > 0 &&
    Object.values(state.ingestion || {}).every((v) => v !== "connected");
  const savingProblem = state.persistence.status !== "durable" && state.persistence.status !== "disabled";

  // F7: the brand header is static copy, not account data, so it renders immediately - only
  // the financial content area waits for a real snapshot instead of showing synthetic zeros.
  if (!hasReceivedData) {
    return (
      <main className="min-h-screen bg-ground px-3 py-4 sm:px-6 sm:py-5">
        <div className="mx-auto flex max-w-[1400px] flex-col gap-4">
          <Header isConnected={isConnected} broker={state.broker} showPro={showPro} onTogglePro={setShowPro} />
          <div className="flex flex-col items-center justify-center gap-3 py-24 text-center">
            <div className="h-10 w-10 animate-spin rounded-full border-4 border-line border-t-darkcard" aria-hidden="true" />
            <p className="text-sm text-muted">Connecting to the robot…</p>
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="relative min-h-screen overflow-x-hidden bg-ground px-3 py-4 sm:px-6 sm:py-5">
      <div className="relative mx-auto flex max-w-[1400px] flex-col gap-3">
        <Header isConnected={isConnected} broker={state.broker} showPro={showPro} onTogglePro={setShowPro}>
          <SegmentedModeToggle mode={mode} onModeChange={setMode} />
        </Header>

        {showPro && (
          <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-line bg-white/70 px-4 py-2 text-xs text-muted">
            <span>VIX regime: {state.market_context.vix_regime} ({state.market_context.vix != null ? state.market_context.vix.toFixed(1) : "no reading"})</span>
            {Object.entries(state.ingestion || {}).map(([feed, status]) => (
              <span key={feed} className="flex items-center gap-1.5">
                <span
                  className="inline-block h-2 w-2 rounded-full"
                  style={{ background: status === "connected" ? "#0A7D53" : "#C2300F" }}
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
            style={{ borderColor: "#F0D79A", background: "#FFF4DB", color: "#8A4B00" }}
          >
            <WifiOff className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
            {connectionState === "reconnecting"
              ? "Lost connection to the robot. Showing the last numbers it sent. Reconnecting..."
              : "Numbers may be old. Still trying to reach the robot."}
          </div>
        )}
        {feedDown && (
          <div role="status" className="rounded-2xl border px-4 py-3 text-sm" style={{ borderColor: "#F0D79A", background: "#FFF4DB", color: "#8A4B00" }}>
            <AlertTriangle className="mr-2 inline h-4 w-4" aria-hidden="true" />
            Price feed is down, it can't trade right now.
          </div>
        )}
        {state.broker?.mismatch && (
          <div role="status" className="rounded-2xl border px-4 py-3 text-sm" style={{ borderColor: "#F0D79A", background: "#FFF4DB", color: "#8A4B00" }}>
            <AlertTriangle className="mr-2 inline h-4 w-4" aria-hidden="true" />
            The robot's positions don't match the Alpaca account. New trades are paused until they match.
          </div>
        )}
        {savingProblem && (
          <div role="status" className="rounded-2xl border px-4 py-3 text-sm" style={{ borderColor: "#F0D79A", background: "#FFF4DB", color: "#8A4B00" }}>
            <AlertTriangle className="mr-2 inline h-4 w-4" aria-hidden="true" />
            Saving problems: new trades are paused until this is fixed.
          </div>
        )}
        {unsold.length > 0 && (
          <div role="alert" className="rounded-2xl border px-4 py-3 text-sm font-semibold" style={{ borderColor: "#F5B7A8", background: "#FFEFEA", color: "#C2300F" }} data-testid="overnight-unsold-banner">
            <AlertTriangle className="mr-2 inline h-4 w-4" aria-hidden="true" />
            {unsoldBannerText(unsold)}
          </div>
        )}
        {state.account.is_circuit_broken && (
          <div role="status" className="rounded-2xl border px-4 py-3 text-sm" style={{ borderColor: "#F0D79A", background: "#FFF4DB", color: "#8A4B00" }}>
            {overnightOn ? "Day trading stopped for today. It hit the daily loss limit." : "Stopped for today. It hit the daily loss limit."}
          </div>
        )}
        {state.swing?.last_close_entries_withheld && (
          <div role="status" className="rounded-2xl border px-4 py-3 text-sm" style={{ borderColor: "#DDD2FF", background: "#F0EBFF", color: "#4A2AB5" }}>
            Slow trades: last close data was incomplete, so no new slow trades were bought overnight.
          </div>
        )}

        <section className="grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.3fr)]">
          <BalanceCard
            equity={state.account.equity}
            dailyPnl={state.account.daily_pnl}
            todayTrades={todayLedger.items}
            loading={todayLedger.loading && todayLedger.items.length === 0}
            overnightNote={holdViews.length > 0 ? overnightBalanceNote(holdsValue) : null}
            history={ledgerHistoryLine(allLedger, todayEt)}
          />
          <RightNowCard
            sentence={heroSentence}
            tradesToday={tradesToday}
            wins={wins}
            losses={losses}
            countdownLabel={countdownLabel}
            countdownValue={countdownValue}
          />
        </section>

        {overnightOn && (
          <OvernightHolds
            holds={holdViews}
            tonight={tonightLines}
            summary={overnightSummary}
            noBuyActive={noBuyActive}
            disabledReason={disabledReason}
            onSetNoBuy={setNoBuyTonight}
          />
        )}

        {mode === "intraday" ? (
          <div className="fadein grid grid-cols-1 items-start gap-3 lg:grid-cols-[minmax(0,1fr)_380px]">
            <div className="flex min-w-0 flex-col gap-3">
              <HoldingNow
                positions={intradayPositions}
                marketContext={state.market_context}
                onFlattenPosition={flattenPosition}
                onTightenStop={tightenStop}
              />

              <MarketMoodCard context={state.market_context} tradingDay={firstTradingDayFlag} />

              <StrategyTable strategies={state.strategies} ledgerByStrategy={ledgerByStrategy} showPro={showPro} />
            </div>

            <div className="flex min-w-0 flex-col gap-3">
              <SafetyCard
                drawdownDollars={state.account.risk_drawdown ?? state.account.daily_drawdown}
                maxDailyLossDollars={healthLimits.maxDailyLossDollars}
                baseTradeRiskPct={healthLimits.baseTradeRiskPct}
                intradayPositionsCount={intradayPositions.length}
                onFlattenAll={flattenAll}
                overnight={
                  overnightOn
                    ? {
                        buysOn: ovnBuysOn,
                        symbols: ovn?.settings?.enabled?.length ? ovn.settings.enabled : ["NVDA", "IREN", "HUT"],
                        pct: ovn?.settings?.pct ?? null,
                        resultToday: state.account.overnight_realized_today ?? null,
                        holdsCount: holdViews.length,
                      }
                    : null
                }
              />
              <ResultsPanel ledger={allLedger} today={todayEt} streamPersistence={state.persistence} />
            </div>
          </div>
        ) : (
          <div className="fadein flex flex-col gap-4">
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
    </main>
  );
}
