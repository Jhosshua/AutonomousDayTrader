// @steered SNARE-2 2026-09-30
/**
 * Plain-language helpers for the redesigned dashboard.
 *
 * Pure functions only (no React, no fetch). Every number that reaches the screen still comes
 * from the live API payloads; this file only turns codes/ids/ISO timestamps into the plain
 * words, colors, and layout math the mockups (docs/ui_redesign_2026_09_24/*.dc.html) specify.
 */

import type { EntryContext } from "@/types/trading";

// ---------------------------------------------------------------------------
// Strategy identity: friendly names, colors ("muted palette"), and copy
// ---------------------------------------------------------------------------

export function planStatusText(reason: string): string {
  const messages: Record<string, string> = {
    CALENDAR_UNAVAILABLE_OR_CLOSED: "Market closed today.",
    OPERATOR_PAUSED: "Paused by you.",
    MISSING_OPENING_RANGE_BAR: "Skipped today because the opening price data was incomplete.",
    MISSING_REQUIRED_BAR: "Skipped today because required price data was missing.",
    MISSED_ENTRY_WINDOW: "The entry window passed before an order could be sent.",
    SYMBOL_ALREADY_COMMITTED: "Another strategy is already trading this stock.",
    PAPER_BROKER_OR_SIP_UNAVAILABLE: "Waiting for the paper broker and live price feed.",
    ENTRY_NOT_FILLED: "The entry order did not fill.",
    INSUFFICIENT_RISK_OR_BUYING_POWER: "There was not enough buying power or risk budget for this trade.",
    NON_POSITIVE_RISK: "The price was already beyond the planned safety exit.",
    PROTECTION_REJECTED: "The broker could not accept the safety exits. Closing the trade.",
  };
  return messages[reason] || "A required trading check did not pass.";
}

export function historyDateLabel(day: string): string {
  return new Date(`${day}T12:00:00Z`).toLocaleDateString("en-US", {
    timeZone: "America/New_York", weekday: "short", month: "short", day: "numeric", year: "numeric",
  });
}

export function trendBlockText(event: string): string {
  const reasons: Record<string, string> = {
    DUP_BAR: "Repeated price bar ignored.", LATE_BAR: "Delayed price bar ignored.",
    BAD_BAR: "Price data failed validation.", FEED_GAP: "Price feed gap; waiting for fresh history.",
    ZERO_VOLUME_BAR: "No volume in this minute.", AMBIGUOUS_BAR: "Price broke both sides of the range.",
    TOUCH_TOO_EARLY: "Pullback arrived too soon.", NO_TOUCH: "Price did not return to the entry zone in time.",
    HIGH_VOLUME_PULLBACK: "Too much volume in the pullback.", PVR_NOT_THIN: "Pullback volume was not low enough.",
    TICK_UNAVAILABLE: "Trade data was incomplete.", AGGRESSIVE_PULLBACK: "Too much trading against the pullback.",
    PULLBACK_TIMEOUT: "Pullback lasted too long.", RESUMPTION_TOO_OLD: "The bounce took too long.",
    NO_UP_CLOSE: "Price did not resume in the trade's direction.", SLOPE_TOO_SLOW: "The bounce was too slow.",
    CHASED: "Price moved too far from the entry zone.", TICK_VELOCITY_LOW: "Recent trades did not show enough speed.",
    BOOK_UNAVAILABLE: "Bid and ask data was incomplete.", BOOK_AGAINST: "Bid and ask sizes opposed the trade.",
    WINDOW_CLOSED: "The morning entry window closed.", EMISSION_DISABLED: "Strategy paused, cooling down, or at its daily limit.",
    STOP_TOO_WIDE: "The required safety exit was too far away.",
    IMPULSE_NOT_AGGRESSIVE: "Trading flow did not support the initial move.",
    RESUMPTION_NOT_AGGRESSIVE: "Trading flow did not support the bounce.", CUM_DELTA_AGAINST: "Recent net trading opposed the move.",
    SESSION_DELTA_AGAINST: "Net buying and selling opposed this trade.", SPREAD_WIDE: "The bid–ask spread was too wide.",
    HVN_NO_SUPPORT: "The dip missed prior trading support.", HVN_OVERHEAD: "A prior trading level blocked the path to the target.",
    PROFILE_UNAVAILABLE: "Prior-session volume history was unavailable.",
  };
  return reasons[event] || "A required entry check did not pass.";
}

export interface StrategyTheme {
  name: string;
  band: string; // card header background
  ink: string; // header text color
  tint: string; // soft chip/note background
  bar: string; // hours-bar segment / icon tile color
  track: string; // hours-bar track background
  what: string; // one-sentence plain description
}

