import assert from "node:assert/strict";
import { aggregatePerformance } from "../lib/performance.ts";
import { buildHistoryView } from "../lib/historyView.ts";

let checks = 0;
function equal(actual, expected, message) {
  assert.deepStrictEqual(actual, expected, `${message}, got ${JSON.stringify(actual)}`);
  checks += 1;
  console.log(`  ok   ${message}`);
}

const trades = [
  {
    trade_id: "orb-one",
    session_date: "2026-10-05",
    symbol: "AAPL",
    side: "LONG",
    status: "CLOSED",
    strategy_id: "orb",
    opened_at: "2026-10-05T14:00:00.000Z",
    closed_at: "2026-10-05T15:00:00.000Z",
    quantity: 2,
    avg_entry_price: 100,
    avg_exit_price: 112.5,
    realized_pnl: 25,
    fees: 0,
    exit_reason: "TARGET",
  },
];

const sessions = [
  {
    session_date: "2026-10-04",
    opening_equity: 50_000,
    closing_equity: 49_990,
    account_change: -10,
    finished_trade_result: -10,
    realized_pnl: -10,
    trades_count: 1,
    wins: 0,
    losses: 1,
    fees: 0,
    fees_known: false,
    source: "LEGACY",
    aggregate_only: true,
    trade_detail_complete: false,
    strategies: {
      overnight_nvda: { trades_count: 1, realized_pnl: -10, wins: 0, losses: 1 },
    },
  },
  {
    session_date: "2026-10-05",
    opening_equity: 49_990,
    closing_equity: 50_015,
    account_change: 25,
    finished_trade_result: 25,
    realized_pnl: 25,
    trades_count: 1,
    wins: 1,
    losses: 0,
    fees: 0,
    fees_known: true,
    source: "LEDGER",
    aggregate_only: false,
    trade_detail_complete: true,
    strategies: {
      orb: { trades_count: 1, realized_pnl: 25, wins: 1, losses: 0 },
    },
  },
];

const performance = Object.fromEntries(
  ["day", "week", "month", "all"].map((period) => [
    period,
    aggregatePerformance({
      period,
      today: "2026-10-06",
      currentEquity: 50_020,
      dailyOpeningEquity: 50_015,
      openHoldingResult: { dollars: 5 },
      sessions,
      trades,
      historyComplete: true,
      asOf: "2026-10-06T15:30:00-04:00",
    }),
  ]),
);

const view = buildHistoryView({
  performance,
  sessions,
  trades,
  planName: (id) => ({ orb: "Opening Range", overnight_nvda: "NVDA overnight" })[id] ?? id,
  companyName: () => "Apple",
  dateLabel: (date) => date,
  timeLabel: (time) => time.slice(11, 16),
});

console.log("Verifying Cobalt Ledger history view");
equal(view.Day?.summary.changeDollars, 5, "Day summary uses current account change");
equal(view.Week?.rows.Days.length, 3, "Week lists each recorded account day");
equal(
  view.Week?.rows.Days.find((row) => row.id === "2026-10-04")?.dailyTotalOnly,
  true,
  "Recovered aggregate day is labeled daily total only",
);
equal(
  view.Week?.rows.Plans.find((row) => row.id === "orb")?.resultDollars,
  25,
  "Plan result uses the authoritative session strategy total once",
);
equal(
  view.Week?.rows.Plans.find((row) => row.id === "overnight_nvda")?.totalTradeCount,
  1,
  "Aggregate only plan keeps its recorded trade count",
);
equal(
  view.Week?.rows.Plans.find((row) => row.id === "overnight_nvda")?.loadedTradeCount,
  0,
  "Aggregate only plan does not invent detailed trades",
);
equal(view.Week?.rows.Trades.length, 1, "Trade list contains only available detailed rows");
equal(view.Week?.rows.Trades[0].resultPercent, 12.5, "Trade percent uses entry value as its named baseline");

console.log(`\n${checks} history view checks passed`);
