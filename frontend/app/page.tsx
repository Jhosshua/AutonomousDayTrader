// @steered SNARE-2 2026-09-30
"use client";

import { useMemo, useState } from "react";
import { useTradingStream } from "@/hooks/useTradingStream";
import { useLedger, useTodayLedger } from "@/hooks/useTodayLedger";
import { useHealthLimits } from "@/hooks/useHealthLimits";
import Header, { useProWordsToggle } from "@/components/Header";
import SegmentedModeToggle, { TradingMode } from "@/components/SegmentedModeToggle";
import RightNowCard from "@/components/RightNowCard";
import StrategyTable, { StrategyTableOvernight } from "@/components/StrategyTable";
import HoldingNow from "@/components/HoldingNow";
import MarketMoodCard from "@/components/MarketMoodCard";
import SafetyCard from "@/components/SafetyCard";
import SwingTelemetryBar from "@/components/SwingTelemetryBar";
import SwingCandidateWatchlist from "@/components/SwingCandidateWatchlist";
import ActiveSwingPositionsTable from "@/components/ActiveSwingPositionsTable";
import ExecutionLog from "@/components/ExecutionLog";
import OvernightHolds, { HoldView } from "@/components/OvernightHolds";
import DashboardNavigation, {
  DashboardDestination,
  DashboardPage,
} from "@/components/DashboardNavigation";
import PerformancePanel, {
  PerformancePeriod,
} from "@/components/PerformancePanel";
import HistoryExplorer, {
  HistoryPeriod,
} from "@/components/HistoryExplorer";
import RecentHistory from "@/components/RecentHistory";
import { AlertTriangle, WifiOff } from "lucide-react";
import { aggregatePerformance, PerformanceAggregation } from "@/lib/performance";
import { buildHistoryView } from "@/lib/historyView";
import {
  collectAttention,
  companyName,
  countdownToClose,
  etDateKey,
  etDateOfIso,
  etMinutesOfDay,
  etParts,
  etTimeLabel,
  groupLedgerByStrategy,
  historyDateLabel,
  isFeedDown,
  isOvernightPosition,
  joinNames,
  noBuyDisabledReason,
  overnightHoldingSentence,
  rightNowSentence,
  tonightStatusLine,
  unsoldBannerText,
  strategyTheme,
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
  const [page, setPage] = useState<DashboardPage>("today");
  const [performancePeriod, setPerformancePeriod] =
    useState<PerformancePeriod>("day");

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
  // X6: a quick trade in a stock bought overnight tonight closes at 3:46 PM, not 3:55 PM
  const x6Symbol =
    ovnBuysOn && etMin < 15 * 60 + 46
      ? intradayPositions.find((p) => plannedTonight.some((r) => r.symbol === p.symbol))?.symbol ?? null
      : null;
  // The Overnight playbook row and the 3:45 PM handoff (PLAN_2026_10_05). Only with the overnight payload.
  const tableOvernight: StrategyTableOvernight | null = ovn
    ? {
        inputs: {
          running: ovnRunning,
          modeOn: ovnBuysOn,
          enabled: ovn.settings?.enabled ?? [],
          today: ovn.today ?? null,
          tradingDay: tradingDayToday,
          todayEt,
          etMin,
          tooLate,
          noBuyActive,
          rows: ovn.rows ?? [],
          holds: holdViews.filter((h) => !h.bookOnly).map((h) => ({ symbol: h.symbol, saleDate: h.saleDate })),
          x6: ovn.x6 ?? [],
        },
        realizedToday: state.account.overnight_realized_today ?? ovn.state?.realized_today ?? null,
        sizeNote: ovn.rows?.[0]?.size_note ?? null,
        nameOf: (sid: string) => strategyTheme(sid, state.strategies.find((s) => s.id === sid)?.name ?? sid).name,
      }
    : null;

  const dailyOpeningEquity =
    typeof state.account.daily_starting_equity === "number" &&
    Number.isFinite(state.account.daily_starting_equity)
      ? state.account.daily_starting_equity
      : state.account.equity - state.account.daily_pnl;
  const openHoldingsResult = state.all_positions.reduce(
    (sum, position) =>
      sum +
      (Number.isFinite(position.unrealized_pnl) ? position.unrealized_pnl : 0),
    0,
  );
  const performanceByPeriod = useMemo(() => {
    const build = (period: PerformancePeriod): PerformanceAggregation => {
      const ledger = period === "day" ? todayLedger : allLedger;
      return aggregatePerformance({
        period,
        today: todayEt,
        currentEquity: state.account.equity,
        dailyOpeningEquity,
        openHoldingResult: { dollars: openHoldingsResult },
        sessions: ledger.sessions,
        trades: ledger.items,
        tradesTruncated: ledger.truncated,
        oldestLoadedDate: ledger.oldestLoadedDate,
        historyComplete:
          state.persistence.status === "durable" && ledger.error === null,
        asOf: state.timestamp,
      });
    };
    return {
      day: build("day"),
      week: build("week"),
      month: build("month"),
      all: build("all"),
    };
  }, [
    allLedger,
    dailyOpeningEquity,
    openHoldingsResult,
    state.account.equity,
    state.persistence.status,
    state.timestamp,
    todayEt,
    todayLedger,
  ]);
  const selectedPerformance = performanceByPeriod[performancePeriod];
  const historyData = useMemo(
    () =>
      buildHistoryView({
        performance: performanceByPeriod,
        sessions: allLedger.sessions,
        trades: allLedger.items,
        planName: (strategyId) =>
          strategyTheme(
            strategyId,
            state.strategies.find((strategy) => strategy.id === strategyId)
              ?.name ?? strategyId,
          ).name,
        companyName,
        dateLabel: historyDateLabel,
        timeLabel: etTimeLabel,
      }),
    [allLedger.items, allLedger.sessions, performanceByPeriod, state.strategies],
  );
  const performanceLedger =
    performancePeriod === "day" ? todayLedger : allLedger;
  const performanceHeadline =
    selectedPerformance.accountChange.direction === "unknown"
      ? "Account performance unavailable"
      : performancePeriod === "all"
        ? "Since recorded history"
        : `${selectedPerformance.accountChange.direction === "flat"
            ? "Flat"
            : selectedPerformance.accountChange.direction === "up"
              ? "Up"
              : "Down"} ${
            performancePeriod === "day"
              ? "today"
              : performancePeriod === "week"
                ? "this week"
                : "this month"
          }`;
  const performanceStatus =
    performanceLedger.error !== null
      ? { kind: "error" as const, message: performanceLedger.error }
      : connectionState !== "live"
        ? {
            kind: "reconnecting" as const,
            message: "Showing the latest saved performance while reconnecting.",
          }
        : performanceLedger.loading && performanceLedger.items.length === 0
          ? { kind: "loading" as const, message: "Loading recorded performance" }
          : { kind: "ready" as const };
  const partialHistory =
    selectedPerformance.completeness.partialLabels.length > 0
      ? {
          kind: "partial" as const,
          message: selectedPerformance.completeness.partialLabels.join(" "),
        }
      : { kind: "complete" as const };
  const historyPeriod: HistoryPeriod =
    performancePeriod === "day"
      ? "Day"
      : performancePeriod === "week"
        ? "Week"
        : performancePeriod === "month"
          ? "Month"
          : "All";
  const chartPoints = selectedPerformance.chart.points.map((point) => ({
    value: point.value,
    label:
      point.source === "live"
        ? `Live ${etTimeLabel(point.at)}`
        : point.source === "finished-trade"
          ? etTimeLabel(point.at)
          : historyDateLabel(point.at),
  }));
  const fact = (day: PerformanceAggregation["bestDay"]) =>
    day
      ? {
          dateLabel: historyDateLabel(day.date),
          dollarChange: day.accountChange.signedDollars ?? Number.NaN,
          percentChange: day.accountChange.percent ?? Number.NaN,
        }
      : null;

  const navigate = (destination: DashboardDestination) => {
    if (destination === "history") {
      setPage("history");
      window.scrollTo({ top: 0, behavior: "smooth" });
      return;
    }
    setPage("today");
    if (destination === "plans" || destination === "controls") {
      setMode("intraday");
    }
    requestAnimationFrame(() => {
      if (destination === "today") {
        window.scrollTo({ top: 0, behavior: "smooth" });
        return;
      }
      const target = document.getElementById(destination);
      target?.scrollIntoView({ behavior: "smooth", block: "start" });
      target?.focus({ preventScroll: true });
    });
  };

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
  // (an empty feed list counts as down once data has arrived: no feed is connected)
  const feedDown = isFeedDown(state.ingestion);
  const savingProblem = state.persistence.status !== "durable" && state.persistence.status !== "disabled";

  // Everything on the page that needs the operator. The status strip's pill and its list read this one list.
  const attention = collectAttention({
    connectionState,
    feedDown,
    brokerMismatch: !!state.broker?.mismatch,
    savingProblem,
    unsold,
    breakerHit: !!state.account.is_circuit_broken,
    strategies: state.strategies,
    overnight: {
      initError: ovn?.state?.init_error ?? null,
      needsLook: Array.from(new Set([
        ...(ovn?.holds ?? []).filter((h) => (h.needs_look ?? []).length > 0).map((h) => h.symbol),
        ...(ovn?.rows ?? []).filter((r) => (r.needs_look ?? []).length > 0).map((r) => r.symbol),
      ])),
    },
    ledgerError: !!todayLedger.error,
    resultsError: !!allLedger.error,
    positions: intradayPositions,
  });

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
        <DashboardNavigation page={page} onNavigate={navigate} />

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

        {/* Rendered once, above both views (as on main), so a tap-to-confirm in flight survives Quick/Slow switches. */}
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

        <PerformancePanel
          period={performancePeriod}
          onPeriodChange={setPerformancePeriod}
          headline={performanceHeadline}
          equity={selectedPerformance.currentEquity}
          dollarChange={selectedPerformance.accountChange.signedDollars}
          percentChange={selectedPerformance.accountChange.percent}
          comparisonBasis={selectedPerformance.comparisonLabel}
          finishedTradeResult={selectedPerformance.finishedTradeResult.signedDollars}
          openHoldingsResult={selectedPerformance.openHoldingResult.signedDollars}
          tradeCount={selectedPerformance.tradeCount}
          chartLabel={
            selectedPerformance.chart.kind === "cumulative-finished-trade-result"
              ? "Cumulative finished trade result"
              : "Closing account equity"
          }
          chartPoints={chartPoints}
          bestDay={fact(selectedPerformance.bestDay)}
          worstDay={fact(selectedPerformance.worstDay)}
          status={performanceStatus}
          historyState={partialHistory}
        />

        {page === "history" ? (
          <HistoryExplorer
            data={historyData}
            period={historyPeriod}
            onPeriodChange={(period) =>
              setPerformancePeriod(
                period === "Day"
                  ? "day"
                  : period === "Week"
                    ? "week"
                    : period === "Month"
                      ? "month"
                      : "all",
              )
            }
            title="Detailed account history"
          />
        ) : (
          <>
            <RightNowCard
              sentence={heroSentence}
              tradesToday={tradesToday}
              wins={wins}
              losses={losses}
              countdownLabel={countdownLabel}
              countdownValue={countdownValue}
              attention={attention}
            />

            {mode === "intraday" ? (
              <div className="fadein grid grid-cols-1 items-start gap-3 lg:grid-cols-[minmax(0,1fr)_420px]">
                <div className="flex min-w-0 flex-col gap-3">
                  <div id="holdings" tabIndex={-1} className="scroll-mt-4 outline-none">
                    <HoldingNow
                      positions={intradayPositions}
                      marketContext={state.market_context}
                      onFlattenPosition={flattenPosition}
                      onTightenStop={tightenStop}
                    />
                    {intradayPositions.length === 0 && (
                      <section className="rounded-xl border border-line bg-white px-4 py-5">
                        <h2 className="text-lg font-semibold text-ink">Holdings</h2>
                        <p className="mt-1 text-sm text-muted">No quick trade holdings are open.</p>
                      </section>
                    )}
                  </div>

                  <MarketMoodCard context={state.market_context} tradingDay={firstTradingDayFlag} />

                  <div id="plans" tabIndex={-1} className="scroll-mt-4 outline-none">
                    <StrategyTable
                      strategies={state.strategies}
                      ledgerByStrategy={ledgerByStrategy}
                      showPro={showPro}
                      overnight={tableOvernight}
                    />
                  </div>
                </div>

                <div className="flex min-w-0 flex-col gap-3">
                  <RecentHistory
                    days={performanceByPeriod.all.days}
                    onOpenHistory={() => navigate("history")}
                  />
                  <div id="controls" tabIndex={-1} className="scroll-mt-4 outline-none">
                    <SafetyCard
                      drawdownDollars={state.account.risk_drawdown ?? state.account.daily_drawdown}
                      maxDailyLossDollars={healthLimits.maxDailyLossDollars}
                      baseTradeRiskPct={healthLimits.baseTradeRiskPct}
                      intradayPositionsCount={intradayPositions.length}
                      onFlattenAll={flattenAll}
                      overnight={
                        overnightOn
                          ? {
                              buysOn:
                                ovnBuysOn &&
                                (ovn?.settings?.enabled?.length ?? 0) > 0,
                              symbols: ovn?.settings?.enabled ?? [],
                              pct: ovn?.settings?.pct ?? null,
                              resultToday:
                                state.account.overnight_realized_today ?? null,
                              holdsCount: holdViews.length,
                            }
                          : null
                      }
                    />
                  </div>
                </div>
              </div>
            ) : (
              <div className="fadein flex flex-col gap-4">
                <SwingTelemetryBar swingState={state.swing} showPro={showPro} />
                <div id="holdings" tabIndex={-1} className="scroll-mt-4 outline-none">
                  <ActiveSwingPositionsTable
                    positions={state.swing?.positions ?? []}
                    onExitNextOpen={swingExitNextOpen}
                    onExitImmediate={swingExitImmediate}
                    onTightenStop={swingTightenStop}
                  />
                </div>
                <SwingCandidateWatchlist candidates={state.swing?.candidates ?? []} />
              </div>
            )}
          </>
        )}

        {showPro && <ExecutionLog records={state.recent_activity} maxItems={20} />}
      </div>
    </main>
  );
}