export const STRATEGY_THEMES: Record<string, StrategyTheme> = {
  tsla_asymmetric_dual: {
    name: "Tesla Morning Plan", band: "#DCE9EF", ink: "#2F5368", tint: "#EDF4F7",
    bar: "#7299AF", track: "#DFEAF0",
    what: "Trades a confirmed morning bounce or breakdown. Splits the position into two equal parts, each with a fixed target and closing time.",
  },
  cde_asymmetric_dual: {
    name: "Coeur Morning Plan", band: "#EAE3CF", ink: "#65542E", tint: "#F5F1E5",
    bar: "#AD985E", track: "#EBE4D2",
    what: "Trades Coeur Mining after a morning bounce or breakdown, with QQQ confirmation. Uses one fixed target and a three-hour limit.",
  },
  tsla_or15_retest: {
    name: "Tesla Morning Retest",
    band: "#DCE9EF",
    ink: "#2F5368",
    tint: "#EDF4F7",
    bar: "#7299AF",
    track: "#DFEAF0",
    what: "Buys one Tesla share after a morning breakout pulls back and holds. Checks QQQ for support, then uses fixed exits and a two-hour limit.",
  },
  orb: {
    name: "Opening Range Breakout (ORBStraddle rules)",
    band: "#F4E0CF",
    ink: "#7A3E1D",
    tint: "#FAF0E6",
    bar: "#D98B5F",
    track: "#F2E3D5",
    what: "Scans about 250 stocks for a clean break of their 9:30 to 9:35 range. Decides at 9:38 AM, may add a trade until 10:15 AM, and closes every trade by 11:00 AM. Each trade is a broker bracket: its stop and target wait at Alpaca.",
  },
  vwap_pullback: {
    name: "Ride the Trend",
    band: "#DCE8DE",
    ink: "#2F5A45",
    tint: "#EDF3EE",
    bar: "#6E9C82",
    track: "#DDE9E0",
    what: "Version 2. When a stock pushes to a new high (or low), it waits for a quiet, low-volume dip back toward the day's average price, then joins only if the move picks up speed again. Mornings only.",
  },
  news_momentum: {
    name: "Big News",
    band: "#F1DDDF",
    ink: "#7A3343",
    tint: "#F7EBED",
    bar: "#C47A88",
    track: "#EFDFE2",
    what: "Jumps in when a company gets very big news and lots of people rush to trade it.",
  },
  mean_reversion: {
    name: "Snap Back",
    band: "#E0E1F0",
    ink: "#3E4478",
    tint: "#EEEFF7",
    bar: "#8189C4",
    track: "#E2E3F1",
    what: "Bets that a stock that ran too far, too fast will bounce back a little.",
  },
  // Overnight holds: labels only, in the neutral colors (no new palette until the mockups are approved).
  overnight_nvda: {
    name: "NVDA overnight", band: "#ECE9DE", ink: "#5D5A73", tint: "#F3F1EA", bar: "#A7A2B8", track: "#E7E3D6",
    what: "Buys NVDA at the 4:00 PM close and sells it at the next 9:30 AM open. No stop.",
  },
  overnight_iren: {
    name: "IREN overnight", band: "#ECE9DE", ink: "#5D5A73", tint: "#F3F1EA", bar: "#A7A2B8", track: "#E7E3D6",
    what: "Buys IREN at the 4:00 PM close and sells it at the next 9:30 AM open. No stop.",
  },
  overnight_hut: {
    name: "HUT overnight", band: "#ECE9DE", ink: "#5D5A73", tint: "#F3F1EA", bar: "#A7A2B8", track: "#E7E3D6",
    what: "Buys HUT at the 4:00 PM close and sells it at the next 9:30 AM open. No stop.",
  },
};

export const NEUTRAL_THEME: Omit<StrategyTheme, "name" | "what"> = {
  band: "#ECE9DE",
  ink: "#5D5A73",
  tint: "#F3F1EA",
  bar: "#A7A2B8",
  track: "#E7E3D6",
};

/** Theme + copy for a strategy id. Unknown ids fall back to neutral grey with the raw name. */
export function strategyTheme(id: string, fallbackName?: string): StrategyTheme {
  const known = STRATEGY_THEMES[id];
  if (known) return known;
  return { ...NEUTRAL_THEME, name: fallbackName || id, what: "" };
}

const COMPANY_NAMES: Record<string, string> = {
  AAPL: "Apple",
  NVDA: "Nvidia",
  PLTR: "Palantir",
  TSLA: "Tesla",
  CDE: "Coeur Mining",
  META: "Meta",
  AMD: "AMD",
  MSFT: "Microsoft",
  AMZN: "Amazon",
  GOOGL: "Alphabet",
  MU: "Micron",
  LRCX: "Lam Research",
  KLAC: "KLA",
  GS: "Goldman Sachs",
};

/** Company display name for a ticker, falling back to the ticker itself. */
export function companyName(ticker: string | undefined | null): string {
  if (!ticker) return "";
  return COMPANY_NAMES[ticker.toUpperCase()] || ticker.toUpperCase();
}

// ---------------------------------------------------------------------------
// Trading-window state -> status chip (color family + label)
// ---------------------------------------------------------------------------

export type ChipTone = "sage" | "lavender" | "grey" | "terracotta" | "amber";

export interface StatusChip {
  label: string;
  tone: ChipTone;
  breathing: boolean; // animated "live" dot
}

/** Maps a StrategyWindow.state to the plain status chip. Idle is NEVER red/terracotta (rule 3). */
export function windowToChip(win: { state: string; headline: string; blockers?: string[] } | undefined): StatusChip {
  if (!win) return { label: "Unknown", tone: "grey", breathing: false };
  switch (win.state) {
    case "MANAGING":
      return { label: "Managing trade", tone: "sage", breathing: true };
    case "CAN_TRADE":
      return { label: "Watching now", tone: "sage", breathing: true };
    case "LIMITED":
      return { label: "Watching, extra careful", tone: "sage", breathing: true };
    case "WAITING":
      return { label: win.headline || "Waiting", tone: "lavender", breathing: false };
    case "DONE_FOR_DAY":
      return { label: "Done for today", tone: "grey", breathing: false };
    case "NO_TRADE_TODAY":
      // cannot trade for the rest of the day (e.g. ORB's 9:38 scan failed): amber, never "watching"
      return { label: win.headline || "No trade today", tone: "amber", breathing: false };
    case "BLOCKED":
      return { label: win.blockers?.[0] ? `Paused: ${win.blockers[0]}` : "Paused right now", tone: "grey", breathing: false };
    case "PAUSED":
      return { label: "Paused", tone: "grey", breathing: false };
    case "MARKET_CLOSED":
      return { label: "Market closed", tone: "grey", breathing: false };
    default:
      return { label: win.headline || win.state, tone: "grey", breathing: false };
  }
}

