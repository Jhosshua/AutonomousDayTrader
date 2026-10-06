import assert from "node:assert/strict";
import { aggregatePerformance } from "../lib/performance.ts";

let checks = 0;
const check = (condition, message) => {
  assert(condition, message);
  checks += 1;
  console.log(`  ok   ${message}`);
};
const equal = (actual, expected, message) => {
  assert.deepStrictEqual(actual, expected, `${message}, got ${JSON.stringify(actual)}`);
  checks += 1;
  console.log(`  ok   ${message}`);
};

const trade = (id, date, pnl, closedAt, overrides = {}) => ({
  trade_id: id,
  session_date: date,
  symbol: "AAPL",
  side: "LONG",
  status: "CLOSED",
  strategy_id: "orb",
  opened_at: `${date}T14:00:00.000Z`,
  closed_at: closedAt,
  quantity: 1,
  avg_entry_price: 100,
  avg_exit_price: 101,
  realized_pnl: pnl,
  fees: 0,
  exit_reason: "TARGET",
  ...overrides,
});

const session = (date, opening, closing, pnl, count, overrides = {}) => ({
  session_date: date,
  opening_equity: opening,
  closing_equity: closing,
  realized_pnl: pnl,
  trades_count: count,
  fees: 0,
  source: "CHECKPOINT",
  aggregate_only: false,
  ...overrides,
});

const base = (overrides = {}) => ({
  period: "day",
  today: "2026-10-06",
  currentEquity: 50_100,
  dailyOpeningEquity: 50_000,
  openHoldingResult: { dollars: 25, percent: 0.5 },
  sessions: [],
  trades: [],
  tradesTruncated: false,
  oldestLoadedDate: null,
  historyComplete: true,
  asOf: "2026-10-06T15:30:00-04:00",
  ...overrides,
});

console.log("Verifying Cobalt Ledger performance aggregation");

{
  const result = aggregatePerformance(base({
    trades: [
      trade("late", "2026-10-06", -30, "2026-10-06T15:30:00.000Z"),
      trade("early", "2026-10-06", 50, "2026-10-06T14:15:00.000Z"),
      trade("other-day", "2026-10-05", 999, "2026-10-05T15:00:00.000Z"),
    ],
  }));
  equal(result.chart.kind, "cumulative-finished-trade-result", "Day chart is finished trade result, never equity");
  equal(result.chart.points.map((point) => point.value), [50, 20], "Day chart accumulates results in close time order");
  equal(result.chart.points.map((point) => point.tradeId), ["early", "late"], "Day chart identifies the ordered trades");
  equal(result.finishedTradeResult.signedDollars, 20, "Day finished result excludes other dates");
  equal(result.accountChange, { signedDollars: 100, percent: 0.2, direction: "up" }, "Account change is separate dollars and percent");
  equal(result.openHoldingResult, { signedDollars: 25, percent: 0.5, direction: "up" }, "Open holding result stays separate");
}

{
  const sessions = [
    session("2026-09-29", 49_700, 49_750, 50, 1),
    session("2026-09-30", 49_750, 49_800, 50, 1),
    session("2026-10-02", 49_800, 49_900, 100, 2),
    session("2026-10-05", 49_900, 50_000, 100, 2),
  ];
  const week = aggregatePerformance(base({ period: "week", sessions, currentEquity: 50_100 }));
  equal(week.rangeStart, "2026-09-30", "Week is today plus the previous six calendar dates");
  equal(week.days.map((day) => day.date), ["2026-09-30", "2026-10-02", "2026-10-05", "2026-10-06"], "Week excludes dates before its calendar window");
  equal(week.openingEquity, 49_750, "Week opens at the earliest complete session in range");
  equal(week.accountChange, { signedDollars: 350, percent: 0.7, direction: "up" }, "Week account return uses opening equity and live equity");
  equal(week.chart.kind, "session-closing-equity", "Week chart uses closing equity");
  equal(week.chart.points.map((point) => point.source), ["session-close", "session-close", "session-close", "live"], "Longer chart appends one current live point");

  const month = aggregatePerformance(base({ period: "month", sessions }));
  equal(month.rangeStart, "2026-09-07", "Month is today plus the previous twenty nine calendar dates");

  const all = aggregatePerformance(base({ period: "all", sessions }));
  equal(all.periodLabel, "Since recorded history", "All is labeled Since recorded history");
  equal(all.comparisonLabel, "the first recorded opening equity", "All comparison label is unambiguous");
}

