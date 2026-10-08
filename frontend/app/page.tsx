"use client";

import { useEffect, useMemo, useState } from "react";
import { useTradingStream } from "@/hooks/useTradingStream";
import { useLedger, useTodayLedger } from "@/hooks/useTodayLedger";
import { useHealthLimits } from "@/hooks/useHealthLimits";
import type { StrategyTableOvernight } from "@/components/StrategyTable";
import type { HoldView } from "@/components/OvernightHolds";
import HistoryExplorer, { HistoryPeriod } from "@/components/HistoryExplorer";
import { aggregatePerformance, type PerformancePeriod, type PerformanceAggregation } from "@/lib/performance";
import { buildHistoryView } from "@/lib/historyView";
import { collectAttention, companyName, etDateKey, etDateOfIso, etMinutesOfDay, etTimeLabel, groupLedgerByStrategy, historyDateLabel, isFeedDown, isOvernightPosition, joinNames, noBuyDisabledReason, overnightHoldingSentence, rightNowSentence, tonightStatusLine, unsoldBannerText, strategyTheme } from "@/lib/plain";
import { clockLine, firstAlarmAction, isMarketOpen, playbooksInitiallyOpen, lockCountdown, resultsDays, tradingDayOf, weekResultByStrategy } from "@/lib/gut";
import TopBar from "@/components/gut/TopBar";
import StatusCard from "@/components/gut/StatusCard";
import MoneyTiles from "@/components/gut/MoneyTiles";
import Holdings from "@/components/gut/Holdings";
import ResultsBars from "@/components/gut/ResultsBars";
import PlaybookPanel from "@/components/gut/PlaybookPanel";
import ControlsCard from "@/components/gut/ControlsCard";
import ProDetails from "@/components/gut/ProDetails";
import Intro from "@/components/gut/Intro";