// ---------------------------------------------------------------------------
// ET time helpers (no dependency on server clock; formats the browser's Date in America/New_York)
// ---------------------------------------------------------------------------

const ET_TZ = "America/New_York";

export function etParts(now: Date = new Date()): { hour: number; minute: number; weekday: number } {
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone: ET_TZ,
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    weekday: "short",
  });
  const parts = fmt.formatToParts(now);
  const hour = Number(parts.find((p) => p.type === "hour")?.value ?? "0");
  const minute = Number(parts.find((p) => p.type === "minute")?.value ?? "0");
  const weekdayStr = parts.find((p) => p.type === "weekday")?.value ?? "Sun";
  const map: Record<string, number> = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };
  return { hour: hour === 24 ? 0 : hour, minute, weekday: map[weekdayStr] ?? 0 };
}

/** YYYY-MM-DD for "now" in America/New_York, used to compare against a trade's session_date. */
export function etDateKey(now: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: ET_TZ, year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
}

/** "h:mm AM/PM" for an ISO timestamp, in America/New_York. */
export function etTimeLabel(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return new Intl.DateTimeFormat("en-US", { timeZone: ET_TZ, hour: "numeric", minute: "2-digit", hour12: true }).format(d);
}

const SESSION_START_MIN = 9 * 60 + 30;
const SESSION_END_MIN = 16 * 60;

/** Clamp a minutes-of-day value to 0-100% across the 9:30-16:00 ET session. */
export function sessionPct(minutesOfDay: number): number {
  const span = SESSION_END_MIN - SESSION_START_MIN;
  return Math.min(100, Math.max(0, ((minutesOfDay - SESSION_START_MIN) / span) * 100));
}

export function etMinutesOfDay(now: Date = new Date()): number {
  const { hour, minute } = etParts(now);
  return hour * 60 + minute;
}

/** Minutes-of-day (ET) for an ISO timestamp string, for placing a trade on the session axis. */
export function etMinutesOfDayFromIso(iso: string): number {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return SESSION_START_MIN;
  return etMinutesOfDay(d);
}

export function isWithinSession(minutesOfDay: number): boolean {
  return minutesOfDay >= SESSION_START_MIN && minutesOfDay <= SESSION_END_MIN;
}

// ---------------------------------------------------------------------------
// Hours bar: backend `window.ranges` ("HH:MM" ET pairs) -> track segments
// ---------------------------------------------------------------------------

export interface TrackSegment {
  left: number; // percent
  width: number; // percent
}

export function rangesToSegments(ranges: [string, string][] | undefined | null): TrackSegment[] {
  if (!ranges || ranges.length === 0) return [];
  return ranges
    .map(([startStr, endStr]) => {
      const [sh, sm] = startStr.split(":").map(Number);
      const [eh, em] = endStr.split(":").map(Number);
      const left = sessionPct(sh * 60 + sm);
      const right = sessionPct(eh * 60 + em);
      return { left, width: Math.max(0, right - left) };
    })
    .filter((s) => s.width > 0);
}

// ---------------------------------------------------------------------------
// "Quick trades close in" countdown to 15:55 ET. A quick trade in a stock the robot buys
// overnight tonight closes at 15:46 instead (plan X6), so the caller can pass that minute.
// ---------------------------------------------------------------------------