{
  const result = aggregatePerformance(base({
    period: "week",
    currentEquity: 9_990,
    sessions: [
      session("2026-10-01", 10_000, 9_900, -100, 1),
      session("2026-10-02", 50_000, 49_800, -200, 1),
      session("2026-10-05", 10_000, 10_000, 0, 0),
      session("2026-10-06", 10_000, 10_100, 100, 1),
    ],
  }));
  equal(result.bestDay?.date, "2026-10-06", "Best day ranks by account percent");
  equal(result.worstDay?.date, "2026-10-01", "Worst day ranks by percent, not larger dollar loss");
  equal(result.days.find((day) => day.date === "2026-10-05")?.accountChange, { signedDollars: 0, percent: 0, direction: "flat" }, "Flat day remains explicitly flat");
  equal(result.days.find((day) => day.date === "2026-10-01")?.accountChange.direction, "down", "Negative day remains explicitly negative");
}

{
  const result = aggregatePerformance(base({
    period: "week",
    currentEquity: 50_090,
    sessions: [session("2026-10-05", 50_000, 50_090, 90, 2)],
    trades: [
      trade("account-versus-trades-one", "2026-10-05", 30, "2026-10-05T14:00:00.000Z"),
      trade("account-versus-trades-two", "2026-10-05", 20, "2026-10-05T15:00:00.000Z"),
    ],
  }));
  const day = result.days.find((row) => row.date === "2026-10-05");
  equal(day?.accountChange.signedDollars, 90, "Nonaggregate account change comes from closing minus opening equity");
  equal(day?.finishedTradeResult.signedDollars, 50, "Nonaggregate finished result comes only from detailed trades");
  equal(result.finishedTradeResult.signedDollars, 50, "Period finished result never uses nonaggregate session realized result");
}

{
  const result = aggregatePerformance(base({
    period: "week",
    sessions: [session("2026-10-05", 50_000, 50_020, 20, 1)],
    trades: [
      trade("covered-session", "2026-10-05", 20, "2026-10-05T15:00:00.000Z"),
      trade("missing-session", "2026-10-04", -10, "2026-10-04T15:00:00.000Z"),
    ],
  }));
  const missing = result.days.find((day) => day.date === "2026-10-04");
  equal(missing?.source, "trades", "A date without a session uses its detailed trades");
  equal(missing?.finishedTradeResult.signedDollars, -10, "Missing session preserves known finished result");
  equal(result.finishedTradeResult.signedDollars, 10, "Missing session result is included once");
  equal(result.completeness.sessionCoverage, "partial", "Missing session marks session coverage partial");
  check(result.completeness.partialLabels.includes("Some days lack session equity summaries."), "Missing session has a plain partial label");
}

{
  const aggregate = session("2026-10-05", 50_000, 49_950, -50, 4, { aggregate_only: true });
  const result = aggregatePerformance(base({
    period: "week",
    sessions: [aggregate],
    trades: [
      trade("overlap-one", "2026-10-05", -20, "2026-10-05T14:00:00.000Z"),
      trade("overlap-two", "2026-10-05", -30, "2026-10-05T15:00:00.000Z"),
    ],
  }));
  const day = result.days.find((row) => row.date === "2026-10-05");
  equal(day?.finishedTradeResult.signedDollars, -50, "Session total owns an overlapping date");
  equal(day?.tradeCount, 4, "Session trade count owns an overlapping date");
  equal(result.finishedTradeResult.signedDollars, -50, "Aggregate only total never stacks on detailed rows");
  equal(result.tradeCount, 4, "Aggregate only count never stacks on detailed rows");
  equal(day?.aggregateOnly, true, "Aggregate only coverage remains explicit");
  equal(day?.detailsComplete, false, "Aggregate only coverage never claims detailed completeness");
  equal(result.completeness.tradeDetails, "partial", "Aggregate only coverage marks trade details partial");
  check(result.completeness.partialLabels.includes("Daily total only. Individual trade details are unavailable."), "Aggregate only coverage has a plain partial label");
}