export default function Home() {
  const { state, isConnected, hasReceivedData, connectionState, flattenPosition, flattenAll, tightenStop,
    swingExitNextOpen, swingExitImmediate, swingTightenStop, setNoBuyTonight } = useTradingStream();
  const [playbookOpen, setPlaybookOpen] = useState<boolean | null>(null);
  const [page, setPage] = useState<"today" | "history">("today");
  const [performancePeriod, setPerformancePeriod] = useState<PerformancePeriod>("day");
  const todayLedger = useTodayLedger(state.ledger_revision, isConnected);
  // every day and every trade since the start: the Results panel and the Balance card's "Since start" line
  const allLedger = useLedger("all", state.ledger_revision, isConnected);
  const weekLedger = useLedger("7d", state.ledger_revision, isConnected);
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


  // Overnight holds (PLAN_2026_09_30_overnight_holds.md section 5). Everything below reads the
  // websocket "overnight" payload and the positions. With neither (older backend) nothing changes.
  const [now, setNow] = useState(() => new Date());
  useEffect(() => { const id = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(id); }, []);
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
  const tradingDayToday = tradingDayOf(state.strategies, now);
  const marketOpen = isMarketOpen(state.market_context.market_status, tradingDayToday);
  useEffect(() => {
    if (hasReceivedData) setPlaybookOpen(previous => previous ?? playbooksInitiallyOpen(state.strategies, intradayPositions.length));
  }, [hasReceivedData, state.strategies, intradayPositions.length]);
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
      ? `Tonight it buys ${joinNames(plannedTonight.map((r) => r.symbol))} near the 4:00 PM close and sells at the next 9:30 AM open.`
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
  const heroSentence = rightNowSentence({
    isCircuitBroken: state.account.is_circuit_broken,
    marketStatus: tradingDayToday ? state.market_context.market_status : "CLOSED",
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
    resultsError: !!allLedger.error || !!weekLedger.error,
    riskDrawdown: state.account.risk_drawdown ?? state.account.daily_drawdown,
    maxDailyLossDollars: healthLimits.maxDailyLossDollars,
    swingDataWithheld: state.swing?.last_close_entries_withheld,
    positions: intradayPositions,
  });

  const weekResults = weekResultByStrategy(weekLedger.items, weekLedger.recoveredSessions);
  const days = resultsDays(weekLedger.items, weekLedger.sessions, todayEt);
  const action = firstAlarmAction(attention, intradayPositions);
  const alarmTexts: Record<string, string> = {
    unsold: unsoldBannerText(unsold),
    mismatch: "The robot's positions don't match the Alpaca account. New trades are paused until they match.",
    feed: "Price feed is down, it can't trade right now.",
    saving: "Saving problems: new trades are paused until this is fixed.",
    breaker: "Day trading stopped for today. It hit the daily loss limit.",
  };
  const firstAlarm = attention[0] ? { text: alarmTexts[attention[0].key] ?? attention[0].label,
    action: action ? { label: action.label, onConfirm: () => flattenPosition(action.symbol) } : undefined } : null;
  const historyPeriod = ({ day: "Day", week: "Week", month: "Month", all: "All" } as const)[performancePeriod];
  const changeHistoryPeriod = (period: HistoryPeriod) => setPerformancePeriod(({ Day: "day", Week: "week", Month: "month", All: "all" } as const)[period]);
  const openHistory = () => { setPerformancePeriod("week"); setPage("history"); window.scrollTo({ top: 0 }); };

  return <main className="gut-main">
    <Intro hasReceivedData={hasReceivedData} attentionCount={attention.length} playbookCount={state.strategies.length + (ovn ? 1 : 0)} />
    <div className="gut-shell">
      <TopBar lastUpdated={hasReceivedData ? state.lastUpdated : null} connectionState={connectionState} marketOpen={hasReceivedData && marketOpen} />
      {!hasReceivedData ? <section data-testid="first-frame-skeleton" aria-busy="true" className="gut-skeleton">
        <p role="status" className="text-sm text-muted">Connecting to the robot…</p>
        <div className="gut-card h-32" /><div className="grid grid-cols-3 gap-2"><div className="gut-card h-28" /><div className="gut-card h-28" /><div className="gut-card h-28" /></div><div className="gut-card h-48" /><div className="gut-card h-40" />
      </section> : page === "history" ? <div>
        <button type="button" onClick={() => setPage("today")} className="mb-3 min-h-[44px] text-sm font-semibold text-darkcard" data-testid="back-to-today">← Back to today</button>
        {attention.length > 0 && <div className="gut-alarm mb-3" data-testid="history-alarms"><strong>{attention.length === 1 ? "1 thing needs a look" : `${attention.length} things need a look`}:</strong> {firstAlarm?.text ?? attention[0].label}{attention.length > 1 && ` · ${attention.slice(1).map(a => a.label).join(" · ")}`} <button type="button" onClick={() => setPage("today")} className="font-semibold underline">Back to today</button></div>}
        {allLedger.error && <p className="gut-alarm">History did not refresh. Showing the last recorded results.</p>}
        <HistoryExplorer data={historyData} period={historyPeriod} onPeriodChange={changeHistoryPeriod} title="Detailed account history" />
      </div> : <div className="gut-grid">
        <StatusCard sentence={heroSentence} attention={attention} firstAlarm={firstAlarm}
          clock={clockLine(now, marketOpen, tradingDayToday, state.strategies.find(s => s.window?.next_change_at)?.window?.next_change_at)}
          lockCountdown={lockCountdown(ovn?.no_buy_until, now, tradingDayToday && ovnBuysOn && !noBuyActive && plannedTonight.length > 0)}
          stateIcon={!marketOpen ? "closed" : state.account.is_circuit_broken ? "paused" : intradayPositions.length ? "running" : "flat"}
          unsoldText={unsold.length ? unsoldBannerText(unsold) : null}
          reconnecting={connectionState === "live" ? null : connectionState === "reconnecting" ? "Lost connection to the robot. Showing the last numbers it sent. Reconnecting..." : "Numbers may be old. Still trying to reach the robot."} />
        <MoneyTiles account={state.account} today={todayLedger.summary} week={weekLedger.summary} todayError={!!todayLedger.error} weekError={!!weekLedger.error} />
        <Holdings day={{ positions: intradayPositions, marketContext: state.market_context, onFlattenPosition: flattenPosition, onTightenStop: tightenStop }}
          overnightPositions={state.all_positions.filter(isOvernightPosition)} holds={holdViews}
          swing={{ positions: state.swing?.positions ?? [], onExitNextOpen: swingExitNextOpen, onExitImmediate: swingExitImmediate, onTightenStop: swingTightenStop }}
          marketOpen={marketOpen} etMin={etMin} tradingDay={tradingDayToday} mismatch={!!state.broker?.mismatch} />
        <ResultsBars days={days} total={weekLedger.summary?.realized_pnl ?? null} loading={weekLedger.loading} error={weekLedger.error} onOpenHistory={openHistory} />
        <PlaybookPanel strategies={state.strategies} ledgerByStrategy={ledgerByStrategy} showPro={false} overnight={tableOvernight}
          dayCount={intradayPositions.length} overnightCount={holdViews.length} weekResults={weekResults}
          savedOpen={playbookOpen} onOpenChange={setPlaybookOpen} />
        <ControlsCard marketOpen={marketOpen} dayCount={intradayPositions.length} workingOrders={state.working_orders_count}
          stopped={state.account.is_circuit_broken || state.strategies.every(s => ["PAUSED", "DONE_FOR_DAY"].includes(s.window?.state ?? ""))}
          onFlattenAll={flattenAll} noBuy={{ noBuyActive, disabledReason, summary: overnightSummary, onSetNoBuy: setNoBuyTonight }} tonight={tonightLines} />
        <ProDetails state={state} limits={healthLimits} tradingDay={tradingDayToday} />
      </div>}
    </div>
  </main>;
}