export function countdownToClose(now: Date = new Date(), isTradingDay?: boolean, closeMinute: number = 15 * 60 + 55): string {
  const { hour, minute, weekday } = etParts(now);
  const tradingDay = isTradingDay ?? (weekday !== 0 && weekday !== 6);
  if (!tradingDay) return "Market closed";
  const nowMin = hour * 60 + minute;
  const closeMin = closeMinute;
  if (nowMin < SESSION_START_MIN || nowMin >= closeMin) return "Market closed";
  const remaining = closeMin - nowMin;
  const h = Math.floor(remaining / 60);
  const m = remaining % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

// ---------------------------------------------------------------------------
// "Right now" headline sentence (dark hero card)
// ---------------------------------------------------------------------------

export interface RightNowStrategy {
  window?: { state: string; headline: string };
}

export interface RightNowInputs {
  isCircuitBroken: boolean;
  marketStatus: string | undefined;
  strategies: RightNowStrategy[];
  positionsCount: number;
  maxDailyLossDollars?: number | null;
  /** The overnight holds exist on this robot. A loss limit day still buys at the close (D5), so the
   * stop is worded as day trading only. Absent on an older backend: the old words stay. */
  overnightOn?: boolean;
  /** One sentence about the overnight holds held right now (built by the caller), or null. */
  overnightLine?: string | null;
}

export function rightNowSentence(inputs: RightNowInputs): string {
  const { isCircuitBroken, marketStatus, strategies, positionsCount, maxDailyLossDollars, overnightOn, overnightLine } = inputs;
  let base: string;
  if (isCircuitBroken) {
    const lead = overnightOn ? "Day trading stopped for today." : "Stopped for today.";
    base =
      maxDailyLossDollars != null
        ? `${lead} It hit the $${Math.round(maxDailyLossDollars).toLocaleString()} loss limit.`
        : `${lead} It hit the daily loss limit.`;
  } else if ((marketStatus || "").toUpperCase() !== "OPEN") {
    // with holds the overnight line already says when the next open is
    base = overnightLine ? "Market is closed." : "Market is closed. It starts again at 9:30 AM.";
  } else {
    const active = strategies.filter((s) => s.window?.state === "CAN_TRADE" || s.window?.state === "LIMITED");
    const waiting = strategies.filter((s) => s.window?.state === "WAITING");
    if (active.length > 0) {
      const plural = active.length === 1 ? "playbook is" : "playbooks are";
      base = `${active.length} ${plural} watching for a good chance.`;
      if (waiting.length > 0) {
        const when = (waiting[0].window?.headline || "").replace(/^Opens\s*/i, "");
        base += ` One is resting until ${when || "later"}.`;
      }
    } else if (waiting.length > 0) {
      const headline = waiting[0].window?.headline || "";
      base = /^Waiting\b/i.test(headline) ? headline : `Waiting. ${headline}`.trim();
    } else {
      base = "Nothing is watching right now.";
    }
  }
  if (overnightLine) base = `${overnightLine} ${base}`;
  if (positionsCount > 0) {
    const plural = positionsCount === 1 ? "trade" : "trades";
    base = `Holding ${positionsCount} ${plural} right now. ${base}`;
  }
  return base;
}

// ---------------------------------------------------------------------------
// Strategy card "today" note line
// ---------------------------------------------------------------------------

export interface StrategyDecisionsLike {
  signals_today: number;
  orders_today: number;
  top_block_text?: string | null;
}

export function strategyNoteLine(
  decisions: StrategyDecisionsLike | undefined,
  marketText: string | undefined,
  scheduleNote?: string
): string {
  const signals = decisions?.signals_today ?? 0;
  const orders = decisions?.orders_today ?? 0;
  let lead: string;
  if (orders > 0 && signals > orders) {
    lead = `${orders} trade${orders === 1 ? "" : "s"} today. Skipped ${signals - orders} other chance${signals - orders === 1 ? "" : "s"}.`;
  } else if (orders > 0) {
    lead = `${orders} trade${orders === 1 ? "" : "s"} today.`;
  } else if (signals > 0) {
    lead = `Saw ${signals} chance${signals === 1 ? "" : "s"}, skipped all.`;
  } else {
    lead = scheduleNote || "No chances yet today.";
  }
  const tail = marketText || "";
  return tail ? `${lead} ${tail}` : lead;
}

// ---------------------------------------------------------------------------
// Ledger grouping (F4/F5): today's trades grouped by strategy
// ---------------------------------------------------------------------------

export interface LedgerTradeLike {
  trade_id: string;
  strategy_id: string;
  realized_pnl: number;
  closed_at: string;
  session_date: string;
}

export interface StrategyLedgerAgg {
  realized_pnl: number;
  trades_count: number;
  wins: number;
  losses: number;
}

/** Group ledger rows by strategy_id, deduping by trade_id. Pure aggregation, no fetching. */
export function groupLedgerByStrategy(items: LedgerTradeLike[]): Record<string, StrategyLedgerAgg> {
  const seen = new Set<string>();
  const out: Record<string, StrategyLedgerAgg> = {};
  for (const item of items) {
    if (seen.has(item.trade_id)) continue;
    seen.add(item.trade_id);
    const agg = out[item.strategy_id] || { realized_pnl: 0, trades_count: 0, wins: 0, losses: 0 };
    agg.realized_pnl = Math.round((agg.realized_pnl + item.realized_pnl) * 100) / 100;
    agg.trades_count += 1;
    if (item.realized_pnl > 0) agg.wins += 1;
    else if (item.realized_pnl < 0) agg.losses += 1;
    out[item.strategy_id] = agg;
  }
  return out;
}

/** Dedupe trade rows by trade_id, keeping the first occurrence (used while draining pages). */
export function dedupeTrades<T extends { trade_id: string }>(items: T[]): T[] {
  const seen = new Set<string>();
  const out: T[] = [];
  for (const item of items) {
    if (seen.has(item.trade_id)) continue;
    seen.add(item.trade_id);
    out.push(item);
  }
  return out;
}

// ---------------------------------------------------------------------------
// Money / percent formatting
// ---------------------------------------------------------------------------

export function formatMoney(value: number): string {
  const abs = Math.abs(value);
  const formatted = abs.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${value < 0 ? "-" : ""}$${formatted}`;
}

export function formatSignedMoney(value: number): string {
  const abs = Math.abs(value);
  const formatted = abs.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${value < 0 ? "-" : "+"}$${formatted}`;
}

/** "2026-10-21" -> "Oct 21" (date-only strings, no timezone shift). Falls back to the input. */
export function shortDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return iso;
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const mi = Number(m[2]) - 1;
  return mi >= 0 && mi < 12 ? `${months[mi]} ${Number(m[3])}` : iso;
}

/** Plain name for one fixed part of a Tesla/Coeur plan trade. */
export function trancheName(index: number, count: number): string {
  if (count <= 1) return "Whole position";
  return index === 0 ? "First half" : "Second half";
}

// ---------------------------------------------------------------------------
// Market mood + "why this trade" (2026-09-29). Every sentence is built from values the backend sent
// (recorded at entry, or live config); nothing here invents a number.
// ---------------------------------------------------------------------------

/** Playbooks that size and place stops by the market mood. */
export const ADAPTIVE_IDS = ["vwap_pullback", "news_momentum", "mean_reversion"];
/** Playbooks with fixed plans that never react to the mood. */
export const FIXED_PLAN_IDS = ["tsla_asymmetric_dual", "cde_asymmetric_dual", "tsla_or15_retest"];