{
  const result = aggregatePerformance(base({
    period: "week",
    sessions: [
      session("2026-10-05", 50_000, 50_040, 40, 4),
      session("2026-10-06", 50_040, 50_100, 60, 3),
    ],
    trades: [trade("only-loaded-row", "2026-10-06", 60, "2026-10-06T15:00:00.000Z")],
    tradesTruncated: true,
  }));
  equal(result.finishedTradeResult.signedDollars, null, "Truncation never substitutes nonaggregate account change for finished trade result");
  equal(result.tradeCount, 7, "Truncation does not weaken complete session counts");
  equal(result.completeness.account, "complete", "Truncation does not make account performance partial");
  equal(result.completeness.tradeDetails, "partial", "Truncation marks only trade detail completeness partial");
  check(result.completeness.partialLabels.includes("Only the most recent trades are loaded for this period."), "Truncation has a plain partial label");
  check(
    result.completeness.partialLabels.includes("Finished trade totals are unavailable where detailed trade rows are incomplete."),
    "Truncation explains why finished trade totals are unavailable",
  );
}

{
  const result = aggregatePerformance(base({
    period: "week",
    sessions: [
      session("2026-10-05", 50_000, 50_040, 999, 4, {
        finished_trade_result: 40,
        trade_detail_complete: true,
      }),
      session("2026-10-06", 50_040, 50_100, 999, 3, {
        finished_trade_result: 60,
        trade_detail_complete: true,
      }),
    ],
    trades: [
      trade("week-one", "2026-10-05", 40, "2026-10-05T15:00:00.000Z"),
      trade("week-two", "2026-10-06", 60, "2026-10-06T15:00:00.000Z"),
    ],
    tradesTruncated: true,
    oldestLoadedDate: "2026-09-01",
  }));
  equal(result.finishedTradeResult.signedDollars, 100, "Older global truncation does not weaken a fully covered week");
  equal(result.completeness.tradeDetails, "partial", "Missing detailed rows still keep drilldown completeness honest");
  check(
    !result.completeness.partialLabels.includes("Only the most recent trades are loaded for this period."),
    "Covered period does not show a global truncation warning",
  );
}

{
  const result = aggregatePerformance(base({
    period: "week",
    sessions: [session("2026-10-05", 50_000, 50_100, 100, 2)],
    trades: [trade("one-of-two", "2026-10-05", 25, "2026-10-05T15:00:00.000Z")],
  }));
  equal(result.days.find((day) => day.date === "2026-10-05")?.finishedTradeResult.signedDollars, null, "Missing detailed rows make a nonaggregate day finished result unavailable");
  equal(result.finishedTradeResult.signedDollars, null, "One incomplete nonaggregate day makes the period finished result unavailable");
  equal(result.tradeCount, 2, "Incomplete details preserve the authoritative session trade count");
}

{
  const duplicate = trade("same-id", "2026-10-06", 15, "2026-10-06T14:00:00.000Z");
  const result = aggregatePerformance(base({ trades: [duplicate, { ...duplicate }] }));
  equal(result.tradeCount, 1, "Duplicate trade identities count once");
  equal(result.finishedTradeResult.signedDollars, 15, "Duplicate trade identities add result once");
  check(result.completeness.partialLabels.includes("Duplicate trade rows were ignored."), "Duplicate trades are reported");
}

{
  const result = aggregatePerformance(base({
    period: "week",
    currentEquity: Number.POSITIVE_INFINITY,
    sessions: [
      session("not-a-date", 50_000, 50_010, 10, 1),
      session("2026-10-05", Number.NaN, Number.POSITIVE_INFINITY, Number.NaN, Number.NaN),
    ],
    trades: [
      trade("bad-date", "2026-02-31", 10, "2026-02-31T15:00:00.000Z"),
      trade("bad-pnl", "2026-10-06", Number.NaN, "2026-10-06T15:00:00.000Z"),
      trade("bad-close", "2026-10-06", 5, "invalid"),
    ],
  }));
  equal(result.currentEquity, null, "Nonfinite live equity becomes unknown");
  equal(result.accountChange.direction, "unknown", "Unknown account values never become flat");
  equal(result.finishedTradeResult.signedDollars, null, "Nonfinite finished results never become zero");
  check(result.chart.points.length === 0, "Invalid Day inputs do not leak into a longer equity chart");
  check(result.completeness.partialLabels.includes("Trades with invalid session dates were ignored."), "Invalid trade dates are reported");
  check(result.completeness.partialLabels.includes("Sessions with invalid dates were ignored."), "Invalid session dates are reported");
}

{
  assert.throws(
    () => aggregatePerformance(base({ today: "2026-02-31" })),
    /today must be a valid/,
    "Invalid caller date fails loudly",
  );
  checks += 1;
  console.log("  ok   Invalid caller date fails loudly");
}

console.log(`\n${checks} performance aggregation checks passed`);