/** Fear gauge level (backend vix_regime) -> plain adjective for a sentence, and chip label. */
export const LEVEL_WORD: Record<string, string> = { LOW: "calm", NORMAL: "normal", ELEVATED: "nervous", CRISIS: "panicky" };
export const LEVEL_CHIP: Record<string, string> = { LOW: "Calm", NORMAL: "Normal", ELEVATED: "Nervous", CRISIS: "Panic" };
/** One neutral scale (grey, then deepening amber). Never the strategy colors, never terracotta. */
export const LEVEL_COLORS: Record<string, { bg: string; ink: string }> = {
  LOW: { bg: "#ECE9DE", ink: "#4A4760" },
  NORMAL: { bg: "#E9E5D6", ink: "#4A4760" },
  ELEVATED: { bg: "#F2E2BC", ink: "#5C4310" },
  CRISIS: { bg: "#E6C57E", ink: "#4A3208" },
};

export const TREND_WORD: Record<string, string> = { BULLISH: "rising", BEARISH: "falling", NEUTRAL: "flat", UNKNOWN: "unclear" };

export const PHASE_PLAIN: Record<string, string> = {
  PRE_MARKET: "Before the open",
  OPEN_VOLATILITY_FLUSH: "Opening rush",
  TREND_CONTINUATION: "Morning",
  MIDDAY_CHOP: "Midday",
  AFTERNOON_PUSH: "Afternoon",
  POWER_HOUR: "Last hour",
  EOD_FLATTEN: "Closing time",
  POST_MARKET: "After the close",
};

export function levelWord(regime: string | null | undefined): string {
  return LEVEL_WORD[(regime || "").toUpperCase()] || "unclear";
}

/** "0.7" -> "70%" (a sizing multiplier as a share of the usual amount). */
export function pctOfUsual(multiplier: number): string {
  return `${Math.round(multiplier * 100)}%`;
}

/** "11:30" -> "11:30 AM" */
export function hmLabel(hm: string | null | undefined): string {
  const m = /^(\d{1,2}):(\d{2})/.exec(hm || "");
  if (!m) return "";
  const h = Number(m[1]);
  return `${h % 12 === 0 ? 12 : h % 12}:${m[2]} ${h >= 12 ? "PM" : "AM"}`;
}

export function joinNames(names: string[]): string {
  if (names.length <= 1) return names[0] || "";
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

function strategyNames(ids: string[]): string[] {
  return ids.map((id) => STRATEGY_THEMES[id]?.name ?? id);
}

export interface MoodInputs {
  vix: number | null | undefined;
  vix_regime?: string | null;
  market_status?: string | null;
  sizing_multiplier?: number | null;
  time_multiplier?: number | null;
  market_trend?: string | null;
  adaptive_strategies?: string[] | null;
}

export interface MoodHeadline {
  tone: "grey" | "level";
  /** Show the three tiles (market open and data seen). Closed and waiting states are the headline alone. */
  tiles: boolean;
  text: string;
  /** Second line, only when the robot is trading by the mood. */
  note: string | null;
}

/** The mood card's one-sentence answer, decided in this order: closed, no data, trend unclear, normal. */
export function moodHeadline(ctx: MoodInputs, tradingDay: boolean | undefined): MoodHeadline {
  const status = (ctx.market_status || "").toUpperCase();
  if (tradingDay === false || status === "CLOSED") {
    return {
      tone: "grey",
      tiles: false,
      text: "Market closed. When it opens, the robot will size and place new trades for the market's mood.",
      note: null,
    };
  }
  const trend = ctx.market_trend ? ctx.market_trend.toUpperCase() : null;
  if (ctx.vix == null && trend == null) return { tone: "grey", tiles: false, text: "Waiting for market data.", note: null };
  const on = ctx.adaptive_strategies ?? null;
  const ownChecks = "ORB and the Tesla/Coeur plans use their own checks.";
  if (trend === "UNKNOWN") {
    const names = on ? strategyNames(on) : [];
    const who = on ? (names.length === 0 ? "No mood-sized playbook is switched on. " : `${joinNames(names)} ${names.length === 1 ? "is" : "are"} waiting until the robot can see SPY and QQQ again. `) : "The mood-sized playbooks are waiting until the robot can see SPY and QQQ again. ";
    return { tone: "grey", tiles: true, text: `${who}${ownChecks}`, note: null };
  }
  const level = levelWord(ctx.vix_regime);
  const direction = trend ? TREND_WORD[trend] ?? "unclear" : null;
  const opening = direction ? `The market is ${level} and ${direction}.` : `The market is ${level}.`;
  const names = on ? strategyNames(on) : null;
  const m = ctx.sizing_multiplier ?? null;
  let body: string;
  if (names && names.length === 0) {
    body = "No mood-sized playbook is switched on right now.";
  } else {
    const subject = names ? joinNames(names) : "The mood-sized playbooks";
    const plural = names ? names.length > 1 : true;
    const verb = plural ? "risk" : "risks";
    if (m == null) body = `${subject} size each trade for the mood.`;
    else if (m === 1) body = `${subject} ${verb} the usual amount per trade.`;
    else {
      const smaller = m < 1;
      body = `${subject} ${verb} ${pctOfUsual(m)} of the usual amount per trade; only trades with a wide safety exit end up ${smaller ? "smaller" : "bigger"}.`;
    }
    if (ctx.time_multiplier != null && ctx.time_multiplier < 1) body += " Midday halves that.";
  }
  return { tone: "level", tiles: true, text: `${opening} ${body}`, note: "ORB uses its own 9:38 market check instead. The Tesla/Coeur plans do not change with the mood." };
}

/** What the market direction means for new trades (live rules of the three mood-sized playbooks). */
export function trendEffect(trend: string | null | undefined): string {
  switch ((trend || "").toUpperCase()) {
    case "BULLISH": return "Rising: these playbooks only buy.";
    case "BEARISH": return "Falling: these playbooks only sell short.";
    case "NEUTRAL": return "Flat: Ride the Trend waits for a direction. Snap Back trades both ways.";
    case "UNKNOWN": return "SPY and QQQ can't be read, so new trades wait.";
    default: return "Direction not reported yet.";
  }
}

/** "1.4" -> "placed 40% farther away", "0.85" -> "placed 15% closer". */
function stopDistancePhrase(mult: number | null | undefined): "farther" | "closer" | "normal" | null {
  if (mult == null) return null;
  if (mult > 1.001) return "farther";
  if (mult < 0.999) return "closer";
  return "normal";
}

export interface WhyLine { label: string; value: string }

export interface AdaptiveWhy {
  sizeLine: string;
  stopLine: string | null;
  trendLine: string | null;
  rows: WhyLine[];
}

/** The one-sentence "why this size" plus the closed-by-default factor rows, honest to what was recorded. */
export function adaptiveWhy(ctx: EntryContext, shares: number, isLong: boolean): AdaptiveWhy {
  const verb = isLong ? "bought" : "sold short";
  const fin = ctx.qty_final ?? null;
  const neutral = ctx.qty_if_neutral ?? null;
  const sizing = ctx.sizing_multiplier ?? 1;
  const timeMult = ctx.time_multiplier ?? 1;
  const lead = fin != null && fin === shares ? (isLong ? "Bought" : "Sold short") : "Sized at";
  let sizeLine: string;
  if (fin == null || neutral == null) {
    sizeLine = "The robot did not keep how this trade was sized.";
  } else if (fin < neutral) {
    const parts: string[] = [];
    if (sizing < 1) parts.push(`${ctx.vix_regime === "CRISIS" ? "panicky" : "jumpy"} market (${pctOfUsual(sizing)})`);
    if (timeMult < 1) parts.push("midday (half)");
    let why = parts.join(" and ");
    if (ctx.size_limited_by === "account_limits") why = why ? `${why}, and the account's limits trimmed it` : "the account's limits trimmed it";
    sizeLine = `${lead} ${fin} shares, fewer than the usual ${neutral}${why ? `: ${why}` : ""}.`;
  } else if (fin > neutral) {
    sizeLine = `Bigger than usual: ${lead.toLowerCase()} ${fin} shares instead of ${neutral} because the market was calm (${pctOfUsual(sizing)}).`;
  } else if (ctx.size_limited_by === "notional_cap") {
    sizeLine = "Normal size. The per-trade money cap was the limit, so the market mood did not change the share count.";
  } else {
    sizeLine = "Normal size. The market mood did not change the share count.";
  }

  const dist = stopDistancePhrase(ctx.stop_multiplier);
  let stopLine: string | null = null;
  if (ctx.stop_basis === "structure") {
    stopLine = isLong ? "Safety exit placed below the dip's low." : "Safety exit placed above the bounce's high.";
  } else if (ctx.stop_basis === "floor") {
    stopLine = "Safety exit placed at the closest distance the robot allows.";
  } else if (dist === "farther") {
    stopLine = "Safety exit placed farther away because the market is jumpy.";
  } else if (dist === "closer") {
    stopLine = "Safety exit placed closer because the market is calm.";
  } else if (dist === "normal") {
    stopLine = "Safety exit placed a normal distance away.";
  }

  let trendLine: string | null = null;
  const trend = (ctx.market_trend || "").toUpperCase();
  if (ctx.trend_reason === "APPROVED_EXTREME_CATALYST") {
    trendLine = `It ${verb} against the market's direction because the news was extreme and trading was heavy.`;
  } else if (ctx.trend_reason === "APPROVED_IDIOSYNCRATIC_BREAKOUT") {
    trendLine = "It traded in a flat market because this stock was trading far more than normal.";
  } else if (trend === "BULLISH" && isLong) trendLine = "It bought because the market was rising too.";
  else if (trend === "BEARISH" && !isLong) trendLine = "It sold short because the market was falling too.";
  else if (trend === "NEUTRAL") trendLine = "The market was flat, which this playbook allows.";

  const rows: WhyLine[] = [];
  if (ctx.vix != null) {
    rows.push({
      label: "Fear gauge",
      value: `${ctx.vix.toFixed(1)}${ctx.vix_regime ? `, ${levelWord(ctx.vix_regime)}` : ""}${ctx.vix_stale ? " (reading was old)" : ""}: risks ${pctOfUsual(sizing)} of usual`,
    });
  }
  if (ctx.time_phase) {
    rows.push({ label: "Time of day", value: `${PHASE_PLAIN[ctx.time_phase] ?? ctx.time_phase}: ${timeMult < 1 ? "half size" : "full size"}` });
  }
  if (trend) {
    rows.push({ label: "Market direction", value: `${TREND_WORD[trend] ?? "unclear"}` });
  }
  if (ctx.macro) {
    rows.push({ label: "News calendar", value: ctx.macro === "MACRO_CLEAR" ? "No big news release blocking" : "Checked before buying" });
  }
  if (ctx.rs) {
    rows.push({ label: "Stronger than SPY", value: ctx.rs.day && ctx.rs.recent ? "Yes, today and the last 30 minutes" : "Not on both measures" });
  }
  return { sizeLine, stopLine, trendLine, rows };
}

/** Sentence for the "Now" line under an adaptive trade. */
export function stillHeldLine(regimeNow: string | null | undefined): string {
  const keep = "This trade keeps the size it got; its safety exit never moves farther away.";
  return regimeNow ? `Market is ${levelWord(regimeNow)} now. ${keep}` : keep;
}

export interface OrbBoxInputs {
  classification: string | null;
  short_frac: number | null;
  short_bounds: { min: number; max: number } | null;
  risk_usd: number | null;
  breakeven_r: number | null;
}

/** ORB's short box: its own 9:38 check, the risk on this trade, when its stop moves. No stop/target repeat. */
export function orbBoxText(o: OrbBoxInputs): string {
  const pct = (x: number) => `${Math.round(x * 100)}%`;
  const parts: string[] = [];
  if (o.classification === "CALM_TREND") {
    const bal = o.short_frac != null && o.short_bounds
      ? `the breakout list balanced (${pct(o.short_frac)} bets down, needs ${pct(o.short_bounds.min)} to ${pct(o.short_bounds.max)})`
      : "the breakout list balanced";
    parts.push(`Traded after its 9:38 market check: SPY and QQQ data complete and ${bal}.`);
  } else {
    parts.push("Traded after passing its own 9:38 market check (the robot did not keep the numbers).");
  }
  if (o.risk_usd != null) parts.push(`Risk on this trade: ${formatMoney(o.risk_usd)}.`);
  if (o.breakeven_r != null) {
    parts.push(`Its stop moves to its entry price at +${o.breakeven_r}x its risk, and it can close early if the trade fails fast.`);
  }
  return parts.join(" ");
}

// ---------------------------------------------------------------------------
// Overnight holds (NVDA, IREN, HUT overnight), PLAN_2026_09_30_overnight_holds.md section 5.
// Words only. Every number and state comes from the backend's overnight payload or the positions.
// ---------------------------------------------------------------------------

export const OVERNIGHT_IDS = ["overnight_nvda", "overnight_iren", "overnight_hut"];

/** A position that is an overnight hold (backend flag, or its strategy id when the flag is missing). */
export function isOvernightPosition(p: { strategy_id?: string | null; overnight?: boolean | null }): boolean {
  return p.overnight === true || OVERNIGHT_IDS.includes(String(p.strategy_id || "").toLowerCase());
}

const WEEKDAY_SHORT = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTH_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-10-01" -> "Thu Oct 1" (a calendar date, no time zone shift). Empty when unreadable. */
export function dayLabel(ymd: string | null | undefined): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(ymd || "");
  if (!m) return "";
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  return `${WEEKDAY_SHORT[d.getUTCDay()]} ${MONTH_SHORT[d.getUTCMonth()]} ${d.getUTCDate()}`;
}

/** The ET calendar date of an ISO timestamp, "YYYY-MM-DD". */
export function etDateOfIso(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : etDateKey(d);
}

/** Plain words for the backend's skip and wait reasons (overnight_schedule.py). Each is a full sentence. */
export function overnightReasonText(code: string | null | undefined): string {
  const words: Record<string, string> = {
    EARLY_CLOSE: "The market closes early today, and the rule only buys on full days.",
    NOT_TRADING_DAY: "The market is closed today.",
    CALENDAR_NOT_COVERED: "The robot's market calendar does not cover this date.",
    CALENDAR_DISAGREES: "Alpaca's market calendar did not agree with the robot's.",
    ALPACA_CALENDAR_UNAVAILABLE: "Alpaca's market calendar could not be read.",
    NO_0930_BAR: "Today's 9:30 AM price data is missing.",
    DATA_SHORT: "Too much of today's price data is missing.",
    RELAY_UNAVAILABLE: "The price data service did not answer.",
    MODE_OFF: "Overnight holds are switched off.",
    STOCK_OFF: "This stock is switched off.",
    NO_BROKER: "The robot is not connected to Alpaca.",
    OPERATOR_NO_BUY_TONIGHT: "You turned off tonight's buy.",
    EARLIER_HOLD_UNSOLD: "An earlier hold in this stock is not sold yet.",
    HELD_BY_OTHER_STRATEGY: "Another strategy holds this stock.",
    BROKER_MISMATCH: "The robot's positions do not match the Alpaca account.",
    INTENT_NOT_DURABLE: "The robot could not save its plan first.",
    DAY_TRADE_NOT_CLOSED: "A day trade in this stock could not be closed in time.",
    BROKER_NOT_FLAT: "Alpaca still showed shares or an open order in this stock.",
    ACCOUNT_UNAVAILABLE: "The Alpaca account could not be read.",
    NO_PRICE: "There was no recent price for this stock.",
    NO_ROOM: "There was not enough room in the account.",
    BUY_REFUSED: "Alpaca refused the buy.",
    WASH_TRADE_REFUSED: "Alpaca refused the buy because of another open order in this stock.",
    BUYING_POWER_REFUSED: "Alpaca said there was not enough buying power.",
    BROKER_UNREACHABLE: "Alpaca did not answer.",
    MISSED_BUY_WINDOW: "No buy could be sent by 3:49:30 PM.",
    AUCTION_NO_FILL: "The closing buy did not fill.",
    CANCELED_AT_ALPACA: "The buy was cancelled in the Alpaca app.",
  };
  return words[code || ""] || "A required check did not pass.";
}

const MOVE_TOGETHER = "IREN and HUT are both bitcoin miners and move together.";

export interface HoldLineInputs {
  symbol: string;
  shares: number;
  buyPrice: number | null;
  saleDate: string | null; // YYYY-MM-DD
  nights: string | null; // weeknight | weekend | holiday
  needsLook?: boolean;
}

/** One line per hold. Always names the sale day. */
export function holdLine(h: HoldLineInputs): string {
  const bought = h.buyPrice != null ? ` bought at ${formatMoney(h.buyPrice)} at the close` : " bought at the close";
  const day = dayLabel(h.saleDate);
  const over = h.nights === "weekend" ? "Held over the weekend, sells" : h.nights === "holiday" ? "Held over the holiday, sells" : "Sells";
  const sale = day ? `${over} at the 9:30 AM open on ${day}.` : `${over} at the next 9:30 AM open.`;
  let line = `${h.shares} shares of ${h.symbol}${bought}. No stop. ${sale}`;
  if (h.symbol === "IREN" || h.symbol === "HUT") line += ` ${MOVE_TOGETHER}`;
  if (h.needsLook) line += " The robot flagged it for a look. It still sells at the open.";
  return line;
}

export interface TonightRowLike {
  symbol: string;
  enabled: boolean;
  state: string | null;
  buy_date: string | null;
  reason: string | null;
  block: string | null;
  qty: number | null;
}

const BUY_WAITING = ["IDLE", "INTENT", "BUY_SENT"];
const HELD_STATES = ["HELD", "SALE_QUEUED", "SOLD"];

/** From 3:45 PM, what happens to one stock at the close, or why it has no buy tonight.
 * Null when the stock is bought (its hold line says the rest). `today` is the ET date, `etMin` minutes of day. */
export function tonightStatusLine(row: TonightRowLike, o: { modeOn: boolean; noBuyTonight: boolean; today: string; etMin: number }): string | null {
  if (!o.modeOn) return "No buy tonight. Overnight holds are switched off.";
  if (!row.enabled) return "No buy tonight. This stock is switched off.";
  const tonight = row.buy_date === o.today ? row : null;
  if (!tonight || !tonight.state) {
    // an earlier hold still unsold blocks tonight's buy (EARLIER_HOLD_UNSOLD)
    if (row.state && ["HELD", "SALE_QUEUED"].includes(row.state)) return `No buy tonight. ${overnightReasonText("EARLIER_HOLD_UNSOLD")}`;
    if (o.noBuyTonight) return "No buy tonight. You turned it off.";
    if (o.etMin >= 15 * 60 + 50) return "No buy tonight. No order went in by 3:49:30 PM.";
    return "Buys at the 4:00 PM close. The order goes in at 3:46 PM.";
  }
  const st = tonight.state;
  if (HELD_STATES.includes(st)) return null;
  if (st === "SKIPPED") return `No buy tonight. ${overnightReasonText(tonight.reason)}`;
  const shares = tonight.qty != null ? `${tonight.qty} shares` : "shares";
  if (st === "BUY_ACCEPTED") {
    return o.etMin < 16 * 60
      ? `Buy for ${shares} is waiting at Alpaca. It fills at the 4:00 PM close.`
      : `Waiting for Alpaca to report the closing buy of ${shares}.`;
  }
  if (BUY_WAITING.includes(st) && tonight.block) {
    return `Not bought yet. ${overnightReasonText(tonight.block)} It keeps trying until 3:49:30 PM.`;
  }
  if (st === "INTENT" || st === "BUY_SENT") return `Sending a buy for ${shares} at the 4:00 PM close.`;
  return "Checking tonight's buy.";
}

export interface NoBuyButtonInputs {
  running: boolean;
  modeOn: boolean;
  enabledCount: number;
  tradingDay: boolean;
  active: boolean; // "No overnight buy tonight" is on for today
  tooLate: boolean; // at or after 3:49:30 PM ET
  alreadyStopped: boolean; // a buy tonight was already stopped by the control
  nothingPlanned: boolean; // every switched on stock tonight is skipped or already bought
}

/** Why the "No overnight buy tonight" button (or its undo) cannot be used right now. Null when it can. */
export function noBuyDisabledReason(b: NoBuyButtonInputs): string | null {
  if (!b.running) return "The overnight holds are not running on this robot.";
  if (!b.modeOn || b.enabledCount === 0) return "Overnight holds are switched off, so there is no buy to stop.";
  if (!b.tradingDay) return "The market is closed today, so there is no buy to stop.";
  if (b.tooLate) return "Too late to change tonight. It can only be changed until 3:49:30 PM.";
  if (b.active && b.alreadyStopped) return "Tonight's buy was already stopped and cannot be restarted.";
  if (!b.active && b.nothingPlanned) return "No overnight buy is planned tonight.";
  return null;
}

/** Account card note while holds are held (they are not re-marked until they sell). */
export function overnightBalanceNote(totalAtBuyPrice: number): string {
  return `Includes ${formatMoney(totalAtBuyPrice)} in overnight holds at their buy price. Their real value is known at 9:30 AM.`;
}

/** Red banner while any hold is still unsold after 9:31 AM. */
export function unsoldBannerText(symbols: string[]): string {
  const names = joinNames(symbols.map((s) => `${s} overnight`));
  return symbols.length === 1
    ? `${names} is not sold yet after 9:31 AM. The robot keeps trying to sell it. Check the Alpaca app.`
    : `${names} are not sold yet after 9:31 AM. The robot keeps trying to sell them. Check the Alpaca app.`;
}

/** The "Right now" sentence part for holds. `saleDate` is the earliest sale day, `pastSale` true once its 9:30 AM passed. */
export function overnightHoldingSentence(count: number, saleDate: string | null, pastSale: boolean): string | null {
  if (count <= 0) return null;
  const what = `${count} overnight ${count === 1 ? "stock" : "stocks"}`;
  if (pastSale) return `Selling ${what} bought at the last close.`;
  const day = dayLabel(saleDate);
  return `Holding ${what} until the 9:30 AM open${day ? ` on ${day}` : ""}.`;
}
